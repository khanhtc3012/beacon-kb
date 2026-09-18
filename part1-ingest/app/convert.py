"""Convert a Help Center article (HTML body) into clean Markdown."""


def to_markdown(article: dict, base_url: str) -> str:
    """Return `# title`, an `Article URL:` line, then the converted body.

    Keeps headings, code blocks, lists and tables; makes relative links absolute;
    drops scripts, styles and in-page anchor links.
    """
    raise NotImplementedError("step 3")


def filename(article: dict) -> str:
    """`<id>-<slug>.md`; the id prefix keeps names unique."""
    raise NotImplementedError("step 3")
