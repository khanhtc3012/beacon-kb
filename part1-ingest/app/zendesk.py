"""Read articles from the Zendesk Help Center API (paginated, no auth)."""
import logging
import time
from typing import Iterator, Optional

import requests

log = logging.getLogger("beacon-kb")

PER_PAGE = 100
TIMEOUT = 30
MAX_ATTEMPTS = 5
RETRY_STATUSES = {429, 500, 502, 503, 504}


def _get_json(session, url: str) -> dict:
    """GET `url`, retrying 429/5xx and network errors with backoff (honours Retry-After)."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        wait = 2 ** attempt
        try:
            resp = session.get(url, timeout=TIMEOUT)
        except requests.RequestException as exc:
            if attempt == MAX_ATTEMPTS:
                raise
            log.warning("request failed (%s), retry %d/%d", exc, attempt, MAX_ATTEMPTS)
        else:
            if resp.status_code not in RETRY_STATUSES:
                resp.raise_for_status()
                return resp.json()
            if attempt == MAX_ATTEMPTS:
                resp.raise_for_status()
            retry_after = resp.headers.get("Retry-After", "")
            if retry_after.isdigit():
                wait = int(retry_after)
            log.warning("HTTP %d, retry %d/%d in %ds", resp.status_code, attempt, MAX_ATTEMPTS, wait)
        time.sleep(wait)
    raise RuntimeError("unreachable")


def fetch_articles(
    base_url: str,
    locale: str,
    limit: Optional[int] = None,
    session=None,
) -> Iterator[dict]:
    """Yield published articles, following `next_page` until exhausted.

    Skips drafts and articles with an empty body. Stops after `limit` yielded articles.
    """
    session = session or requests.Session()
    url = f"{base_url.rstrip('/')}/api/v2/help_center/{locale}/articles.json?per_page={PER_PAGE}"
    yielded = drafts = empty = 0
    try:
        while url:
            page = _get_json(session, url)
            for article in page.get("articles", []):
                if article.get("draft"):
                    drafts += 1
                    continue
                if not (article.get("body") or "").strip():
                    empty += 1
                    continue
                yield article
                yielded += 1
                if limit and yielded >= limit:
                    return
            url = page.get("next_page")
    finally:
        log.info("[scrape] fetched=%d skipped_draft=%d skipped_empty=%d", yielded, drafts, empty)
