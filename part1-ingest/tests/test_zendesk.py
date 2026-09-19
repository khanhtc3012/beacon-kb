import pytest
import requests

from app import zendesk

BASE = "https://help.example.com"


class FakeResponse:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code = status
        self._payload = payload or {}
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []

    def get(self, url, timeout=None):
        self.urls.append(url)
        return self.responses.pop(0)


def article(n, **extra):
    return {"id": n, "title": f"A{n}", "html_url": f"{BASE}/{n}", "body": f"<p>{n}</p>", **extra}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(zendesk.time, "sleep", slept.append)
    return slept


def test_follows_next_page_and_skips_drafts_and_empty_bodies():
    page1 = {"articles": [article(1), article(2, draft=True), article(3, body="  ")], "next_page": f"{BASE}/p2"}
    page2 = {"articles": [article(4)], "next_page": None}
    session = FakeSession([FakeResponse(payload=page1), FakeResponse(payload=page2)])
    got = [a["id"] for a in zendesk.fetch_articles(BASE, "en-us", session=session)]
    assert got == [1, 4]
    assert session.urls[0].startswith(f"{BASE}/api/v2/help_center/en-us/articles.json")
    assert session.urls[1] == f"{BASE}/p2"


def test_limit_stops_early():
    page = {"articles": [article(i) for i in range(1, 6)], "next_page": f"{BASE}/p2"}
    session = FakeSession([FakeResponse(payload=page)])
    got = list(zendesk.fetch_articles(BASE, "en-us", limit=2, session=session))
    assert [a["id"] for a in got] == [1, 2]
    assert len(session.urls) == 1


def test_retries_429_and_honours_retry_after(no_sleep):
    page = {"articles": [article(1)], "next_page": None}
    session = FakeSession([FakeResponse(429, headers={"Retry-After": "7"}), FakeResponse(payload=page)])
    assert [a["id"] for a in zendesk.fetch_articles(BASE, "en-us", session=session)] == [1]
    assert no_sleep == [7]


def test_gives_up_after_max_attempts():
    session = FakeSession([FakeResponse(503)] * zendesk.MAX_ATTEMPTS)
    with pytest.raises(requests.HTTPError):
        list(zendesk.fetch_articles(BASE, "en-us", session=session))


def test_client_error_is_not_retried():
    session = FakeSession([FakeResponse(404)])
    with pytest.raises(requests.HTTPError):
        list(zendesk.fetch_articles(BASE, "en-us", session=session))
    assert len(session.urls) == 1
