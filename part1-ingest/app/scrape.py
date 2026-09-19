"""Scrape the help center into Markdown files named `<id>-<slug>.md`."""
import argparse
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Optional

from dotenv import load_dotenv

from app.convert import filename, to_markdown
from app.zendesk import fetch_articles

log = logging.getLogger("beacon-kb")


@dataclass
class Doc:
    article_id: str
    title: str
    url: str
    updated_at: str
    filename: str
    markdown: str


def scrape(base_url: str, locale: str, limit: Optional[int] = None, session=None) -> Iterator[Doc]:
    for article in fetch_articles(base_url, locale, limit=limit, session=session):
        yield Doc(
            article_id=str(article["id"]),
            title=(article.get("title") or "").strip(),
            url=article["html_url"],
            updated_at=article.get("updated_at") or "",
            filename=filename(article),
            markdown=to_markdown(article, base_url),
        )


def write_docs(docs: Iterable[Doc], out_dir: Path) -> int:
    """Write each doc as UTF-8 with LF endings; drop an older file of the same article if its title changed."""
    out_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for doc in docs:
        for old in out_dir.glob(f"{doc.article_id}-*.md"):
            if old.name != doc.filename:
                old.unlink()
        (out_dir / doc.filename).write_text(doc.markdown, encoding="utf-8", newline="\n")
        count += 1
    return count


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    load_dotenv()
    parser = argparse.ArgumentParser(description="Scrape help-center articles to Markdown files.")
    parser.add_argument("--out", default="out", help="output directory (default: out)")
    parser.add_argument("--limit", type=int, default=int(os.environ.get("ARTICLE_LIMIT") or 0) or None)
    args = parser.parse_args(argv)

    base_url = os.environ.get("HC_BASE_URL")
    locale = os.environ.get("HC_LOCALE", "en-us")
    if not base_url:
        log.error("HC_BASE_URL is not set")
        return 1
    written = write_docs(scrape(base_url, locale, limit=args.limit), Path(args.out))
    log.info("[scrape] written=%d dir=%s", written, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
