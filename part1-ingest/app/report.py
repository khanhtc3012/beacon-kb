"""A status page for the latest runs, written to a public S3 prefix so its address never changes."""
import datetime
import html
import json
import logging
import re
from dataclasses import asdict, dataclass
from typing import List

log = logging.getLogger("beacon-kb")

HISTORY = 30  # runs kept on the page
# The page is public, so refuse to publish a log that looks like it holds a secret or an id.
SENSITIVE = re.compile(r"sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|arn:aws|\bvs_[0-9a-f]{20,}|\bfile-[A-Za-z0-9]{15,}")


@dataclass
class RunRecord:
    started: str  # UTC, ISO 8601
    duration_s: int
    exit_code: int
    added: int = 0
    updated: int = 0
    skipped: int = 0
    removed: int = 0
    failed: int = 0


def looks_sensitive(text: str) -> bool:
    return bool(SENSITIVE.search(text))


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_runs(s3, bucket: str, key: str) -> List[dict]:
    try:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except Exception as exc:  # a missing file is normal on the first run; anything else is a real problem
        code = getattr(exc, "response", {}).get("Error", {}).get("Code")
        if code in ("NoSuchKey", "404"):
            return []
        raise
    try:
        runs = json.loads(body)
    except ValueError:
        runs = None
    if not isinstance(runs, list):
        log.warning("the run history could not be read, starting a new one")
        return []
    return runs


def render_index(runs: List[dict], log_text: str, repo_url: str = "") -> str:
    """One page: the latest result, a table of recent runs (newest first), and the full log of the latest run."""
    esc = html.escape
    latest = runs[-1]
    status = "ok" if latest["exit_code"] == 0 else "failed"
    rows = "".join(
        "<tr><td>{started}</td><td>{result}</td><td>{duration_s}s</td>"
        "<td>{added}</td><td>{updated}</td><td>{skipped}</td><td>{removed}</td><td>{failed}</td></tr>".format(
            started=esc(str(r["started"])), result="ok" if r["exit_code"] == 0 else "failed",
            **{k: int(r.get(k, 0)) for k in ("duration_s", "added", "updated", "skipped", "removed", "failed")})
        for r in reversed(runs)
    )
    repo = f' Code: <a href="{esc(repo_url)}">{esc(repo_url)}</a>.' if repo_url else ""
    return f"""<!doctype html>
<meta charset="utf-8"><title>beacon-kb sync job</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;max-width:60rem;margin:2rem auto;padding:0 1rem}}
table{{border-collapse:collapse}}td,th{{border:1px solid #ccc;padding:.3rem .6rem;text-align:left}}
pre{{background:#f4f4f4;padding:1rem;overflow:auto;font-size:13px}}</style>
<h1>beacon-kb: daily sync job</h1>
<p>Latest run: <b>{status}</b>, started {esc(str(latest["started"]))}.{repo}
This page is rewritten by the job at the end of every run.</p>
<h2>Recent runs</h2>
<table><tr><th>Started (UTC)</th><th>Result</th><th>Duration</th><th>Added</th><th>Updated</th><th>Skipped</th><th>Removed</th><th>Failed</th></tr>{rows}</table>
<h2>Log of the latest run</h2>
<p><a href="last_run.log">last_run.log</a> (plain text)</p>
<pre>{esc(log_text)}</pre>
"""


def publish_run(s3, bucket: str, prefix: str, record: RunRecord, log_text: str, repo_url: str = "") -> None:
    """Overwrite index.html, last_run.log and runs.json under `prefix`. The keys are fixed, so the address is stable."""
    if looks_sensitive(log_text):
        raise ValueError("the log looks sensitive, so it was not published")
    prefix = prefix.strip("/")

    def key(name: str) -> str:
        return f"{prefix}/{name}" if prefix else name

    runs = (_load_runs(s3, bucket, key("runs.json")) + [asdict(record)])[-HISTORY:]
    files = (
        ("last_run.log", log_text, "text/plain; charset=utf-8"),
        ("runs.json", json.dumps(runs), "application/json"),
        ("index.html", render_index(runs, log_text, repo_url), "text/html; charset=utf-8"),
    )
    for name, body, content_type in files:
        s3.put_object(Bucket=bucket, Key=key(name), Body=body.encode("utf-8"),
                      ContentType=content_type, CacheControl="no-cache")


def make_s3_client():
    import boto3

    return boto3.client("s3")
