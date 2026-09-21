"""Entrypoint: scrape -> convert -> diff -> upload delta -> summary. Runs once, then exits.

Configuration is read from the environment; only OPENAI_API_KEY is required.
Exit code 0 means every article is in sync. Anything else (bad config, a failed article,
an API or network error) exits 1, so a scheduler can tell a bad run from a good one.

If LOG_BUCKET is set, the run also rewrites a status page in that S3 bucket when it ends
(see app/report.py). A failure to publish that page is logged and never changes the exit code.
"""
import logging
import os
import sys
import time
from typing import Optional, Tuple

import openai
from dotenv import load_dotenv
from openai import OpenAI

from app import report
from app.logs import configure_logging
from app.scrape import scrape
from app.sync import SyncResult, log_result, sync
from app.vectorstore import CHUNK_OVERLAP_TOKENS, MAX_CHUNK_TOKENS, Chunking, get_or_create_store

log = logging.getLogger("beacon-kb")

DEFAULT_BASE_URL = "https://support.optisigns.com"
DEFAULT_LOCALE = "en-us"
DEFAULT_STORE = "support-kb"


def _sync_job(env, client, scrape_fn) -> Tuple[int, Optional[SyncResult]]:
    started = time.monotonic()
    api_key = env.get("OPENAI_API_KEY")
    if not api_key:
        log.error("OPENAI_API_KEY is not set")
        return 1, None
    try:
        limit = int(env.get("ARTICLE_LIMIT") or 0) or None
        chunking = Chunking(
            int(env.get("CHUNK_MAX_TOKENS") or MAX_CHUNK_TOKENS),
            int(env.get("CHUNK_OVERLAP_TOKENS") or CHUNK_OVERLAP_TOKENS),
        )
    except ValueError as exc:
        log.error("invalid configuration: %s", exc)
        return 1, None
    base_url = env.get("HC_BASE_URL") or DEFAULT_BASE_URL
    locale = env.get("HC_LOCALE") or DEFAULT_LOCALE
    store_name = env.get("VECTOR_STORE_NAME") or DEFAULT_STORE

    log.info("[job] start store=%s locale=%s limit=%s chunking=%d/%d",
             store_name, locale, limit or "none", chunking.max_tokens, chunking.overlap_tokens)
    try:
        docs = list(scrape_fn(base_url, locale, limit=limit))
        if not docs:
            log.error("no articles were scraped, refusing to sync")
            return 1, None
        client = client or OpenAI(api_key=api_key)
        store_id = get_or_create_store(client, store_name)
        result = sync(client, store_id, docs, chunking, remove_missing=limit is None)
        counts = client.vector_stores.retrieve(store_id).file_counts
    except Exception as exc:  # last line of defence: report any failure and exit non-zero
        # the 401 message echoes a masked copy of the key, and OpenAI errors are self-explanatory without a traceback
        detail = "the API key was rejected" if isinstance(exc, openai.AuthenticationError) else exc
        log.error("job failed: %s: %s", type(exc).__name__, detail, exc_info=not isinstance(exc, openai.OpenAIError))
        return 1, None

    log_result(result, time.monotonic() - started)
    log.info("[embedded] files=%d est_chunks=~%d (max=%d overlap=%d)",
             result.added + result.updated, result.est_chunks, chunking.max_tokens, chunking.overlap_tokens)
    log.info("[store] name=%s total_files=%d completed=%d failed=%d in_progress=%d",
             store_name, counts.total, counts.completed, counts.failed, counts.in_progress)
    return (0 if result.ok else 1), result


class _LogCapture(logging.Handler):
    """Keeps this run's log lines, in the same format as the console, for the status page."""

    def __init__(self):
        super().__init__(logging.INFO)
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        self.lines = []

    def emit(self, record):
        self.lines.append(self.format(record))


def run(env, client=None, scrape_fn=scrape, s3=None) -> int:
    started_at, timer = report.utc_now(), time.monotonic()
    capture = _LogCapture()
    log.addHandler(capture)
    try:
        code, result = _sync_job(env, client, scrape_fn)
    finally:
        log.removeHandler(capture)

    bucket = env.get("LOG_BUCKET")
    if bucket:
        counts = {k: getattr(result, k) for k in ("added", "updated", "skipped", "removed", "failed")} if result else {}
        record = report.RunRecord(started_at, int(time.monotonic() - timer), code, **counts)
        try:
            report.publish_run(s3 or report.make_s3_client(), bucket, env.get("LOG_PREFIX", "public"), record,
                               "\n".join(capture.lines) + "\n", env.get("REPO_URL", ""))
            log.info("[report] status page updated in bucket %s", bucket)
        except Exception as exc:  # the page is a convenience: the sync result and exit code stand on their own
            log.warning("could not publish the status page: %s: %s", type(exc).__name__, exc)
    return code


def main() -> int:
    configure_logging()
    load_dotenv()
    return run(os.environ)


if __name__ == "__main__":
    sys.exit(main())
