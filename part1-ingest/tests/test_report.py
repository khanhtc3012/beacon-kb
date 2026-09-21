import io
import json
import logging

import pytest
from fakes import FakeOpenAI, doc

import main
from app import report

KEY = "fake-api-key-for-tests-0123456789"


class MissingKey(Exception):
    response = {"Error": {"Code": "NoSuchKey"}}


class Denied(Exception):
    response = {"Error": {"Code": "AccessDenied"}}


class FakeS3:
    """The two S3 calls the report uses, with the objects kept in a dict."""

    def __init__(self, deny_reads=False, fail_puts=False):
        self.objects, self.puts = {}, []
        self.deny_reads, self.fail_puts = deny_reads, fail_puts

    def get_object(self, Bucket, Key):
        if self.deny_reads:
            raise Denied("no")
        if (Bucket, Key) not in self.objects:
            raise MissingKey("gone")
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def put_object(self, Bucket, Key, Body, ContentType, CacheControl):
        if self.fail_puts:
            raise RuntimeError("s3 is down")
        self.objects[(Bucket, Key)] = Body
        self.puts.append((Key, ContentType, CacheControl))

    def text(self, key, bucket="b"):
        return self.objects[(bucket, key)].decode("utf-8")


def record(exit_code=0, started="2026-09-21T02:00:03Z", **counts):
    return report.RunRecord(started, 25, exit_code, **counts)


@pytest.fixture(autouse=True)
def capture(caplog):
    caplog.set_level(logging.INFO, logger="beacon-kb")
    return caplog


def scraper(docs):
    return lambda base_url, locale, limit=None: iter(docs)


def run_job(env, docs, s3, client=None):
    return main.run({"OPENAI_API_KEY": KEY, **env}, client=client or FakeOpenAI(), scrape_fn=scraper(docs), s3=s3)


def test_first_publish_writes_the_three_files_under_the_prefix():
    s3 = FakeS3()
    report.publish_run(s3, "b", "public", record(added=30), "log line\n", "https://github.com/x/y")
    assert sorted(k for k, _, _ in s3.puts) == ["public/index.html", "public/last_run.log", "public/runs.json"]
    types = {k: t for k, t, _ in s3.puts}
    assert types["public/index.html"] == "text/html; charset=utf-8"
    assert types["public/last_run.log"] == "text/plain; charset=utf-8"
    assert types["public/runs.json"] == "application/json"
    assert {c for _, _, c in s3.puts} == {"no-cache"}
    assert json.loads(s3.text("public/runs.json")) == [json.loads(json.dumps(report.asdict(record(added=30))))]
    assert "https://github.com/x/y" in s3.text("public/index.html")


def test_next_publish_overwrites_the_same_keys_and_keeps_the_history():
    s3 = FakeS3()
    report.publish_run(s3, "b", "public", record(started="2026-09-20T02:00:00Z", added=30), "first\n")
    keys_after_first = set(s3.objects)
    report.publish_run(s3, "b", "public", record(started="2026-09-21T02:00:00Z", skipped=30), "second\n")
    assert set(s3.objects) == keys_after_first  # same addresses, nothing new
    assert s3.text("public/last_run.log") == "second\n"
    runs = json.loads(s3.text("public/runs.json"))
    assert [r["started"] for r in runs] == ["2026-09-20T02:00:00Z", "2026-09-21T02:00:00Z"]
    page = s3.text("public/index.html")
    assert page.index("2026-09-21T02:00:00Z") < page.index("2026-09-20T02:00:00Z")  # newest first
    assert "second" in page and "first" not in page.split("<pre>")[1]  # the log shown is the latest run's


def test_history_is_capped():
    s3 = FakeS3()
    for n in range(report.HISTORY + 5):
        report.publish_run(s3, "b", "public", record(started=f"run-{n:03d}"), "x\n")
    runs = json.loads(s3.text("public/runs.json"))
    assert len(runs) == report.HISTORY
    assert runs[-1]["started"] == f"run-{report.HISTORY + 4:03d}"
    assert runs[0]["started"] == "run-005"


def test_unreadable_history_starts_again(caplog):
    s3 = FakeS3()
    s3.objects[("b", "public/runs.json")] = b"not json"
    report.publish_run(s3, "b", "public", record(), "x\n")
    assert len(json.loads(s3.text("public/runs.json"))) == 1
    assert "could not be read" in caplog.text


def test_a_read_error_other_than_missing_file_is_not_hidden():
    with pytest.raises(Denied):
        report.publish_run(FakeS3(deny_reads=True), "b", "public", record(), "x\n")


@pytest.mark.parametrize("secret", [
    "sk-abcdefghijklmnopqrstuvwx", "AKIAABCDEFGHIJKLMNOP", "arn:aws:iam::1:role/x",
    "vs_6aae3176a63481919e49d069bd891a8c", "file-AbCdEfGhIjKlMnOpQrSt",
])
def test_a_log_that_looks_sensitive_is_not_published(secret):
    s3 = FakeS3()
    with pytest.raises(ValueError):
        report.publish_run(s3, "b", "public", record(), f"ok\nsomething {secret}\n")
    assert s3.puts == []


