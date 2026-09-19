from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import openai
import pytest

from app import vectorstore as vs
from app.scrape import Doc


def doc(n, text="# T\n\nbody\n", updated="2026-01-01T00:00:00Z"):
    return Doc(str(n), f"T{n}", f"https://x/{n}", updated, f"{n}-t.md", text)


class FakeClient:
    """Just enough of the OpenAI client for vectorstore.py."""

    def __init__(self, stores=(), bad_filenames=(), failed_ids=()):
        self.created_files, self.batches, self.deleted_files, self.deleted_stores = [], [], [], []
        self.bad_filenames, self.failed_ids = set(bad_filenames), set(failed_ids)
        self.stores = list(stores)
        self.files = NS(create=self._create_file, delete=self.deleted_files.append)
        self.vector_stores = NS(
            list=lambda limit=None: iter(self.stores),
            create=self._create_store,
            delete=self.deleted_stores.append,
            files=NS(list=lambda store_id, limit=None: iter([NS(id="f1"), NS(id="f2")])),
            file_batches=NS(create_and_poll=self._batch, list_files=self._list_failed),
        )

    def _create_file(self, file, purpose):
        name = file[0]
        if name in self.bad_filenames:
            raise openai.OpenAIError("boom")
        self.created_files.append((file, purpose))
        return NS(id=f"file-{name}")

    def _create_store(self, name):
        store = NS(id="vs_new", name=name)
        self.stores.append(store)
        return store

    def _batch(self, store_id, files):
        self.batches.append((store_id, files))
        failed = [f for f in files if f["attributes"]["article_id"] in self.failed_ids]
        counts = NS(completed=len(files) - len(failed), failed=len(failed), cancelled=0)
        return NS(id=f"batch{len(self.batches)}", file_counts=counts)

    def _list_failed(self, batch_id, vector_store_id, filter):
        if filter != "failed":
            return iter([])
        batch = self.batches[int(batch_id[5:]) - 1][1]
        return iter([
            NS(id=f["file_id"], attributes=f["attributes"], last_error=NS(message="unsupported"))
            for f in batch if f["attributes"]["article_id"] in self.failed_ids
        ])


def test_chunking_param_shape_and_validation():
    assert vs.Chunking(800, 200).param() == {
        "type": "static",
        "static": {"max_chunk_size_tokens": 800, "chunk_overlap_tokens": 200},
    }
    for bad in [(99, 0), (4097, 0), (800, 401), (800, -1)]:
        with pytest.raises(ValueError):
            vs.Chunking(*bad)
    vs.Chunking(800, 400)  # exactly half is allowed


def test_estimate_chunks():
    c = vs.Chunking(800, 200)
    assert [vs.estimate_chunks(t, c) for t in (0, 1, 800, 801, 1400, 1401)] == [0, 1, 1, 2, 2, 3]


def test_file_attributes_drop_empty_values_and_hash_is_stable():
    attrs = vs.file_attributes(doc(7, updated=""))
    assert attrs["article_id"] == "7"
    assert attrs["url"] == "https://x/7"
    assert "updated_at" not in attrs
    assert attrs["content_hash"] == vs.file_attributes(doc(7, updated="x"))["content_hash"]
    assert attrs["content_hash"] != vs.file_attributes(doc(7, text="other"))["content_hash"]


def test_get_or_create_store_finds_existing_by_name():
    client = FakeClient(stores=[NS(id="vs_a", name="other"), NS(id="vs_b", name="kb")])
    assert vs.get_or_create_store(client, "kb") == "vs_b"
    assert len(client.stores) == 2


def test_get_or_create_store_creates_when_missing():
    client = FakeClient(stores=[NS(id="vs_a", name="other")])
    assert vs.get_or_create_store(client, "kb") == "vs_new"
    assert client.stores[-1].name == "kb"


def test_upload_docs_batches_and_sends_attributes_and_chunking():
    client = FakeClient()
    docs = [doc(n) for n in range(1, 6)]
    result = vs.upload_docs(client, "vs_1", docs, vs.Chunking(800, 200), batch_size=2)
    assert [len(files) for _, files in client.batches] == [2, 2, 1]
    assert all(store == "vs_1" for store, _ in client.batches)
    first = client.batches[0][1][0]
    assert first["attributes"]["article_id"] == "1"
    assert first["chunking_strategy"]["static"]["max_chunk_size_tokens"] == 800
    (name, body, mime), purpose = client.created_files[0]
    assert (name, mime, purpose) == ("1-t.md", "text/markdown", "assistants")
    assert body == docs[0].markdown.encode("utf-8")
    assert (result.files, result.completed, result.failed) == (5, 5, 0)
    assert result.est_chunks == 5


def test_one_failed_file_upload_does_not_stop_the_rest():
    client = FakeClient(bad_filenames={"2-t.md"})
    result = vs.upload_docs(client, "vs_1", [doc(1), doc(2), doc(3)], vs.Chunking())
    assert (result.files, result.completed, result.failed) == (3, 2, 1)
    assert result.failures == [("2", "OpenAIError: boom")]
    assert sum(len(files) for _, files in client.batches) == 2


def test_files_that_fail_processing_are_reported_with_reason():
    client = FakeClient(failed_ids={"2"})
    result = vs.upload_docs(client, "vs_1", [doc(1), doc(2)], vs.Chunking())
    assert (result.completed, result.failed) == (1, 1)
    assert result.failures == [("2", "unsupported")]


@pytest.mark.parametrize("body", [
    {"type": "insufficient_quota", "code": "insufficient_quota"},
    {"type": "insufficient_quota", "code": "credit_balance_exhausted"},  # what the API returns when the balance is 0
])
def test_quota_error_aborts_instead_of_failing_every_file(body):
    err = openai.RateLimitError("quota", response=MagicMock(status_code=429, headers={}), body=body)
    client = FakeClient()
    client.files = NS(create=MagicMock(side_effect=err), delete=lambda file_id: None)
    with pytest.raises(openai.RateLimitError):
        vs.upload_docs(client, "vs_1", [doc(1), doc(2)], vs.Chunking())
    assert client.batches == []


def test_plain_rate_limit_is_not_fatal():
    err = openai.RateLimitError("slow down", response=MagicMock(status_code=429, headers={}), body={"type": "requests", "code": "rate_limit_exceeded"})
    client = FakeClient()
    client.files = NS(create=MagicMock(side_effect=err), delete=lambda file_id: None)
    result = vs.upload_docs(client, "vs_1", [doc(1)], vs.Chunking())
    assert result.failed == 1


def test_delete_store_removes_store_and_its_files():
    client = FakeClient()
    assert vs.delete_store(client, "vs_1") == 2
    assert client.deleted_stores == ["vs_1"]
    assert client.deleted_files == ["f1", "f2"]
