from fakes import FakeOpenAI, doc

from app.sync import sync
from app.vectorstore import Chunking

CH = Chunking()


def run(client, docs, remove_missing=False, chunking=CH):
    return sync(client, "vs_1", docs, chunking, remove_missing)


def counts(result):
    return (result.added, result.updated, result.skipped, result.removed, result.failed)


def test_first_run_adds_everything_and_second_run_skips_everything():
    client, docs = FakeOpenAI(), [doc(1), doc(2), doc(3)]
    assert counts(run(client, docs)) == (3, 0, 0, 0, 0)
    assert counts(run(client, docs)) == (0, 0, 3, 0, 0)
    assert client.article_ids() == ["1", "2", "3"]


def test_changed_article_is_updated_without_duplicates_and_uploaded_before_the_old_file_goes():
    client = FakeOpenAI()
    run(client, [doc(1), doc(2)])
    client.ops.clear()
    result = run(client, [doc(1), doc(2, text="# T\n\nchanged\n")])
    assert counts(result) == (0, 1, 1, 0, 0)
    assert client.article_ids() == ["1", "2"]
    kinds = [op for op, _ in client.ops]
    assert kinds.index("attach") < kinds.index("detach")


def test_failed_replacement_keeps_the_old_file_and_the_next_run_recovers():
    client = FakeOpenAI()
    run(client, [doc(1), doc(2)])
    changed = [doc(1), doc(2, text="# T\n\nchanged\n")]
    client.fail_names.add("2-t.md")
    result = run(client, changed)
    assert counts(result) == (0, 0, 1, 0, 1)
    assert not result.ok
    assert any(v["attributes"]["article_id"] == "2" and v["status"] == "completed" for v in client.attached.values())
    client.fail_names.clear()
    assert counts(run(client, changed)) == (0, 1, 1, 0, 0)
    assert client.article_ids() == ["1", "2"]


def test_removed_articles_are_deleted_only_when_asked():
    client = FakeOpenAI()
    run(client, [doc(1), doc(2), doc(3)])
    assert counts(run(client, [doc(1), doc(2)])) == (0, 0, 2, 0, 0)
    assert client.article_ids() == ["1", "2", "3"]
    assert counts(run(client, [doc(1), doc(2)], remove_missing=True)) == (0, 0, 2, 1, 0)
    assert client.article_ids() == ["1", "2"]


def test_removal_is_skipped_and_reported_when_the_scrape_looks_broken():
    client = FakeOpenAI()
    run(client, [doc(n) for n in range(1, 7)])
    result = run(client, [doc(1)], remove_missing=True)  # 1 of 6 is under the 50% guard
    assert counts(result) == (0, 0, 1, 0, 0)
    assert result.problems and not result.ok
    assert len(client.attached) == 6


def test_changing_the_chunking_re_uploads_everything():
    client, docs = FakeOpenAI(), [doc(1), doc(2)]
    run(client, docs)
    assert counts(run(client, docs, chunking=Chunking(400, 100))) == (0, 2, 0, 0, 0)
    assert client.article_ids() == ["1", "2"]


def test_failed_files_are_cleaned_up_and_retried_on_the_next_run():
    client = FakeOpenAI(fail_names={"1-t.md"})
    assert counts(run(client, [doc(1), doc(2)])) == (1, 0, 0, 0, 1)
    client.fail_names.clear()
    assert counts(run(client, [doc(1), doc(2)])) == (1, 0, 1, 0, 0)
    assert client.article_ids() == ["1", "2"]
    assert all(v["status"] == "completed" for v in client.attached.values())


def test_duplicate_files_are_collapsed_to_the_newest():
    client, docs = FakeOpenAI(), [doc(1)]
    run(client, docs)
    (file_id, entry), = client.attached.items()
    client.attached["dup"] = {**entry, "created_at": entry["created_at"] + 50}
    assert counts(run(client, docs)) == (0, 0, 1, 0, 0)
    assert list(client.attached) == ["dup"]
    assert ("detach", file_id) in client.ops


def test_nothing_to_do_makes_no_uploads():
    client, docs = FakeOpenAI(), [doc(1)]
    run(client, docs)
    files_before = dict(client.raw)
    run(client, docs)
    assert client.raw == files_before