def test_the_page_escapes_the_log():
    s3 = FakeS3()
    report.publish_run(s3, "b", "public", record(started="<b>x</b>"), "<script>alert(1)</script> & done\n")
    page = s3.text("public/index.html")
    assert "<script>alert" not in page and "&lt;script&gt;alert(1)&lt;/script&gt; &amp; done" in page
    assert "<b>x</b>" not in page


def test_the_page_says_failed_when_the_latest_run_failed():
    s3 = FakeS3()
    report.publish_run(s3, "b", "public", record(exit_code=1, failed=2), "x\n")
    assert "Latest run: <b>failed</b>" in s3.text("public/index.html")


def test_an_empty_prefix_writes_at_the_bucket_root():
    s3 = FakeS3()
    report.publish_run(s3, "b", "", record(), "x\n")
    assert sorted(k for k, _, _ in s3.puts) == ["index.html", "last_run.log", "runs.json"]


# --- the job publishes the page itself, at the end of every run


def test_every_run_rewrites_the_page_at_the_same_address():
    s3, client, docs = FakeS3(), FakeOpenAI(), [doc(1), doc(2), doc(3)]
    assert run_job({"LOG_BUCKET": "b"}, docs, s3, client) == 0
    addresses = set(s3.objects)
    assert run_job({"LOG_BUCKET": "b"}, docs, s3, client) == 0
    assert set(s3.objects) == addresses == {("b", "public/index.html"), ("b", "public/last_run.log"), ("b", "public/runs.json")}
    runs = json.loads(s3.text("public/runs.json"))
    assert [(r["added"], r["skipped"], r["exit_code"]) for r in runs] == [(3, 0, 0), (0, 3, 0)]
    log = s3.text("public/last_run.log")
    assert "[job] start" in log and "[sync] added=0 updated=0 skipped=3" in log
    assert "[report]" not in log  # the page is written after the log is captured


def test_a_failed_run_is_recorded_on_the_page():
    s3 = FakeS3()
    assert run_job({"LOG_BUCKET": "b"}, [doc(1), doc(2)], s3, FakeOpenAI(fail_names={"2-t.md"})) == 1
    (r,) = json.loads(s3.text("public/runs.json"))
    assert (r["exit_code"], r["added"], r["failed"]) == (1, 1, 1)


def test_a_job_that_crashes_before_syncing_is_still_recorded():
    def boom(*args, **kwargs):
        raise ConnectionError("help center unreachable")
    s3 = FakeS3()
    assert main.run({"OPENAI_API_KEY": KEY, "LOG_BUCKET": "b"}, client=FakeOpenAI(), scrape_fn=boom, s3=s3) == 1
    (r,) = json.loads(s3.text("public/runs.json"))
    assert (r["exit_code"], r["added"]) == (1, 0)
    assert "job failed: ConnectionError" in s3.text("public/last_run.log")


def test_a_problem_publishing_never_changes_the_exit_code(caplog):
    assert run_job({"LOG_BUCKET": "b"}, [doc(1)], FakeS3(fail_puts=True)) == 0
    assert "could not publish the status page: RuntimeError: s3 is down" in caplog.text


def test_without_a_bucket_nothing_is_published_and_no_client_is_created(monkeypatch):
    def no_client():
        raise AssertionError("an S3 client must not be created without LOG_BUCKET")
    monkeypatch.setattr(report, "make_s3_client", no_client)
    assert main.run({"OPENAI_API_KEY": KEY}, client=FakeOpenAI(), scrape_fn=scraper([doc(1)])) == 0


def test_prefix_and_repository_link_come_from_the_environment():
    s3 = FakeS3()
    run_job({"LOG_BUCKET": "b", "LOG_PREFIX": "site", "REPO_URL": "https://github.com/x/y"}, [doc(1)], s3)
    assert sorted(k for (_, k) in s3.objects) == ["site/index.html", "site/last_run.log", "site/runs.json"]
    assert "https://github.com/x/y" in s3.text("site/index.html")


def test_the_api_key_is_never_on_the_page():
    s3 = FakeS3()
    run_job({"LOG_BUCKET": "b"}, [doc(1)], s3)
    run_job({"LOG_BUCKET": "b", "ARTICLE_LIMIT": "x"}, [doc(1)], s3)  # a configuration error is logged too
    assert all(KEY.encode() not in body for body in s3.objects.values())


def test_the_calls_match_what_the_real_s3_client_accepts():
    """botocore's Stubber checks every parameter against the S3 API model, which the hand-made fake cannot."""
    boto3 = pytest.importorskip("boto3")
    from botocore.stub import Stubber

    s3 = boto3.client("s3", region_name="ap-southeast-1", aws_access_key_id="x", aws_secret_access_key="y")
    with Stubber(s3) as stub:
        stub.add_client_error("get_object", service_error_code="NoSuchKey", http_status_code=404,
                              expected_params={"Bucket": "b", "Key": "public/runs.json"})
        for key, content_type in (("public/last_run.log", "text/plain; charset=utf-8"),
                                  ("public/runs.json", "application/json"),
                                  ("public/index.html", "text/html; charset=utf-8")):
            stub.add_response("put_object", {}, {"Bucket": "b", "Key": key, "ContentType": content_type,
                                                 "CacheControl": "no-cache", "Body": stub_any()})
        report.publish_run(s3, "b", "public", record(added=30), "log\n")
        stub.assert_no_pending_responses()


def stub_any():
    from botocore.stub import ANY

    return ANY
