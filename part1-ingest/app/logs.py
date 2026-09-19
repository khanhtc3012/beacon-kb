"""Logging setup shared by every entrypoint."""
import logging
import sys

# The OpenAI SDK logs every HTTP call at INFO, with vector store and file ids in the URL. The logger is named
# after whichever HTTP package the SDK uses (httpx2 in openai 3.x), so silence the whole family.
HTTP_LOGGERS = ("httpx", "httpx2", "httpcore", "httpcore2")


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    for name in HTTP_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
