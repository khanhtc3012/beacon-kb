from app.convert import filename, to_markdown

BASE = "https://help.example.com"


def md(body, title="Title", **extra):
    article = {"id": 1, "title": title, "html_url": f"{BASE}/hc/en-us/articles/1-title", "body": body}
    article.update(extra)
    return to_markdown(article, BASE)


def test_header_has_title_and_article_url():
    out = md("<p>Hello</p>")
    assert out.startswith("# Title\n\nArticle URL: https://help.example.com/hc/en-us/articles/1-title\n\n")
    assert out.rstrip().endswith("Hello")


def test_relative_links_become_absolute():
    out = md('<p>See <a href="/hc/en-us/articles/2-other">this</a> and <a href="https://x.io/a">that</a>.</p>')
    assert "[this](https://help.example.com/hc/en-us/articles/2-other)" in out
    assert "[that](https://x.io/a)" in out


def test_pre_becomes_code_block():
    out = md("<pre>line one\n  line two</pre>")
    assert "```\nline one\n  line two\n```" in out


def test_heading_levels_preserved_and_body_h1_demoted():
    out = md("<h1>Top</h1><h2>Two</h2><h3><strong>Three</strong></h3>")
    assert "## Top" in out
    assert "## Two" in out
    assert "### Three" in out
    assert "**Three**" not in out
    assert out.count("\n# ") == 0  # the only h1 is the title on the first line


def test_empty_body_does_not_crash():
    assert md("").strip().endswith("Article URL: https://help.example.com/hc/en-us/articles/1-title")
    assert md(None).startswith("# Title")
    assert md("   \n ").startswith("# Title")


def test_anchor_only_toc_dropped_and_other_anchors_unwrapped():
    out = md('<ul><li><a href="#a">First</a></li><li><a href="#b">Second</a></li></ul>'
             '<p>Jump to <a href="#a">first</a>.</p><ul><li>real item</li></ul>')
    assert "First" not in out and "Second" not in out
    assert "Jump to first." in out
    assert "- real item" in out


def test_one_column_table_becomes_blockquote():
    out = md("<table><tbody><tr><td><strong>NOTE</strong></td></tr><tr><td>Be careful.</td></tr></tbody></table>")
    assert "> **NOTE**" in out
    assert "> Be careful." in out
    assert "|" not in out


def test_table_with_header_is_kept_as_table():
    out = md("<table><thead><tr><th>Name</th><th>Value</th></tr></thead>"
             "<tbody><tr><td>a</td><td>1</td></tr></tbody></table>")
    assert "| Name | Value |" in out
    assert "| --- | --- |" in out
    assert "| a | 1 |" in out


def test_table_without_header_gets_one():
    out = md("<table><tbody><tr><td>a</td><td>1</td></tr><tr><td>b</td><td>2</td></tr></tbody></table>")
    lines = [line for line in out.splitlines() if line.startswith("|")]
    assert lines[1].replace(" ", "") == "|---|---|"
    assert len(lines) == 3


def test_images_keep_alt_and_absolute_url_drop_without_alt():
    out = md('<p><img src="/hc/article_attachments/1" alt="Settings page"></p>'
             '<p><img src="/hc/article_attachments/2" alt=""></p><p><img src="/x.png"></p>')
    assert "![Settings page](https://help.example.com/hc/article_attachments/1)" in out
    assert "article_attachments/2" not in out
    assert "x.png" not in out


def test_iframe_becomes_link_and_srcless_iframe_dropped():
    out = md('<iframe src="https://www.youtube.com/embed/abc"></iframe><iframe></iframe>')
    assert "[YouTube video](https://www.youtube.com/embed/abc)" in out
    assert "<iframe" not in out


def test_adjacent_bold_is_merged():
    out = md("<p><strong>RMA request</strong><strong>.</strong></p>")
    assert "**RMA request.**" in out
    assert "****" not in out


def test_divs_do_not_run_together():
    out = md("<div>first</div><div>second</div>")
    assert "first\n\nsecond" in out


def test_scripts_and_styles_removed():
    out = md("<p>keep</p><script>alert(1)</script><style>p{}</style>")
    assert "alert" not in out and "p{}" not in out


def test_non_breaking_space_normalised():
    assert "\xa0" not in md("<p>a&nbsp;b</p>")


def test_filename_has_id_prefix_and_ascii_slug():
    assert filename({"id": 42, "title": "How to Use YouTube with OptiSigns!"}) == "42-how-to-use-youtube-with-optisigns.md"
    assert filename({"id": 7, "title": "Café — Menü"}) == "7-cafe-menu.md"
    assert filename({"id": 9, "title": "???"}) == "9-article.md"
    assert len(filename({"id": 1, "title": "x" * 500})) < 100
