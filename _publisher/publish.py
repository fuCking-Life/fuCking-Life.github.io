#!/usr/bin/env python3
"""Publish posts and daily-digest items to this static site.

Each post is stored as a record in ``_publisher/posts/<slug>.json``. Publishing
upserts that record (or the digest items in ``daily/items.json``) and then
rebuilds every generated page from all records with ``sitegen.py``: the
homepage, post pages, column and tag pages, the archive, RSS, the sitemap,
``404.html``, and ``/daily/``.

Run ``python3 _publisher/publish.py --help`` for the arguments. See
``_publisher/README.md`` for the files that change and the known limits.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sitegen  # noqa: E402
from sitegen import (  # noqa: E402,F401
    COLUMNS,
    SITE_BASE,
    TZ,
    PostRecord,
    PublishError,
    mark_callouts,
    plain_text,
    urlize,
    xml_escape,
)

_META_START = re.compile(r"^(?:时间窗口|覆盖范围|\d{4}年)")

# Fixed vocabularies for /daily/. Names stay in this order in the filter UI.
DOMAINS = [
    "语音 Agent",
    "全双工",
    "打断与轮次",
    "语音识别 ASR",
    "语音合成 TTS",
    "端到端语音",
    "端侧",
    "iOS",
    "苹果生态",
    "个人 Agent",
    "Agent 架构",
    "工具与 MCP",
    "大模型",
    "多模态",
    "评测与榜单",
    "开源",
    "论文",
    "智能座舱",
]
TYPES = [
    "产品发布",
    "模型发布",
    "开源",
    "技术解读",
    "观点讨论",
    "评测结果",
    "论文",
    "行业数据",
]
SOURCE_BRIEFS = ("lumi", "agent-app")
RELEVANCE_COLORS = ("#6d28d9", "#7c3aed", "#2563eb", "#f59e0b", "#ea580c")
DAILY_ITEMS_REL = "daily/items.json"
DAILY_INDEX_REL = "daily/index.html"

HERE = Path(__file__).resolve().parent
DAILY_TEMPLATE = sitegen.DAILY_TEMPLATE
POSTS_REL = "_publisher/posts"


def hugo_escape(text: str) -> str:
    """Escape text the way Hugo 0.82's HTML templates did, including '+'."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&#34;")
        .replace("'", "&#39;")
        .replace("+", "&#43;")
    )


def validate_slug(slug: str) -> str:
    slug = slug.strip().strip("/")
    if not slug or slug.startswith("/") or "\\" in slug or "//" in slug:
        raise PublishError(f"invalid slug {slug!r}")
    for part in slug.split("/"):
        if part in ("", ".", ".."):
            raise PublishError(f"invalid slug {slug!r}")
        if not re.fullmatch(r"[\w-]+", part, flags=re.UNICODE):
            raise PublishError(
                f"invalid slug {slug!r}; use letters, numbers, '_' and '-'"
            )
    return slug


def parse_when(value: str) -> datetime:
    raw = value.strip()
    date_only = (
        "%Y-%m-%d",
    )
    with_time = (
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%dT%H:%M:%S",
    )
    zoned = (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%d %H:%M:%S%z",
    )
    for fmt in date_only:
        try:
            dt = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        return dt.replace(hour=8, minute=0, second=0, tzinfo=TZ)
    for fmt in with_time:
        try:
            dt = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        return dt.replace(tzinfo=TZ)
    for fmt in zoned:
        try:
            dt = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        return dt.astimezone(TZ)
    raise PublishError(
        f"unrecognized date {value!r}; use YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS+08:00"
    )


def heading_id(text: str, used: set[str]) -> str:
    plain = re.sub(r"[*_`]", "", text).strip().lower()
    chars: list[str] = []
    for ch in plain:
        if ch.isspace():
            chars.append("-")
        elif ch.isalnum() or ch in "-_":
            chars.append(ch)
    slug = "".join(chars) or "section"
    base = slug
    n = 1
    while slug in used:
        n += 1
        slug = f"{base}-{n}"
    used.add(slug)
    return slug


