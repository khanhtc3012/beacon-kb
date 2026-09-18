"""Entrypoint: scrape -> convert -> diff -> upload delta -> summary. Runs once, then exits."""
import logging
import os
import sys

from dotenv import load_dotenv

log = logging.getLogger("beacon-kb")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )
    load_dotenv()

    if not os.environ.get("OPENAI_API_KEY"):
        log.error("OPENAI_API_KEY is not set")
        return 1

    # Wired up in steps 3-7: zendesk.fetch_articles -> convert -> delta.classify -> vectorstore.
    log.error("pipeline not implemented yet")
    return 1


if __name__ == "__main__":
    sys.exit(main())
