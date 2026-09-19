import logging
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import openai
import pytest
from fakes import FakeOpenAI, doc

import main

KEY = "fake-api-key-for-tests-0123456789"


def scraper(docs, calls=None):
    def scrape(base_url, locale, limit=None):
        if calls is not None:
            calls.append((base_url, locale, limit))
        return iter(docs[:limit] if limit else docs)
    return scrape


def run(env, docs, client=None, calls=None):
    return main.run({"OPENAI_API_KEY": KEY, **env}, client=client or FakeOpenAI(), scrape_fn=scraper(docs, calls))


@pytest.fixture(autouse=True)
def capture(caplog):
    caplog.set_level(logging.INFO, logger="beacon-kb")
    return caplog


def messages(caplog):
    return [r.getMessage() for r in caplog.records]


def test_a_run_adds_everything_then_a_second_run_skips_everything_and_both_exit_zero(caplog):
    client, docs = FakeOpenAI(), [doc(1), doc(2), doc(3)]
    assert run({}, docs, client) == 0
    assert any("[sync] added=3 updated=0 skipped=0 removed=0 failed=0" in m for m in messages(caplog))
    caplog.clear()
    assert run({}, docs, client) == 0
    assert any("[sync] added=0 updated=0 skipped=3 removed=0 failed=0" in m for m in messages(caplog))


def test_the_summary_reports_files_chunks_and_store_totals(caplog):
    assert run({}, [doc(1), doc(2)]) == 0
    text = "\n".join(messages(caplog))
    assert "[embedded] files=2 est_chunks=~2 (max=800 overlap=200)" in text
    assert "[store] name=support-kb total_files=2 completed=2 failed=0 in_progress=0" in text


def test_only_the_api_key_is_required_and_the_others_have_defaults():
    calls = []
    assert run({}, [doc(1)], calls=calls) == 0
    assert calls == [("https://support.optisigns.com", "en-us", None)]


def test_settings_come_from_the_environment(caplog):
    calls = []
    env = {"HC_BASE_URL": "https://help.example.com", "HC_LOCALE": "fr", "VECTOR_STORE_NAME": "kb", "ARTICLE_LIMIT": "2"}
    assert run(env, [doc(1), doc(2), doc(3)], calls=calls) == 0
    assert calls == [("https://help.example.com", "fr", 2)]
    assert any("store=kb" in m and "limit=2" in m for m in messages(caplog))


def test_missing_api_key_exits_one_without_scraping(caplog):
    calls = []
    assert main.run({}, client=FakeOpenAI(), scrape_fn=scraper([doc(1)], calls)) == 1
    assert calls == []
    assert "OPENAI_API_KEY is not set" in messages(caplog)


@pytest.mark.parametrize("env", [
    {"ARTICLE_LIMIT": "many"},
    {"CHUNK_MAX_TOKENS": "50"},
    {"CHUNK_MAX_TOKENS": "800", "CHUNK_OVERLAP_TOKENS": "500"},
])
def test_invalid_configuration_exits_one(env, caplog):
    assert run(env, [doc(1)]) == 1
    assert any(m.startswith("invalid configuration") for m in messages(caplog))


def test_no_articles_scraped_exits_one_and_touches_nothing(caplog):
    client = FakeOpenAI()
    assert run({}, [], client) == 1
    assert client.attached == {} and client.raw == {}
    assert "no articles were scraped, refusing to sync" in messages(caplog)


def test_a_scrape_crash_is_reported_and_exits_one(caplog):
    def boom(*args, **kwargs):
        raise ConnectionError("help center unreachable")
    assert main.run({"OPENAI_API_KEY": KEY}, client=FakeOpenAI(), scrape_fn=boom) == 1
    assert any("job failed: ConnectionError: help center unreachable" in m for m in messages(caplog))


def test_out_of_credit_is_reported_and_exits_one(caplog):
    err = openai.RateLimitError("no credits", response=MagicMock(status_code=429, headers={}),
                                body={"type": "insufficient_quota", "code": "credit_balance_exhausted"})
    client = FakeOpenAI()
    client.files = NS(create=MagicMock(side_effect=err), delete=lambda file_id: None)
    assert run({}, [doc(1), doc(2)], client) == 1
    assert any("job failed: RateLimitError" in m for m in messages(caplog))


def test_a_rejected_key_is_reported_without_echoing_the_masked_key_or_a_traceback(caplog):
    err = openai.AuthenticationError("Incorrect API key provided: sk-tes*****alue", response=MagicMock(status_code=401, headers={}), body={})
    client = FakeOpenAI()
    client.vector_stores.list = MagicMock(side_effect=err)
    assert run({}, [doc(1)], client) == 1
    failed = [r for r in caplog.records if r.getMessage().startswith("job failed")]
    assert [r.getMessage() for r in failed] == ["job failed: AuthenticationError: the API key was rejected"]
    assert not failed[0].exc_info
    assert "sk-tes" not in caplog.text


def test_an_unexpected_crash_keeps_its_traceback(caplog):
    def boom(*args, **kwargs):
        raise ConnectionError("help center unreachable")
    main.run({"OPENAI_API_KEY": KEY}, client=FakeOpenAI(), scrape_fn=boom)
    failed = [r for r in caplog.records if r.getMessage().startswith("job failed")]
    assert failed[0].exc_info is not None


def test_one_bad_article_does_not_stop_the_others_but_the_run_exits_one(caplog):
    client = FakeOpenAI(fail_names={"2-t.md"})
    assert run({}, [doc(1), doc(2), doc(3)], client) == 1
    assert client.article_ids() == ["1", "2", "3"]  # the failed file stays in the store until the next run cleans it
    completed = {v["attributes"]["article_id"] for v in client.attached.values() if v["status"] == "completed"}
    assert completed == {"1", "3"}
    assert any(m.startswith("failed: 2:") for m in messages(caplog))
    assert any("[sync] added=2 updated=0 skipped=0 removed=0 failed=1" in m for m in messages(caplog))


def test_a_limited_run_never_removes_articles():
    client = FakeOpenAI()
    run({}, [doc(1), doc(2), doc(3)], client)
    assert run({"ARTICLE_LIMIT": "1"}, [doc(1), doc(2), doc(3)], client) == 0
    assert client.article_ids() == ["1", "2", "3"]


def test_a_full_run_removes_articles_that_are_gone():
    client = FakeOpenAI()
    run({}, [doc(1), doc(2), doc(3)], client)
    assert run({}, [doc(1), doc(2)], client) == 0
    assert client.article_ids() == ["1", "2"]


def test_the_api_key_never_appears_in_the_logs(caplog):
    run({}, [doc(1)])
    run({"ARTICLE_LIMIT": "x"}, [doc(1)])
    assert KEY not in "\n".join(messages(caplog))
    assert KEY not in caplog.text
