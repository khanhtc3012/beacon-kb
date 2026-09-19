from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import openai
import pytest

from app import vectorstore as vs
from app.scrape import Doc

CH = vs.Chunking(800, 200)


def doc(n, text="# T\n\nbody\n", updated="2026-01-01T00:00:00Z"):
    return Doc(str(n), f"T{n}", f"https://x/{n}", updated, f"{n}-t.md", text)


def remote_item(file_id, article_id="1", status="completed", content_hash="h", created_at=1):
    attrs = {"article_id": article_id, "content_hash": content_hash} if article_id else None
    return NS(id=file_id, status=status, attributes=attrs, created_at=created_at)


def not_found():
    return openai.NotFoundError("gone", response=MagicMock(status_code=404, headers={}), body=None)


class FakeClient:
    """Just enough of the OpenAI client for vectorstore.py."""

    def __init__(self, stores=(), bad_filenames=(), failed_ids=(), remote_items=()):
        self.created_files, self.batches, self.deleted_files, self.deleted_stores = [], [], [], []
        self.detached = []
        self.bad_filenames, self.failed_ids = set(bad_filenames), set(failed_ids)
        self.stores = list(stores)
        self.files = NS(create=self._create_file, delete=self.deleted_files.append)
        self.vector_stores = NS(
            list=lambda limit=None: iter(self.stores),
            create=self._create_store,
            delete=self.deleted_stores.append,
            files=NS(
                list=lambda store_id, limit=None: iter(remote_items),
                delete=lambda file_id, vector_store_id: self.detached.append(file_id),
            ),
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
    assert [vs.estimate_chunks(t, CH) for t in (0, 1, 800, 801, 1400, 1401)] == [0, 1, 1, 2, 2, 3]


def test_file_attributes_drop_empty_values():
    attrs = vs.file_attributes(doc(7, updated=""), CH)
    assert attrs["article_id"] == "7"
    assert attrs["url"] == "https://x/7"
    assert "updated_at" not in attrs


def test_fingerprint_depends_on_text_and_chunking_but_not_metadata():
    base = vs.file_attributes(doc(7), CH)["content_hash"]
    assert base == vs.file_attributes(doc(7, updated="later"), CH)["content_hash"]
    assert base != vs.file_attributes(doc(7, text="other"), CH)["content_hash"]
    assert base != vs.file_attributes(doc(7), vs.Chunking(400, 100))["content_hash"]


def test_get_or_create_store_finds_existing_by_name():
    client = FakeClient(stores=[NS(id="vs_a", name="other"), NS(id="vs_b", name="kb")])
    assert vs.get_or_create_store(client, "kb") == "vs_b"
    assert len(client.stores) == 2


def test_get_or_create_store_creates_when_missing():
    client = FakeClient(stores=[NS(id="vs_a", name="other")])
    assert vs.get_or_create_store(client, "kb") == "vs_new"
    assert client.stores[-1].name == "kb"


def test_find_store_returns_none_when_missing():
    assert vs.find_store(FakeClient(), "kb") is None


def test_upload_docs_batches_and_sends_attributes_and_chunking():
    client = FakeClient()
    docs = [doc(n) for n in range(1, 6)]
    result = vs.upload_docs(client, "vs_1", docs, CH, batch_size=2)
    assert [len(files) for _, files in client.batches] == [2, 2, 1]
    assert all(store == "vs_1" for store, _ in client.batches)
    first = client.batches[0][1][0]
    assert first["attributes"]["article_id"] == "1"
    assert first["attributes"]["content_hash"] == vs.fingerprint(docs[0].markdown, CH)
    assert first["chunking_strategy"]["static"]["max_chunk_size_tokens"] == 800
    (name, body, mime), purpose = client.created_files[0]
    assert (name, mime, purpose) == ("1-t.md", "text/markdown", "assistants")
    assert body == docs[0].markdown.encode("utf-8")
    assert (result.files, result.completed, result.failed) == (5, 5, 0)
    assert result.est_chunks == 5


def test_upload_docs_with_nothing_to_upload_makes_no_calls():
    client = FakeClient()
    result = vs.upload_docs(client, "vs_1", [], CH)
    assert (result.files, result.completed, result.failed) == (0, 0, 0)
    assert client.batches == [] and client.created_files == []


def test_one_failed_file_upload_does_not_stop_the_rest():
    client = FakeClient(bad_filenames={"2-t.md"})
    result = vs.upload_docs(client, "vs_1", [doc(1), doc(2), doc(3)], CH)
    assert (result.files, result.completed, result.failed) == (3, 2, 1)
    assert result.failures == [("2", "OpenAIError: boom")]
    assert sum(len(files) for _, files in client.batches) == 2


def test_files_that_fail_processing_are_reported_with_reason():
    client = FakeClient(failed_ids={"2"})
    result = vs.upload_docs(client, "vs_1", [doc(1), doc(2)], CH)
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
        vs.upload_docs(client, "vs_1", [doc(1), doc(2)], CH)
    assert client.batches == []


def test_plain_rate_limit_is_not_fatal():
    err = openai.RateLimitError("slow down", response=MagicMock(status_code=429, headers={}), body={"type": "requests", "code": "rate_limit_exceeded"})
    client = FakeClient()
    client.files = NS(create=MagicMock(side_effect=err), delete=lambda file_id: None)
    result = vs.upload_docs(client, "vs_1", [doc(1)], CH)
    assert result.failed == 1


def test_list_remote_reads_completed_files_by_article_id():
    client = FakeClient(remote_items=[remote_item("f1", "1", content_hash="aaa"), remote_item("f2", "2", content_hash="bbb")])
    remote = vs.list_remote(client, "vs_1")
    assert {k: (v.file_id, v.content_hash) for k, v in remote.files.items()} == {"1": ("f1", "aaa"), "2": ("f2", "bbb")}
    assert remote.junk == []


def test_list_remote_marks_failed_and_cancelled_files_as_junk():
    client = FakeClient(remote_items=[remote_item("f1", status="failed"), remote_item("f2", status="cancelled"), remote_item("f3", "3")])
    remote = vs.list_remote(client, "vs_1")
    assert remote.junk == ["f1", "f2"]
    assert list(remote.files) == ["3"]


def test_list_remote_keeps_newest_duplicate_and_marks_older_as_junk():
    client = FakeClient(remote_items=[
        remote_item("new", "1", created_at=20), remote_item("old", "1", created_at=10), remote_item("mid", "1", created_at=15),
    ])
    remote = vs.list_remote(client, "vs_1")
    assert remote.files["1"].file_id == "new"
    assert sorted(remote.junk) == ["mid", "old"]


def test_list_remote_leaves_unfinished_and_unlabelled_files_alone():
    client = FakeClient(remote_items=[remote_item("f1", "1", status="in_progress"), remote_item("f2", article_id=None)])
    remote = vs.list_remote(client, "vs_1")
    assert remote.files == {} and remote.junk == []


def test_delete_file_removes_it_from_the_store_and_deletes_the_file():
    client = FakeClient()
    vs.delete_file(client, "vs_1", "f1")
    assert client.detached == ["f1"]
    assert client.deleted_files == ["f1"]


def test_delete_file_ignores_files_that_are_already_gone():
    client = FakeClient()
    client.vector_stores.files.delete = MagicMock(side_effect=not_found())
    client.files.delete = MagicMock(side_effect=not_found())
    vs.delete_file(client, "vs_1", "f1")  # must not raise
    client.files.delete.assert_called_once_with("f1")


def test_delete_store_removes_store_and_its_files():
    client = FakeClient(remote_items=[NS(id="f1"), NS(id="f2")])
    assert vs.delete_store(client, "vs_1") == 2
    assert client.deleted_stores == ["vs_1"]
    assert client.deleted_files == ["f1", "f2"]


def test_token_estimate_falls_back_to_characters_when_tiktoken_is_unavailable(monkeypatch):
    class Broken:
        @staticmethod
        def get_encoding(name):
            raise ConnectionError("cannot download the vocabulary")

    monkeypatch.setitem(__import__("sys").modules, "tiktoken", Broken)
    vs._encoding.cache_clear()
    try:
        assert vs.count_tokens("x" * 400) == 100
        assert vs.count_tokens("") == 0
    finally:
        vs._encoding.cache_clear()
