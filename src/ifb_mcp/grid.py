"""Reads ifb.ir's server-rendered tables and pages through its WebForms grids."""

from __future__ import annotations

import re
from html.parser import HTMLParser

from . import http

CP = "ctl00$ContentPlaceHolder1$"  # prefix of every control name on the pages


class Row(list):
    """The text of each cell; `hrefs` holds the first link of each cell (or None)."""

    hrefs: list[str | None]


class Table:
    def __init__(self, tid: str | None, cls: str | None = None):
        self.id = tid
        self.cls = cls or ""
        self.rows: list[Row] = []


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[Table] = []
        self._stack: list[dict] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            t = Table(a.get("id"), a.get("class"))
            self.tables.append(t)
            self._stack.append({"t": t, "row": None, "cell": None})
        elif not self._stack:
            return
        elif tag == "tr":
            row = Row()
            row.hrefs = []
            self._stack[-1]["row"] = row
        elif tag in ("td", "th") and self._stack[-1]["row"] is not None:
            self._stack[-1]["cell"] = {"text": "", "href": None}
        elif tag == "a" and (c := self._stack[-1]["cell"]) is not None and c["href"] is None and a.get("href"):
            c["href"] = a["href"]
        elif tag == "br" and (c := self._stack[-1]["cell"]) is not None:
            c["text"] += " "

    def handle_endtag(self, tag):
        if not self._stack:
            return
        top = self._stack[-1]
        if tag in ("td", "th") and top["cell"] is not None and top["row"] is not None:
            top["row"].append(re.sub(r"\s+", " ", top["cell"]["text"]).strip())
            top["row"].hrefs.append(top["cell"]["href"])
            top["cell"] = None
        elif tag == "tr" and top["row"] is not None:
            if top["row"]:
                top["t"].rows.append(top["row"])
            top["row"] = None
        elif tag == "table":
            self._stack.pop()

    def handle_data(self, data):
        if self._stack and (c := self._stack[-1]["cell"]) is not None:
            c["text"] += data


def tables(html: str) -> list[Table]:
    p = _Parser()
    p.feed(html)
    return p.tables


def table(html: str, suffix: str) -> Table | None:
    """The table whose id ends with `suffix` (e.g. "grdIPOs")."""
    return next((t for t in tables(html) if t.id and t.id.endswith(suffix)), None)


def pairs(html: str) -> dict[str, str]:
    """Label/value rows of the `insTable` blocks on an instrument page (first value wins)."""
    out: dict[str, str] = {}
    for t in tables(html):
        if t.cls.startswith("insTable"):
            for r in t.rows:
                if len(r) == 2 and r[0] and r[0] not in out:
                    out[r[0]] = r[1]
    return out


def hrefid(href: str | None) -> int | None:
    """The instrument id in a link like Instruments.aspx?id=123."""
    m = re.search(r"id=(\d+)", href or "")
    return int(m.group(1)) if m else None


async def grid(path: str, name: str, page: int = 1, form: dict[str, str] | None = None) -> tuple[Table | None, str]:
    """One page of the WebForms grid control `name` on `path`, after an optional search `form` (control -> value).

    Searches and pager clicks are postbacks that need the page's hidden state, so this is 1 to 3 requests.
    Returns the table and the final HTML.
    """
    html = await http.get_page(path)
    fields = {k: v for k, v in (form or {}).items()}
    if fields:
        html = await http.post_page(path, {**http.hidden_fields(html), **fields})
    if page > 1:
        target = {"__EVENTTARGET": f"{CP}{name}", "__EVENTARGUMENT": f"Page${page}"}
        keep = {k: v for k, v in fields.items() if not k.endswith("BtnSearch")}  # the button would re-run the search
        html = await http.post_page(path, {**http.hidden_fields(html), **keep, **target})
    return table(html, name), html
