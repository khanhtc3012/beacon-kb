import logging

import httpx2
import openai

from app.logs import configure_logging


def test_openai_http_calls_are_not_logged(caplog):
    """Each call would log its URL, which carries vector store and file ids; the job log is public."""
    configure_logging()
    caplog.set_level(logging.INFO)
    transport = httpx2.MockTransport(lambda request: httpx2.Response(200, json={"object": "list", "data": []}))
    client = openai.OpenAI(api_key="test", http_client=httpx2.Client(transport=transport))
    list(client.vector_stores.list(limit=1))
    assert not [r for r in caplog.records if "HTTP Request" in r.getMessage()]
