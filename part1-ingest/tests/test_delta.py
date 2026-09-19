from app.delta import classify


def test_new_article_is_added():
    delta = classify({"1": "a"}, {})
    assert (delta.added, delta.updated, delta.skipped, delta.removed) == (["1"], [], [], [])


def test_changed_hash_is_updated():
    delta = classify({"1": "new"}, {"1": "old"})
    assert (delta.added, delta.updated, delta.skipped) == ([], ["1"], [])


def test_same_hash_is_skipped():
    delta = classify({"1": "a"}, {"1": "a"})
    assert (delta.added, delta.updated, delta.skipped) == ([], [], ["1"])


def test_empty_remote_adds_everything():
    delta = classify({"1": "a", "2": "b", "3": "c"}, {})
    assert delta.added == ["1", "2", "3"]
    assert delta.updated == delta.skipped == delta.removed == []


def test_missing_articles_are_removed_only_when_asked():
    scraped, remote = {"1": "a"}, {"1": "a", "2": "b", "3": "c"}
    assert classify(scraped, remote).removed == []
    assert classify(scraped, remote, remove_missing=True).removed == ["2", "3"]


def test_each_article_lands_in_exactly_one_bucket():
    delta = classify({"1": "a", "2": "new", "3": "c"}, {"1": "a", "2": "old", "4": "d"}, remove_missing=True)
    assert (delta.added, delta.updated, delta.skipped, delta.removed) == (["3"], ["2"], ["1"], ["4"])
