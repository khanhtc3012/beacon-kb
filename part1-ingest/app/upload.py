"""Scrape the help center and upload the articles to a vector store (a one-shot loader, no delta yet)."""
import argparse
import logging
import os
import sys

from dotenv import load_dotenv
from openai import OpenAI

from app.scrape import scrape
from app.vectorstore import (
    CHUNK_OVERLAP_TOKENS,
    MAX_CHUNK_TOKENS,
    Chunking,
    count_tokens,
    delete_store,
    estimate_chunks,
    get_or_create_store,
    upload_docs,
)

log = logging.getLogger("beacon-kb")


def _find_store(client, name: str):
    return next((store for store in client.vector_stores.list(limit=100) if store.name == name), None)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one INFO line per HTTP call, with ids in the URL
    load_dotenv()
    parser = argparse.ArgumentParser(description="Upload help-center articles to an OpenAI vector store.")
    parser.add_argument("--limit", type=int, default=None, help="only the first N articles")
    parser.add_argument("--ids", default="", help="only these article ids (comma-separated)")
    parser.add_argument("--store-name", default=os.environ.get("VECTOR_STORE_NAME", "support-kb"))
    parser.add_argument("--max-tokens", type=int, default=MAX_CHUNK_TOKENS, help="max tokens per chunk")
    parser.add_argument("--overlap", type=int, default=CHUNK_OVERLAP_TOKENS, help="tokens shared by neighbouring chunks")
    parser.add_argument("--dry-run", action="store_true", help="scrape and estimate only; no OpenAI calls")
    parser.add_argument("--force", action="store_true", help="upload even if the store already has files")
    parser.add_argument("--delete-store", action="store_true", help="delete the named store and its files, then exit")
    args = parser.parse_args(argv)

    chunking = Chunking(args.max_tokens, args.overlap)
    base_url = os.environ.get("HC_BASE_URL")
    locale = os.environ.get("HC_LOCALE", "en-us")
    if not base_url:
        log.error("HC_BASE_URL is not set")
        return 1

    wanted = {i.strip() for i in args.ids.split(",") if i.strip()}

    def load_docs():
        docs = list(scrape(base_url, locale, limit=None if wanted else args.limit))
        return [d for d in docs if d.article_id in wanted] if wanted else docs

    if args.dry_run:
        docs = load_docs()
        tokens = sum(count_tokens(doc.markdown) for doc in docs)
        chunks = sum(estimate_chunks(count_tokens(doc.markdown), chunking) for doc in docs)
        log.info("[dry-run] files=%d tokens=%d est_chunks=~%d (max=%d overlap=%d)",
                 len(docs), tokens, chunks, chunking.max_tokens, chunking.overlap_tokens)
        return 0

    if not os.environ.get("OPENAI_API_KEY"):
        log.error("OPENAI_API_KEY is not set")
        return 1
    client = OpenAI()

    if args.delete_store:
        store = _find_store(client, args.store_name)
        if store is None:
            log.error("no vector store named %r", args.store_name)
            return 1
        removed = delete_store(client, store.id)
        log.info("[delete] store=%r files_deleted=%d", args.store_name, removed)
        return 0

    store_id = get_or_create_store(client, args.store_name)
    existing = client.vector_stores.retrieve(store_id).file_counts.total
    if existing and not args.force:
        log.error("store %r already has %d files; uploading again would duplicate them (use --force)",
                  args.store_name, existing)
        return 1

    docs = load_docs()
    result = upload_docs(client, store_id, docs, chunking)
    log.info("[upload] files=%d completed=%d failed=%d est_chunks=~%d (max=%d overlap=%d)",
             result.files, result.completed, result.failed, result.est_chunks,
             chunking.max_tokens, chunking.overlap_tokens)
    for name, reason in result.failures:
        log.warning("failed: %s: %s", name, reason)
    counts = client.vector_stores.retrieve(store_id).file_counts
    log.info("[store] name=%r id=...%s total=%d completed=%d failed=%d in_progress=%d",
             args.store_name, store_id[-6:], counts.total, counts.completed, counts.failed, counts.in_progress)
    return 0 if result.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
