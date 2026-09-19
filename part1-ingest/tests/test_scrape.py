from pathlib import Path

import pytest

from app.scrape import Doc, write_docs

OUT = Path(__file__).resolve().parent.parent / "out"


def doc(article_id, name, text="# T\n"):
    return Doc(article_id, "T", f"https://x/{article_id}", "2026-01-01T00:00:00Z", name, text)


def test_write_docs_utf8_lf_and_count(tmp_path):
    n = write_docs([doc("1", "1-a.md", "# Café — ok\n")], tmp_path)
    assert n == 1
    raw = (tmp_path / "1-a.md").read_bytes()
    assert raw == "# Café — ok\n".encode("utf-8")
    assert b"\r" not in raw


def test_renamed_article_replaces_old_file_but_not_neighbours(tmp_path):
    write_docs([doc("1", "1-old-title.md"), doc("12", "12-other.md")], tmp_path)
    write_docs([doc("1", "1-new-title.md")], tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["1-new-title.md", "12-other.md"]


# Golden checks on real data: details the original support bot relied on must survive conversion.
# They run against files produced by `python -m app.scrape` and are skipped when `out/` is empty.
def read_article(article_id):
    matches = sorted(OUT.glob(f"{article_id}-*.md"))
    if not matches:
        pytest.skip("run `python -m app.scrape` first")
    return matches[0].read_text(encoding="utf-8")


def test_golden_youtube_article_keeps_shorts_note():
    text = read_article(360051014713)
    assert "/shorts/" in text
    assert "watch?v=" in text
    assert text.startswith("# How to Use YouTube with OptiSigns\n\nArticle URL: https://")


def test_golden_push_contents_article_keeps_ui_labels():
    text = read_article(18988049363859)
    assert "Push To Screens" in text
    assert "Schedule Changes" in text


def test_golden_split_screen_article():
    text = read_article(360026559573)
    assert "Split Screen" in text
