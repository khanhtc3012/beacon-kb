"""Bring the vector store in line with the help center: upload what is new or changed, drop what is gone."""
import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

import openai
from dotenv import load_dotenv
from openai import OpenAI

from app.delta import Delta, classify
from app.scrape import scrape
from app.vectorstore import (
    CHUNK_OVERLAP_TOKENS,
    MAX_CHUNK_TOKENS,
    Chunking,
    Remote,
    RemoteFile,
    count_tokens,
    delete_file,
    delete_store,
    estimate_chunks,
    find_store,
    fingerprint,
    get_or_create_store,
    list_remote,
    upload_docs,
)

log = logging.getLogger("beacon-kb")

REMOVAL_MIN_RATIO = 0.5  # scraping less than this share of the store's articles looks like a glitch, not deletions


@dataclass
class SyncResult:
    added: int = 0
    updated: int = 0
    skipped: int = 0
    removed: int = 0
    failed: int = 0
    est_chunks: int = 0
    failures: List[Tuple[str, str]] = field(default_factory=list)  # (article id, reason)
    problems: List[str] = field(default_factory=list)  # things that should fail the run without being per-article

    @property
    def ok(self) -> bool:
        return self.failed == 0 and not self.problems


def plan_sync(
    docs: Sequence, remote_files: Dict[str, RemoteFile], chunking: Chunking, remove_missing: bool
) -> Tuple[Delta, List[str]]:
    """What a sync would do, without touching the store. Returns the delta and any problems found."""
    scraped = {doc.article_id: fingerprint(doc.markdown, chunking) for doc in docs}
    stored = {article_id: f.content_hash for article_id, f in remote_files.items()}
    problems = []
    if remove_missing and stored and len(scraped) < REMOVAL_MIN_RATIO * len(stored):
        problems.append(f"scraped {len(scraped)} articles but the store has {len(stored)}: not removing anything")
        remove_missing = False
    return classify(scraped, stored, remove_missing), problems


def sync(client, store_id: str, docs: Sequence, chunking: Chunking, remove_missing: bool = False) -> SyncResult:
    """Upload added and updated articles, then remove old versions and articles that are gone.

    An updated article is uploaded before its old file is deleted, so a failed upload never loses it.
    """
    result = SyncResult()
    remote = list_remote(client, store_id)
    for file_id in remote.junk:
        delete_file(client, store_id, file_id)
    if remote.junk:
        log.info("deleted %d failed or duplicate files", len(remote.junk))

    delta, result.problems = plan_sync(docs, remote.files, chunking, remove_missing)
    by_id = {doc.article_id: doc for doc in docs}
    upload = upload_docs(client, store_id, [by_id[i] for i in delta.added + delta.updated], chunking)
    failed_ids = {article_id for article_id, _ in upload.failures}
    result.failures = list(upload.failures)
    result.est_chunks = upload.est_chunks
    result.added = sum(1 for i in delta.added if i not in failed_ids)
    result.skipped = len(delta.skipped)

    stale = [(i, "updated") for i in delta.updated if i not in failed_ids] + [(i, "removed") for i in delta.removed]
    for article_id, kind in stale:
        try:
            delete_file(client, store_id, remote.files[article_id].file_id)
        except openai.OpenAIError as exc:
            result.failures.append((article_id, f"could not delete old file: {exc}"))
            continue
        setattr(result, kind, getattr(result, kind) + 1)
    result.failed = len(result.failures)
    return result


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one INFO line per HTTP call, with ids in the URL
    load_dotenv()
    parser = argparse.ArgumentParser(description="Sync help-center articles into an OpenAI vector store.")
    parser.add_argument("--limit", type=int, default=None, help="only the first N articles (never removes)")
    parser.add_argument("--ids", default="", help="only these article ids, comma-separated (never removes)")
    parser.add_argument("--store-name", default=os.environ.get("VECTOR_STORE_NAME", "support-kb"))
    parser.add_argument("--max-tokens", type=int, default=MAX_CHUNK_TOKENS, help="max tokens per chunk")
    parser.add_argument("--overlap", type=int, default=CHUNK_OVERLAP_TOKENS, help="tokens shared by neighbouring chunks")
    parser.add_argument("--dry-run", action="store_true", help="report what would change; write nothing")
    parser.add_argument("--delete-store", action="store_true", help="delete the named store and its files, then exit")
    args = parser.parse_args(argv)

    chunking = Chunking(args.max_tokens, args.overlap)
    base_url = os.environ.get("HC_BASE_URL")
    locale = os.environ.get("HC_LOCALE", "en-us")
    if not base_url:
        log.error("HC_BASE_URL is not set")
        return 1
    if not os.environ.get("OPENAI_API_KEY"):
        log.error("OPENAI_API_KEY is not set")
        return 1
    client = OpenAI()

    if args.delete_store:
        store = find_store(client, args.store_name)
        if store is None:
            log.error("no vector store named %r", args.store_name)
            return 1
        log.info("[delete] store=%r files_deleted=%d", args.store_name, delete_store(client, store.id))
        return 0

    started = time.monotonic()
    wanted = {i.strip() for i in args.ids.split(",") if i.strip()}
    docs = list(scrape(base_url, locale, limit=None if wanted else args.limit))
    if wanted:
        docs = [doc for doc in docs if doc.article_id in wanted]
    remove_missing = not (wanted or args.limit)

    if args.dry_run:
        store = find_store(client, args.store_name)
        remote = list_remote(client, store.id) if store else Remote()
        delta, problems = plan_sync(docs, remote.files, chunking, remove_missing)
        by_id = {doc.article_id: doc for doc in docs}
        chunks = sum(estimate_chunks(count_tokens(by_id[i].markdown), chunking) for i in delta.added + delta.updated)
        log.info("[dry-run] added=%d updated=%d skipped=%d removed=%d est_chunks=~%d",
                 len(delta.added), len(delta.updated), len(delta.skipped), len(delta.removed), chunks)
        for problem in problems:
            log.error("%s", problem)
        return 0 if not problems else 1

    result = sync(client, get_or_create_store(client, args.store_name), docs, chunking, remove_missing)
    log.info("[sync] added=%d updated=%d skipped=%d removed=%d failed=%d duration=%ds",
             result.added, result.updated, result.skipped, result.removed, result.failed,
             time.monotonic() - started)
    for article_id, reason in result.failures:
        log.warning("failed: %s: %s", article_id, reason)
    for problem in result.problems:
        log.error("%s", problem)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
