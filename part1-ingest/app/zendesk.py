"""Read articles from the Zendesk Help Center API (paginated, no auth)."""
from typing import Iterator, Optional


def fetch_articles(base_url: str, locale: str, limit: Optional[int] = None) -> Iterator[dict]:
    """Yield published articles, following `next_page` until exhausted.

    Skips drafts and articles with an empty body. Retries 429/5xx, honouring Retry-After.
    """
    raise NotImplementedError("step 3")