def render_inline(text: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(text):
        if text[i] == "`":
            j = text.find("`", i + 1)
            if j > i:
                out.append("<code>" + hugo_escape(text[i + 1 : j]) + "</code>")
                i = j + 1
                continue
        if text.startswith("![", i):
            match = re.match(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", text[i:])
            if match:
                alt = hugo_escape(match.group(1))
                src = hugo_escape(match.group(2))
                out.append(f'<img src="{src}" alt="{alt}">')
                i += match.end()
                continue
        if text[i] == "[":
            match = re.match(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", text[i:])
            if match:
                label = render_inline(match.group(1))
                href = hugo_escape(match.group(2))
                out.append(f'<a href="{href}">{label}</a>')
                i += match.end()
                continue
        if text.startswith("**", i) or text.startswith("__", i):
            marker = text[i : i + 2]
            j = text.find(marker, i + 2)
            if j > i + 1:
                out.append("<strong>" + render_inline(text[i + 2 : j]) + "</strong>")
                i = j + 2
                continue
        if text[i] in "*_" and (i == 0 or text[i - 1].isspace() or text[i - 1] in "([{"):
            marker = text[i]
            j = text.find(marker, i + 1)
            if j > i + 1 and not text[j - 1].isspace():
                out.append("<em>" + render_inline(text[i + 1 : j]) + "</em>")
                i = j + 1
                continue
        nxt = i + 1
        specials = "`[*_!"
        while nxt < len(text) and text[nxt] not in specials:
            nxt += 1
        if nxt == i:
            out.append(hugo_escape(text[i]))
            i += 1
        else:
            out.append(hugo_escape(text[i:nxt]))
            i = nxt
    return "".join(out)


def _list_item(line: str) -> tuple[int, bool, str] | None:
    match = re.match(r"^(\s*)([-*+]|\d+\.)\s+(.*)$", line)
    if not match:
        return None
    indent = len(match.group(1).expandtabs(4))
    ordered = match.group(2)[0].isdigit()
    return indent, ordered, match.group(3)


def _continuation(line: str, marker_indent: int) -> str | None:
    if not line.strip():
        return None
    expanded = line.expandtabs(4)
    indent = len(expanded) - len(expanded.lstrip(" "))
    if indent <= marker_indent:
        return None
    stripped = expanded.lstrip(" ")
    if stripped.startswith(("```", "~~~")) or re.match(r"#{1,6} ", stripped):
        return None
    return expanded[indent:]


def _consume_list(lines: list[str], i: int, indent: int) -> tuple[tuple[bool, list], int]:
    """Parse a list. Each item is a sequence of paragraph line-groups and nested lists."""
    ordered: bool | None = None
    items: list[list] = []
    while i < len(lines):
        if not lines[i].strip():
            nxt = i + 1
            while nxt < len(lines) and not lines[nxt].strip():
                nxt += 1
            if nxt >= len(lines) or not items:
                break
            parsed = _list_item(lines[nxt])
            if parsed and parsed[0] == indent and (ordered is None or parsed[1] == ordered):
                i = nxt
                continue
            if _continuation(lines[nxt], indent) is not None:
                items[-1].append(["p", []])
                i = nxt
                continue
            break
        parsed = _list_item(lines[i])
        if parsed and parsed[0] == indent:
            item_indent, is_ordered, text = parsed
            del item_indent
            if ordered is None:
                ordered = is_ordered
            elif is_ordered != ordered:
                break
            items.append([["p", [text]]])
            i += 1
            continue
        if parsed and parsed[0] > indent and items:
            nested, i = _consume_list(lines, i, parsed[0])
            items[-1].append(["list", nested])
            continue
        continued = _continuation(lines[i], indent) if items else None
        if continued is not None:
            blocks = items[-1]
            if not blocks or blocks[-1][0] != "p":
                blocks.append(["p", []])
            blocks[-1][1].append(continued)
            i += 1
            continue
        break
    return (bool(ordered), items), i


def _render_paragraph(lines: list[str]) -> str:
    rendered: list[str] = []
    usable = [line for line in lines if line.strip()]
    for index, line in enumerate(usable):
        hard_break = line.endswith("  ")
        rendered.append(render_inline(line.strip()))
        if index != len(usable) - 1:
            rendered.append("<br>\n" if hard_break else "\n")
    return "<p>" + "".join(rendered) + "</p>"


def _render_list(node: tuple[bool, list]) -> str:
    ordered, items = node
    tag = "ol" if ordered else "ul"
    parts = [f"<{tag}>"]
    for blocks in items:
        chunks: list[str] = []
        for kind, payload in blocks:
            if kind == "p":
                if payload:
                    chunks.append(_render_paragraph(payload))
            else:
                chunks.append(_render_list(payload))
        parts.append("<li>" + "".join(chunks) + "</li>")
    parts.append(f"</{tag}>")
    return "".join(parts)


def _is_table(lines: list[str], i: int) -> bool:
    if i + 1 >= len(lines) or "|" not in lines[i]:
        return False
    return re.match(r"^\s*\|?\s*:?-{3,}[-\s:|]*$", lines[i + 1]) is not None


def _split_row(line: str) -> list[str]:
    stripped = line.strip().strip("|")
    return [cell.strip() for cell in stripped.split("|")]


def render_markdown(source: str) -> str:
    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    html: list[str] = []
    used_ids: set[str] = set()
    i = 0

    def block_start(line: str) -> bool:
        if not line.strip():
            return True
        if line.startswith("```") or line.startswith("~~~"):
            return True
        if re.match(r"#{1,6} ", line):
            return True
        if re.match(r"^(\s*)([-*+]|\d+\.)\s+", line):
            return True
        if line.lstrip().startswith(">"):
            return True
        if re.match(r"^(-{3,}|\*{3,}|_{3,})\s*$", line):
            return True
        return False

    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line.startswith("```") or line.startswith("~~~"):
            fence = line[:3]
            lang = re.sub(r"[^A-Za-z0-9_+-]", "", line[3:].strip()) or "fallback"
            i += 1
            buf: list[str] = []
            while i < len(lines) and not lines[i].startswith(fence):
                buf.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1
            code = hugo_escape("\n".join(buf))
            html.append(
                '<div class="highlight"><pre class="chroma">'
                f'<code class="language-{lang}" data-lang="{lang}">{code}</code>'
                "</pre></div>"
            )
            continue
        heading = re.match(r"(#{1,6}) +(.*)", line)
        if heading:
            level = len(heading.group(1))
            text = heading.group(2).strip()
            ident = heading_id(text, used_ids)
            html.append(f"<h{level} id=\"{ident}\">{render_inline(text)}</h{level}>")
            i += 1
            continue
        if re.match(r"^(-{3,}|\*{3,}|_{3,})\s*$", line):
            html.append("<hr>")
            i += 1
            continue
        if line.lstrip().startswith(">"):
            buf = []
            while i < len(lines) and (
                lines[i].lstrip().startswith(">") or (buf and lines[i].startswith("  "))
            ):
                buf.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            inner = render_markdown("\n".join(buf))
            html.append(f"<blockquote>\n{inner}</blockquote>")
            continue
        if _list_item(line):
            node, i = _consume_list(lines, i, _list_item(line)[0])
            html.append(_render_list(node))
            continue
        if _is_table(lines, i):
            header = _split_row(lines[i])
            i += 2
            body: list[list[str]] = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                body.append(_split_row(lines[i]))
                i += 1
            rows = ["<table>", "<thead>", "<tr>"]
            rows.extend(f"<th>{render_inline(cell)}</th>" for cell in header)
            rows.extend(["</tr>", "</thead>", "<tbody>"])
            for row in body:
                rows.append("<tr>")
                rows.extend(f"<td>{render_inline(cell)}</td>" for cell in row)
                rows.append("</tr>")
            rows.extend(["</tbody>", "</table>"])
            html.append("".join(rows))
            continue
        if line.lstrip().startswith("<"):
            buf = [line]
            i += 1
            while i < len(lines) and lines[i].strip() and not block_start(lines[i]):
                buf.append(lines[i])
                i += 1
            html.append("\n".join(buf))
            continue
        buf = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not block_start(lines[i]):
            buf.append(lines[i])
            i += 1
        html.append(_render_paragraph(buf))
    return "\n".join(html)


def mark_digest_meta(html: str) -> str:
    """Mark the opening time-window, coverage, and dated lines."""
    parts = re.split(r"(?=<h[1-6][\s>])", html, maxsplit=1)
    head = parts[0]
    tail = parts[1] if len(parts) > 1 else ""

    def repl(match: re.Match[str]) -> str:
        open_tag, inner = match.group(1), match.group(2)
        if "digest-meta" in open_tag:
            return match.group(0)
        if _META_START.match(plain_text(inner).lstrip()):
            return f'<p class="digest-meta">{inner}</p>'
        return match.group(0)

    head = re.sub(r"(<p\b[^>]*>)(.*?)(</p>)", repl, head, flags=re.S)
    return head + tail


def split_more(source: str) -> tuple[str | None, str]:
    if "<!--more-->" not in source:
        return None, source
    before, after = source.split("<!--more-->", 1)
    return before.strip(), (before.rstrip() + "\n" + after.lstrip()).strip()


def first_paragraph(html: str) -> str:
    match = re.search(r"<p\b[^>]*>.*?</p>", html, re.S)
    if match:
        return match.group(0)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return "<p>" + hugo_escape(text[:280]) + "</p>"


def _listing_summary(html: str) -> str:
    """Listing text chosen by ``<!--more-->``.

    One paragraph stays the first paragraph. Several paragraphs before the
    separator are all part of the homepage summary.
    """
    if len(re.findall(r"<p\b", html)) > 1:
        return html.strip()
    return first_paragraph(html)


class Post:
    """A post as given on the command line, before it is stored as a record."""

    def __init__(
        self,
        title: str,
        when: datetime,
        slug: str,
        categories: list[tuple[str, str]],
        tags: list[tuple[str, str]],
        body_html: str,
        summary_html: str,
        description: str,
        headline: str = "",
    ) -> None:
        self.title = title
        self.when = when
        self.slug = slug
        self.categories = categories
        self.tags = tags
        self.body_html = body_html
        self.summary_html = summary_html
        self.description = description
        self.headline = headline
        self.path = f"/post/{slug}/"
        self.abs_url = f"{SITE_BASE}{self.path}"

    def to_record(self) -> PostRecord:
        return PostRecord(
            slug=self.slug,
            title=self.title,
            when=self.when,
            categories=[name for name, _slug in self.categories],
            tags=[name for name, _slug in self.tags],
            body_html=self.body_html,
            summary_html=self.summary_html,
            description=self.description,
            headline=self.headline,
        )


# Kept for callers of the old names.
mark_learning_callouts = mark_callouts
render_daily_page = sitegen.render_daily_page


def _require_text(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PublishError(f"item {field} must be a non-empty string")
    return value.strip()


def _string_list(value, field: str, allowed: list[str] | None, minimum: int) -> list[str]:
    if not isinstance(value, list):
        raise PublishError(f"item {field} must be a list")
    out: list[str] = []
    for entry in value:
        if not isinstance(entry, str) or not entry.strip():
            raise PublishError(f"item {field} contains an empty value")
        name = entry.strip()
        if allowed is not None and name not in allowed:
            raise PublishError(f"item {field} has unknown value {name}")
        if name not in out:
            out.append(name)
    if len(out) < minimum:
        raise PublishError(f"item {field} needs at least {minimum} value")
    return out


def validate_item(raw) -> dict:
    """Return one digest item in canonical form, or raise PublishError."""
    if not isinstance(raw, dict):
        raise PublishError("each item must be an object")
    allowed = {
        "id",
        "date",
        "seq",
        "source_brief",
        "title",
        "summary",
        "insight",
        "relevance",
        "domains",
        "types",
        "tags",
        "source",
        "links",
        "archive_url",
        "posted_at",
    }
    extra = sorted(set(raw) - allowed)
    if extra:
        raise PublishError("unknown item fields: " + ", ".join(extra))
    ident = _require_text(raw.get("id"), "id")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", ident):
        raise PublishError(f"invalid item id {ident!r}")
    date_text = _require_text(raw.get("date"), "date")
    try:
        datetime.strptime(date_text, "%Y-%m-%d")
    except ValueError as exc:
        raise PublishError(
            f"item {ident} date must be YYYY-MM-DD in Asia/Shanghai"
        ) from exc
    if isinstance(raw.get("seq"), bool) or not isinstance(raw.get("seq"), int) or raw["seq"] < 1:
        raise PublishError(f"item {ident} seq must be a positive integer")
    brief = raw.get("source_brief")
    if brief not in SOURCE_BRIEFS:
        raise PublishError(f"item {ident} source_brief must be lumi or agent-app")
    title = _require_text(raw.get("title"), "title")
    summary = _require_text(raw.get("summary"), "summary")
    insight_raw = raw.get("insight", None)
    insight = None
    if insight_raw not in (None, "", {}):
        if not isinstance(insight_raw, dict):
            raise PublishError(f"item {ident} insight must be an object")
        heading = _require_text(insight_raw.get("heading"), "insight.heading")
        body = _require_text(insight_raw.get("body"), "insight.body")
        insight = {"heading": heading, "body": body}
    relevance = raw.get("relevance")
    if isinstance(relevance, bool) or not isinstance(relevance, int) or not 1 <= relevance <= 5:
        raise PublishError(f"item {ident} relevance must be an integer from 1 to 5")
    domains = _string_list(raw.get("domains"), "domains", DOMAINS, 1)
    types = _string_list(raw.get("types"), "types", TYPES, 1)
    tags = _string_list(raw.get("tags", []), "tags", None, 0)
    source_raw = raw.get("source")
    if not isinstance(source_raw, dict):
        raise PublishError(f"item {ident} source must be an object")
    source_allowed = {"name", "handle", "identity"}
    source_extra = sorted(set(source_raw) - source_allowed)
    if source_extra:
        raise PublishError(
            f"item {ident} source has unknown fields: " + ", ".join(source_extra)
        )
    source = {
        "name": str(source_raw.get("name") or "").strip(),
        "handle": str(source_raw.get("handle") or "").strip(),
        "identity": str(source_raw.get("identity") or "").strip(),
    }
    if not source["name"] and not source["handle"]:
        raise PublishError(f"item {ident} source needs a name or a handle")
    links_raw = raw.get("links", [])
    if not isinstance(links_raw, list):
        raise PublishError(f"item {ident} links must be a list")
    links = []
    for link in links_raw:
        if not isinstance(link, dict):
            raise PublishError(f"item {ident} link must be an object")
        label = _require_text(link.get("label"), "links.label")
        url = _require_text(link.get("url"), "links.url")
        if not (url.startswith("https://") or url.startswith("http://") or url.startswith("/")):
            raise PublishError(f"item {ident} link url must be http(s) or a site path")
        if url.startswith("//"):
            raise PublishError(f"item {ident} link url is not a site path")
        links.append({"label": label, "url": url})
    archive = _require_text(raw.get("archive_url"), "archive_url")
    if not archive.startswith("/") or archive.startswith("//"):
        raise PublishError(f"item {ident} archive_url must be a site path")
    posted = raw.get("posted_at", None)
    if posted is None:
        posted_at = ""
    elif isinstance(posted, str):
        posted_at = posted.strip()
    else:
        raise PublishError(f"item {ident} posted_at must be a string")
    return {
        "id": ident,
        "date": date_text,
        "seq": raw["seq"],
        "source_brief": brief,
        "title": title,
        "summary": summary,
        "insight": insight,
        "relevance": relevance,
        "domains": domains,
        "types": types,
        "tags": tags,
        "source": source,
        "links": links,
        "archive_url": archive,
        "posted_at": posted_at,
    }


def sort_items(items: list[dict]) -> list[dict]:
    """Newest date first. Within a day, smaller seq comes first."""

    def key(item: dict) -> tuple:
        ordinal = datetime.strptime(item["date"], "%Y-%m-%d").toordinal()
        return (-ordinal, item["seq"], item["id"])

    return sorted(items, key=key)


def day_ordinals(items: list[dict]) -> dict[str, int]:
    """1-based position of each item inside its calendar day, ignoring filters."""
    by_day: dict[str, list[dict]] = {}
    for item in items:
        by_day.setdefault(item["date"], []).append(item)
    ordinals: dict[str, int] = {}
    for rows in by_day.values():
        ordered = sorted(rows, key=lambda item: (item["seq"], item["id"]))
        for index, item in enumerate(ordered, start=1):
            ordinals[item["id"]] = index
    return ordinals


def upsert_items(existing: list, incoming: list) -> list[dict]:
    """Insert or replace items by id. Existing ids not in incoming stay put."""
    if not isinstance(existing, list) or not isinstance(incoming, list):
        raise PublishError("items must be a list")
    current = [validate_item(item) for item in existing]
    by_id = {item["id"]: item for item in current}
    if len(by_id) != len(current):
        raise PublishError("existing items contain a duplicate id")
    seen: set[str] = set()
    max_seq = max((item["seq"] for item in current), default=0)
    prepared: list[dict] = []
    for raw in incoming:
        if not isinstance(raw, dict):
            raise PublishError("each item must be an object")
        ident = str(raw.get("id", "")).strip()
        if ident in seen:
            raise PublishError(f"duplicate item id {ident}")
        seen.add(ident)
        row = dict(raw)
        if row.get("seq") in (None, ""):
            if ident in by_id:
                row["seq"] = by_id[ident]["seq"]
            else:
                max_seq += 1
                row["seq"] = max_seq
        prepared.append(validate_item(row))
    incoming_ids = {item["id"] for item in prepared}
    merged: list[dict] = []
    replaced: set[str] = set()
    for item in current:
        if item["id"] in incoming_ids:
            merged.append(next(row for row in prepared if row["id"] == item["id"]))
            replaced.add(item["id"])
        else:
            merged.append(item)
    for item in prepared:
        if item["id"] not in replaced:
            merged.append(item)
    return sort_items(merged)


def load_items_document(path: Path) -> list:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "items" in data:
        if not isinstance(data["items"], list):
            raise PublishError("items must be a list")
        return data["items"]
    raise PublishError("items JSON must be a list or an object with an items array")


def _source_line(source: dict) -> str:
    handle = source["handle"]
    name = source["name"]
    identity = source["identity"]
    if handle:
        shown = handle if handle.startswith("@") else "@" + handle
    else:
        shown = name
    parts = ["来源 " + shown]
    if identity:
        parts.append(identity)
    elif name and handle:
        parts.append(name)
    return " · ".join(parts)


def _relevance_markup(score: int) -> str:
    color = RELEVANCE_COLORS[score - 1]
    bars = []
    for index in range(1, 6):
        fill = color if index <= score else "#e6e1d8"
        bars.append(
            f'<i style="background:{fill}" data-score="{index}"></i>'
        )
    return (
        f'<p class="relevance" data-relevance="{score}">'
        f'<span class="relevance-label">相关度</span>'
        f'<span class="relevance-bars">{"".join(bars)}</span>'
        f'<span class="relevance-score">{score}</span>'
        "</p>"
    )


def render_item_card(item: dict, ordinal: int) -> str:
    """Render one card. Visible field order is part of the feed contract."""
    item = validate_item(item)
    title_href = item["links"][0]["url"] if item["links"] else item["archive_url"]
    title = hugo_escape(item["title"])
    brief = "Lumi" if item["source_brief"] == "lumi" else "agent-app"
    parts = [
        f'<article class="card" data-id="{hugo_escape(item["id"])}" data-relevance="{item["relevance"]}">',
        '<p class="card-date">',
        f'<time datetime="{item["date"]}">{hugo_escape(item["date"])}</time>',
        f'<span class="brief">{brief}</span>',
        f'<span class="ordinal">当天日报第 {ordinal} 条</span>',
        "</p>",
        _relevance_markup(item["relevance"]),
        f'<h3 class="card-title"><a href="{hugo_escape(title_href)}">{title}</a></h3>',
        f'<p class="card-summary">{hugo_escape(item["summary"])}</p>',
    ]
    insight = item["insight"]
    if insight:
        parts.append(
            '<blockquote class="insight">'
            f'<p class="insight-heading">{hugo_escape(insight["heading"])}</p>'
            f'<p>{hugo_escape(insight["body"])}</p>'
            "</blockquote>"
        )
    if item["tags"]:
        pills = "".join(f"<li>{hugo_escape(tag)}</li>" for tag in item["tags"])
        parts.append(f'<ul class="tags">{pills}</ul>')
    parts.append(f'<p class="source">{hugo_escape(_source_line(item["source"]))}</p>')
    link_rows = [
        f'<li><a href="{hugo_escape(link["url"])}">{hugo_escape(link["label"])}</a></li>'
        for link in item["links"]
    ]
    if item["archive_url"] and all(link["url"] != item["archive_url"] for link in item["links"]):
        link_rows.append(
            f'<li><a href="{hugo_escape(item["archive_url"])}">归档</a></li>'
        )
    if link_rows:
        parts.append(f'<ul class="card-links">{"".join(link_rows)}</ul>')
    posted = item["posted_at"]
    if posted and posted != item["date"]:
        parts.append(f'<p class="posted">发帖日期 {hugo_escape(posted)}</p>')
    parts.append("</article>")
    return "".join(parts)


def item_search_text(item: dict) -> str:
    parts = [item["title"], item["summary"]]
    if item["insight"]:
        parts.append(item["insight"]["heading"])
        parts.append(item["insight"]["body"])
    source = item["source"]
    parts.extend([source["name"], source["handle"], source["identity"]])
    return "\n".join(parts).casefold()


def item_matches(
    item: dict,
    *,
    query: str = "",
    match: str = "all",
    min_relevance: int = 0,
    domains: list[str] | None = None,
    types: list[str] | None = None,
    tags: list[str] | None = None,
    date: str = "",
) -> bool:
    """Whether one item passes the feed filters.

    全部满足 requires every selected domain, type, and tag.
    任一满足 requires at least one selected domain, type, or tag.
    Relevance, date, and search always apply on top of that.
    """
    if match not in ("all", "any"):
        raise PublishError("match must be all or any")
    domains = list(domains or [])
    types = list(types or [])
    tags = list(tags or [])
    if min_relevance and item["relevance"] < min_relevance:
        return False
    if date and item["date"] != date:
        return False
    needle = query.strip().casefold()
    if needle and needle not in item_search_text(item):
        return False
    if not domains and not types and not tags:
        return True
    domain_hits = [name in item["domains"] for name in domains]
    type_hits = [name in item["types"] for name in types]
    tag_hits = [name in item["tags"] for name in tags]
    hits = domain_hits + type_hits + tag_hits
    if match == "any":
        return any(hits)
    return all(hits)


def filter_items(items: list[dict], **kwargs) -> list[dict]:
    return [item for item in items if item_matches(item, **kwargs)]


def _read_existing_items(root: Path) -> list:
    path = root / DAILY_ITEMS_REL
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise PublishError("daily/items.json must be a list")
    return data


class SiteWriter:
    """Writes only files whose text changed, and records what it touched."""

    def __init__(self, root: Path, dry_run: bool) -> None:
        self.root = root
        self.dry_run = dry_run
        self.changed: list[str] = []
        self.notes: list[str] = []

    def _mark(self, relative: str) -> None:
        if relative not in self.changed:
            self.changed.append(relative)

    def write(self, relative: str, text: str) -> None:
        path = self.root / relative
        existing = path.read_text(encoding="utf-8") if path.is_file() else None
        if existing == text:
            return
        self._mark(relative)
        if self.dry_run:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")

    def remove_file(self, relative: str) -> None:
        path = self.root / relative
        if not path.is_file():
            return
        self._mark(relative + " (removed)")
        if not self.dry_run:
            path.unlink()

    def remove_dir(self, path: Path) -> None:
        relative = path.relative_to(self.root).as_posix() + "/"
        self._mark(relative + " (removed)")
        if not self.dry_run:
            shutil.rmtree(path)

    def read(self, relative: str) -> str:
        path = self.root / relative
        if not path.is_file():
            raise PublishError(f"missing {relative}")
        return path.read_text(encoding="utf-8")


def load_records(root: Path) -> list[PostRecord]:
    return sitegen.load_records(root / POSTS_REL)


def rebuild(root: Path, writer: SiteWriter, records: list[PostRecord], items: list | None = None) -> None:
    """Write every generated page from the records and remove stale pages."""
    for relative in ("daily/feed.css", "daily/feed.js"):
        if not (root / relative).is_file():
            raise PublishError(f"missing {relative}")
    rows = _read_existing_items(root) if items is None else items
    dates = [row["date"] for row in rows if isinstance(row, dict) and isinstance(row.get("date"), str)]
    files = sitegen.build_site(root, records, dates)
    for relative, text in files.items():
        writer.write(relative, text)
    removable, kept = sitegen.stale_dirs(root, files)
    for path in removable:
        writer.remove_dir(path)
    for path in kept:
        relative = path.relative_to(root).as_posix()
        note = f"left {relative}/ in place: it holds files the publisher did not write"
        writer.notes.append(note)
        print(f"warning: {note}", file=sys.stderr)


def publish_digest(root: Path, items: list, dry_run: bool = False) -> list[str]:
    """Upsert digest items, then rebuild the site (the /daily/ page and sitemap)."""
    merged = upsert_items(_read_existing_items(root), items)
    writer = SiteWriter(root, dry_run)
    payload = json.dumps(merged, ensure_ascii=False, indent=2) + "\n"
    writer.write(DAILY_ITEMS_REL, payload)
    rebuild(root, writer, load_records(root), merged)
    return writer.changed


def _wants_post(args: argparse.Namespace, payload: dict) -> bool:
    if args.title or args.slug or args.body is not None or args.body_file or args.categories:
        return True
    return any(payload.get(key) for key in ("title", "slug", "body", "body_file"))


def publish(root: Path, post: Post, dry_run: bool = False) -> list[str]:
    """Store the post's record (replacing one with the same slug) and rebuild."""
    writer = SiteWriter(root, dry_run)
    records = {record.slug: record for record in load_records(root)}
    record = post.to_record()
    records[record.slug] = record
    writer.write(f"{POSTS_REL}/{record.slug}.json", record.to_json())
    rebuild(root, writer, list(records.values()))
    return writer.changed


def remove_post(root: Path, slug: str, dry_run: bool = False) -> list[str]:
    """Delete a post's record and rebuild; its page is removed with the stale pages."""
    slug = validate_slug(slug)
    relative = f"{POSTS_REL}/{slug}.json"
    if not (root / relative).is_file():
        raise PublishError(f"no post record {relative}")
    writer = SiteWriter(root, dry_run)
    writer.remove_file(relative)
    records = [record for record in load_records(root) if record.slug != slug]
    rebuild(root, writer, records)
    return writer.changed


def rebuild_only(root: Path, dry_run: bool = False) -> list[str]:
    writer = SiteWriter(root, dry_run)
    rebuild(root, writer, load_records(root))
    return writer.changed


def _as_terms(value, flag: str) -> list[tuple[str, str]]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    terms = []
    for item in value:
        name = str(item).strip()
        if not name:
            continue
        terms.append((name, urlize(name)))
    if not terms and flag == "category":
        raise PublishError("at least one --category is required")
    return terms


def load_post(args: argparse.Namespace) -> Post:
    payload: dict = {}
    if args.input:
        payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise PublishError("--input JSON must be an object")
    title = args.title or payload.get("title")
    when_raw = args.date or payload.get("date")
    slug = args.slug or payload.get("slug")
    if not title or not str(title).strip():
        raise PublishError("a title is required")
    if not when_raw:
        raise PublishError("a date is required")
    if not slug:
        raise PublishError("a slug is required")
    categories = _as_terms(
        args.categories if args.categories is not None else payload.get("categories", payload.get("category")),
        "category",
    )
    if not categories:
        raise PublishError("at least one category is required")
    tags = _as_terms(
        args.tags if args.tags is not None else payload.get("tags", payload.get("tag")),
        "tag",
    )
    fmt = (args.format or payload.get("format") or "markdown").lower()
    if fmt not in ("markdown", "html"):
        raise PublishError("--format must be markdown or html")
    body = args.body if args.body is not None else payload.get("body")
    body_file = args.body_file or payload.get("body_file")
    if body is not None and body_file:
        raise PublishError("pass only one of --body and --body-file")
    if body_file:
        if body_file == "-":
            body = sys.stdin.read()
        else:
            body = Path(body_file).read_text(encoding="utf-8")
    if body is None or not str(body).strip():
        raise PublishError("a markdown or HTML body is required")
    summary_override = args.summary if args.summary is not None else payload.get("summary")
    description_override = args.description if args.description is not None else payload.get("description")
    before, full = split_more(str(body))
    if fmt == "html":
        body_html = full.strip()
        summary_source = (before or "").strip()
        summary_html = _listing_summary(summary_source) if summary_source else first_paragraph(body_html)
    else:
        body_html = render_markdown(full)
        if before:
            summary_html = _listing_summary(render_markdown(before).strip())
        else:
            summary_html = first_paragraph(body_html)
    body_html = mark_digest_meta(body_html)
    if summary_override:
        summary_html = "<p>" + hugo_escape(str(summary_override).strip()) + "</p>"
    if description_override:
        description = re.sub(r"\s+", " ", str(description_override)).strip()
    else:
        description = plain_text(summary_html)[:180]
    headline = args.headline if args.headline is not None else payload.get("headline")
    headline = re.sub(r"\s+", " ", str(headline or "")).strip()
    return Post(
        title=str(title).strip(),
        when=parse_when(str(when_raw)),
        slug=validate_slug(str(slug)),
        categories=categories,
        tags=tags,
        body_html=body_html,
        summary_html=summary_html,
        description=description,
        headline=headline,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="publish.py",
        description=(
            "Publish one post, a list of daily-digest items, or both. "
            "A date without a time is 08:00 in Asia/Shanghai. "
            "Publishing the same slug or item id again replaces it instead of duplicating it."
        ),
    )
    parser.add_argument("--root", help="site root (default: repository root)")
    parser.add_argument(
        "--input",
        help="JSON object for a post, an items array, or an object with both",
    )
    parser.add_argument(
        "--items",
        help="JSON list of digest items, or an object with an items array",
    )
    parser.add_argument("--title")
    parser.add_argument("--date", help="YYYY-MM-DD or a datetime; naive values are Asia/Shanghai")
    parser.add_argument("--slug", help="URL path under /post/, such as lumi-2026-11-17")
    parser.add_argument("--category", action="append", dest="categories", help="repeat for more than one")
    parser.add_argument("--tag", action="append", dest="tags", help="repeat for more than one")
    parser.add_argument("--body", help="markdown or HTML source")
    parser.add_argument("--body-file", help="file containing the body, or - for stdin")
    parser.add_argument("--format", choices=["markdown", "html"], help="default: markdown")
    parser.add_argument("--summary", help="plain-text listing summary; default is the first paragraph")
    parser.add_argument("--description", help="meta description; default is the plain summary")
    parser.add_argument(
        "--headline",
        help="optional one-line headline for the homepage card, such as the day's main news",
    )
    parser.add_argument("--remove", metavar="SLUG", help="delete the post with this slug and rebuild")
    parser.add_argument("--rebuild", action="store_true", help="regenerate every page from the stored records")
    parser.add_argument("--dry-run", action="store_true", help="report files that would change and write nothing")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = Path(args.root).resolve() if args.root else HERE.parent
    if not (root / "daily" / "feed.js").is_file():
        raise PublishError(f"{root} does not look like the site root (no daily/feed.js)")
    action = "would update" if args.dry_run else "updated"
    if args.remove or args.rebuild:
        if args.remove and args.rebuild:
            raise PublishError("pass only one of --remove and --rebuild")
        if args.remove:
            changed = remove_post(root, args.remove, dry_run=args.dry_run)
            print(f"removed /post/{validate_slug(args.remove)}/")
        else:
            changed = rebuild_only(root, dry_run=args.dry_run)
            print("rebuilt the site")
        _report(changed, action)
        return 0
    payload: dict = {}
    item_rows = None
    if args.input:
        raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
        if isinstance(raw, list):
            payload = {"items": raw}
        elif isinstance(raw, dict):
            payload = raw
        else:
            raise PublishError("--input JSON must be an object or a list of items")
    if args.items:
        if payload.get("items"):
            raise PublishError("pass items only once")
        item_rows = load_items_document(Path(args.items))
    elif "items" in payload:
        item_rows = payload["items"]
        if not isinstance(item_rows, list):
            raise PublishError("items must be a list")
    wants_post = _wants_post(args, payload)
    if not wants_post and item_rows is None:
        raise PublishError("a post body or an items list is required")
    changed: list[str] = []
    published_post = None
    if wants_post:
        published_post = load_post(args)
        changed.extend(publish(root, published_post, dry_run=args.dry_run))
    if item_rows is not None:
        changed.extend(publish_digest(root, item_rows, dry_run=args.dry_run))
    if published_post is not None:
        print(f"published {published_post.path}")
    if item_rows is not None:
        print("published /daily/")
    _report(changed, action)
    return 0


def _report(changed: list[str], action: str) -> None:
    seen: set[str] = set()
    unique = []
    for relative in changed:
        if relative in seen:
            continue
        seen.add(relative)
        unique.append(relative)
    if not unique:
        print("no file changes")
        return
    for relative in unique:
        if relative.endswith(" (removed)"):
            verb = "would remove" if action.startswith("would") else "removed"
            print(f"{verb} {relative[: -len(' (removed)')]}")
        else:
            print(f"{action} {relative}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PublishError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
