"""Convert a Help Center article (HTML body) into clean Markdown."""
import re
import unicodedata
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, NavigableString, Tag
from markdownify import markdownify

HEADINGS = ["h1", "h2", "h3", "h4", "h5", "h6"]
BLOCKS = {"p", "ul", "ol", "table", "blockquote", "pre", "hr", "div", "figure", "figcaption", *HEADINGS}
SKIP_SCHEMES = ("#", "mailto:", "tel:", "data:", "javascript:")


def _drop_anchor_lists(soup: BeautifulSoup) -> None:
    """Remove in-page table-of-contents lists; unwrap any remaining `#anchor` links."""

    def anchor_only(li: Tag) -> bool:
        for child in li.children:
            if isinstance(child, Tag):
                if child.name == "a" and child.get("href", "").startswith("#"):
                    continue
                if child.name in ("ul", "ol") and all(anchor_only(i) for i in child.find_all("li", recursive=False)):
                    continue
                return False
            if str(child).strip():
                return False
        return True

    for lst in soup.find_all(["ul", "ol"]):
        if lst.parent is None:
            continue
        items = lst.find_all("li", recursive=False)
        if items and all(anchor_only(li) for li in items):
            lst.decompose()
    for a in soup.find_all("a", href=True):
        if a["href"].startswith("#"):
            a.unwrap()


def _absolutize(soup: BeautifulSoup, base_url: str) -> None:
    base = base_url.rstrip("/") + "/"
    for tag, attr in (("a", "href"), ("img", "src")):
        for el in soup.find_all(tag):
            value = (el.get(attr) or "").strip()
            if value and not value.lower().startswith(SKIP_SCHEMES):
                el[attr] = urljoin(base, value)


def _images(soup: BeautifulSoup) -> None:
    """Keep images that have alt text; drop the rest (screenshots with no text add only URL noise)."""
    for img in soup.find_all("img"):
        if not (img.get("alt") or "").strip() or not img.get("src"):
            img.decompose()


def _iframes(soup: BeautifulSoup, base_url: str) -> None:
    for frame in soup.find_all("iframe"):
        src = (frame.get("src") or frame.get("data-src") or "").strip()
        if not src:
            frame.decompose()
            continue
        src = urljoin(base_url.rstrip("/") + "/", src)
        host = urlparse(src).netloc
        if "youtube" in host or "youtu.be" in host:
            label = "YouTube video"
        elif "canva" in host:
            label = "Canva design"
        else:
            label = "Embedded content"
        link = soup.new_tag("a", href=src)
        link.string = label
        para = soup.new_tag("p")
        para.append(link)
        frame.replace_with(para)


def _tables(soup: BeautifulSoup) -> None:
    """One-column tables are callout boxes, not data: turn them into blockquotes."""
    for table in soup.find_all("table"):
        for junk in table.find_all(["colgroup", "col"]):
            junk.decompose()
        rows = [r.find_all(["td", "th"], recursive=False) for r in table.find_all("tr")]
        if rows and max(len(cells) for cells in rows) <= 1:
            quote = soup.new_tag("blockquote")
            for cells in rows:
                for cell in cells:
                    cell.name = "div"
                    quote.append(cell.extract())
            table.replace_with(quote)


def _flatten_blocks(soup: BeautifulSoup) -> None:
    """markdownify does not break lines around <div>: unwrap it or turn it into a paragraph."""
    for el in reversed(soup.find_all(["div", "figure", "figcaption", "section", "article", "center"])):
        if any(isinstance(c, Tag) and c.name in BLOCKS for c in el.children):
            el.unwrap()
        else:
            el.name = "p"


def _headings(soup: BeautifulSoup) -> None:
    for h in soup.find_all(HEADINGS):
        if not h.get_text(strip=True):
            h.decompose()
            continue
        if h.name == "h1":  # the article title is the only h1
            h.name = "h2"
        for bold in h.find_all(["strong", "b"]):
            bold.unwrap()


def _inline(soup: BeautifulSoup) -> None:
    """Drop empty emphasis and merge adjacent ones, so `<b>a</b><b>b</b>` is not `**a****b**`."""
    tags = ["strong", "b", "em", "i"]
    for el in soup.find_all(tags):
        if not el.get_text(strip=True) and not el.find(["img", "a"]):
            el.unwrap()
    for el in soup.find_all(tags):
        if el.parent is None:
            continue
        while True:
            sib = el.next_sibling
            gap = sib if isinstance(sib, NavigableString) and not sib.strip() else None
            nxt = gap.next_sibling if gap is not None else sib
            if not (isinstance(nxt, Tag) and nxt.name == el.name):
                break
            if gap is not None:
                el.append(NavigableString(str(gap)))
                gap.extract()
            for child in list(nxt.contents):
                el.append(child)
            nxt.decompose()


def _html_to_md(html: str, base_url: str) -> str:
    if not html.strip():
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for junk in soup.find_all(["script", "style", "noscript"]):
        junk.decompose()
    _drop_anchor_lists(soup)
    _absolutize(soup, base_url)
    _images(soup)
    _iframes(soup, base_url)
    _tables(soup)
    _flatten_blocks(soup)
    _headings(soup)
    _inline(soup)
    md = markdownify(str(soup), heading_style="ATX", bullets="-", table_infer_header=True)
    md = md.replace("\xa0", " ").replace("​", "").replace("﻿", "")
    # `****` (adjacent or nested bold that survived) breaks bold rendering; leave code blocks alone
    chunks = md.split("```")
    for n in range(0, len(chunks), 2):
        chunks[n] = re.sub(r"(?<!\*)\*\*\*\*(?!\*)", "", chunks[n])
    md = "```".join(chunks)
    md = re.sub(r"(?m)^(>+)[ \t]+$", r"\1", md)  # blank lines inside blockquotes
    md = re.sub(r"[ \t]+\n(?=>*\n)", "\n", md)  # hard break before a blank line is noise
    md = re.sub(r"\n{3,}", "\n\n", md)
    md = re.sub(r"(\n>\n)(?:>\n)+", r"\1", md)  # collapse runs of empty quote lines
    return md.strip()


def to_markdown(article: dict, base_url: str) -> str:
    """Return `# title`, an `Article URL:` line, then the converted body.

    Keeps headings, code blocks, lists and tables; makes relative links absolute;
    drops scripts, styles, in-page anchor lists and images without alt text.
    One-column tables become blockquotes; body h1 is demoted to h2.
    """
    title = (article.get("title") or article.get("name") or "").strip()
    body = _html_to_md(article.get("body") or "", base_url)
    parts = [f"# {title}", f"Article URL: {article['html_url']}"]
    if body:
        parts.append(body)
    return "\n\n".join(parts) + "\n"


def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text[:80].strip("-") or "article"


def filename(article: dict) -> str:
    """`<id>-<slug>.md`; the id prefix keeps names unique."""
    return f"{article['id']}-{_slugify(article.get('title') or '')}.md"
