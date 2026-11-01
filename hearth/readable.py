"""A web page's readable text: the article, not the menus around it.

What a page says sits in <article> or <main> when the page marks it, else in
<body>; navigation, headers, footers, sidebars, forms, scripts and cookie
banners are left out. Headings, list items, table rows and link targets keep
enough shape (as Markdown) for a model to follow the page. Standard library
only, so it works in every build.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import List, Optional, Tuple

_DROP = {"script", "style", "noscript", "template", "svg", "canvas", "iframe", "nav", "header",
         "footer", "aside", "form", "button", "select", "dialog", "head", "object"}
_VOID = {"br", "img", "hr", "input", "meta", "link", "source", "wbr", "area", "base", "col", "embed", "param", "track"}
_BLOCK = {"p", "div", "section", "article", "main", "br", "tr", "table", "ul", "ol", "dl", "blockquote",
          "pre", "figure", "figcaption", "dd", "dt", "hr"}
_NOISE = re.compile(r"cookie|consent|banner|newsletter|subscribe|share|social|related|promo|advert|\bad\b|"
                    r"sidebar|breadcrumb|comment|popup|modal|menu|navbar|footer|header", re.I)


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self.stack: List[Tuple[str, bool]] = []    # (tag, dropped here)
        self.drop = 0
        self.parts: dict = {"article": [], "main": [], "body": []}
        self.where: List[str] = []                 # article/main we are inside
        self.href: Optional[str] = None
        self.link_text: List[str] = []
        self.pre = 0

    def _out(self, s: str) -> None:
        if self.drop:
            return
        for k in set(self.where) | {"body"}:
            self.parts[k].append(s)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title" and not self.title and not any(t == "svg" for t, _d in self.stack):
            self._in_title = True       # the page's own title, not an icon's
        if tag in _VOID:
            if tag == "br":
                self._out("\n")
            elif tag == "img" and not self.drop and (a.get("alt") or "").strip():
                self._out(f" [image: {a['alt'].strip()[:80]}] ")
            return
        cls = " ".join(filter(None, [a.get("class") or "", a.get("id") or "", a.get("role") or ""]))
        hidden = "hidden" in a or (a.get("aria-hidden") == "true") or "display:none" in (a.get("style") or "").replace(" ", "")
        drop = tag in _DROP or hidden or (tag in ("div", "section", "ul", "span") and bool(_NOISE.search(cls))
                                          and not re.search(r"article|content|post|entry|story", cls, re.I))
        self.stack.append((tag, drop))
        if drop:
            self.drop += 1
            return
        if tag in ("article", "main"):
            self.where.append(tag)
        if tag == "pre":
            self.pre += 1
            self._out("\n```\n")
        elif re.fullmatch(r"h[1-6]", tag):
            self._out("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "li":
            self._out("\n- ")
        elif tag in ("td", "th"):
            self._out(" | ")
        elif tag in _BLOCK:
            self._out("\n")
        elif tag == "a":
            href = a.get("href") or ""
            self.href = href if href.startswith("http") else None
            self.link_text = []

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in _VOID:
            return
        # Close up to the matching open tag; HTML in the wild leaves tags open.
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                for t, dropped in reversed(self.stack[i:]):
                    if dropped:
                        self.drop -= 1
                    elif t in ("article", "main") and t in self.where:
                        self.where.remove(t)
                    elif t == "pre":
                        self.pre = max(0, self.pre - 1)
                        self._out("\n```\n")
                del self.stack[i:]
                break
        if self.drop:
            return
        if tag == "a" and self.href:
            text = " ".join(self.link_text).strip()
            if text and len(text) > 2:
                self._out(f" ({self.href})")
            self.href = None
        elif re.fullmatch(r"h[1-6]", tag) or tag in _BLOCK:
            self._out("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self.drop:
            return
        if self.pre:
            self._out(data)
            return
        text = re.sub(r"\s+", " ", data)
        if text.strip():
            if self.href is not None:
                self.link_text.append(text.strip())
            self._out(text)


def _tidy(s: str) -> str:
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    s = re.sub(r"\n(\| )+", "\n| ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def extract(page: str) -> Tuple[str, str]:
    """(title, readable text) of an HTML page."""
    p = _Page()
    try:
        p.feed(page)
        p.close()
    except Exception:
        pass
    body = _tidy("".join(p.parts["body"]))
    for k in ("article", "main"):
        part = _tidy("".join(p.parts[k]))
        # The marked part, when it holds most of what the page says.
        if len(part) > 400 and len(part) > 0.35 * len(body):
            body = part
            break
    return re.sub(r"\s+", " ", p.title).strip()[:200], body
