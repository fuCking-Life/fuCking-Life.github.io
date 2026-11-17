#!/usr/bin/env python3
"""Insert posts and daily-digest items into this already-built Anatole site.

The site on master is compiled HTML. This script does not run Hugo. It writes
a new ``post/<slug>/index.html`` from the existing single-post chrome and
splices that post into the homepage, the posts archive, RSS, the sitemap, and
the category and tag pages. A JSON item list upserts ``daily/items.json`` and
regenerates the ``/daily/`` feed.

Run ``python3 _publisher/publish.py --help`` for the arguments. See
``_publisher/README.md`` for the files that change and the known limits.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SITE_BASE = "https://fuCking-Life.github.io"
TZ = ZoneInfo("Asia/Shanghai")
DIGEST_TYPOGRAPHY_CSS = """
.post .post-content { font-size: 15px; }
.post .post-content p { line-height: 1.85; margin: 0.7em 0 1em; }
.post .post-content li p { margin: 0.25em 0 0.6em; }
.post .post-content h2 { font-size: 20px; font-weight: 650; margin: 2em 0 0.8em; padding-top: 0.4em; border-top: 1px solid #eee; }
.post .post-content h2:first-of-type { border-top: 0; margin-top: 1.2em; padding-top: 0; }
.post .post-content h3 { font-size: 17px; font-weight: 600; margin: 1.6em 0 0.6em; line-height: 1.45; }
.post .post-content ol, .post .post-content ul { padding-left: 1.3em; margin: 0.4em 0 1em; }
.post .post-content li { margin: 0.45em 0 0.9em; }
.post .post-content a { color: #2f69b3; }
.post .post-content blockquote {
  border-left: 4px solid #3b6fd8;
  background: #f6f8fc;
  color: #334;
  padding: 0.7em 1em;
  margin: 0.9em 0 1.1em;
  border-radius: 0 6px 6px 0;
}
.post .post-content blockquote p { margin: 0.35em 0; color: #334; line-height: 1.75; }
.post .post-content .digest-meta {
  color: #888;
  font-size: 13px;
  line-height: 1.7;
  margin: 0.2em 0 0.4em;
}
.post .post-content hr { border: 0; border-top: 1px solid #eee; margin: 2em 0 1em; }
""".strip()
_META_START = re.compile(r"^(?:时间窗口|覆盖范围|\d{4}年)")
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTHS = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]
MONTH_INDEX = {name: i + 1 for i, name in enumerate(MONTHS)}

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
SINGLE_TEMPLATE = HERE / "templates" / "single.html"
DAILY_TEMPLATE = HERE / "templates" / "daily.html"

SHORT_TAIL = "\n                \n            \n"
LONG_TAIL = SHORT_TAIL + "        "

CARD_RE = re.compile(
    r"        <div class=\"post  animated fadeInDown \">\n"
    r"            \n"
    r"            <div class=\"post-title\">.*?"
    r"\n            </div>\n        </div>",
    re.S,
)
ENTRY_RE = re.compile(r"                    <ul class=\"listing\">.*?</ul>", re.S)
YEAR_RE = re.compile(r"[ \t]*<div class=\"listing-title\">(\d{4})</div>")
ITEM_RE = re.compile(r"    <item>\n.*?\n    </item>", re.S)
DATE_LONG_RE = re.compile(
    r'<span class="date">\s*([A-Za-z]{3}),\s+([A-Za-z]{3})\s+(\d{1,2}),\s+(\d{4})'
)
DATE_SHORT_RE = re.compile(
    r'<span class="date">\s*([A-Za-z]{3})\s+(\d{1,2})\s*</span>'
)


class PublishError(SystemExit):
    def __init__(self, message: str) -> None:
        super().__init__(message)


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


def xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&#34;")
        .replace("'", "&#39;")
    )


def urlize(name: str) -> str:
    """Hugo-like path slug: lowercase, unicode letters kept, other runs become '-'."""
    out: list[str] = []
    for ch in name.strip().lower():
        if ch.isalnum() or ch in "-_":
            out.append("-" if ch == "_" else ch)
        else:
            out.append("-")
    slug = re.sub(r"-{2,}", "-", "".join(out)).strip("-")
    if not slug:
        raise PublishError(f"category or tag {name!r} has no URL slug")
    return slug


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


def html_long_date(dt: datetime) -> str:
    return f"{WEEKDAYS[dt.weekday()]}, {MONTHS[dt.month - 1]} {dt.day}, {dt.year}"


def html_short_date(dt: datetime) -> str:
    return f"{MONTHS[dt.month - 1]} {dt.day}"


def rss_date(dt: datetime) -> str:
    aware = dt.astimezone(TZ)
    return (
        f"{WEEKDAYS[aware.weekday()]}, {aware.day:02d} {MONTHS[aware.month - 1]} "
        f"{aware.year} {aware.hour:02d}:{aware.minute:02d}:{aware.second:02d} +0800"
    )


def sitemap_date(dt: datetime) -> str:
    aware = dt.astimezone(TZ)
    return aware.strftime("%Y-%m-%dT%H:%M:%S+08:00")


def parse_rss_date(value: str) -> datetime:
    match = re.match(
        r"\w+, (\d{2}) (\w{3}) (\d{4}) (\d{2}):(\d{2}):(\d{2}) ([+-]\d{4})",
        value.strip(),
    )
    if not match:
        raise PublishError(f"unrecognized RSS date {value!r}")
    sign = 1 if match.group(7)[0] == "+" else -1
    hours = int(match.group(7)[1:3])
    minutes = int(match.group(7)[3:5])
    tz = timezone(sign * timedelta(hours=hours, minutes=minutes))
    return datetime(
        int(match.group(3)),
        MONTH_INDEX[match.group(2)],
        int(match.group(1)),
        int(match.group(4)),
        int(match.group(5)),
        int(match.group(6)),
        tzinfo=tz,
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
    match = re.search(r"<p>.*?</p>", html, re.S)
    if match:
        return match.group(0)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return "<p>" + hugo_escape(text[:280]) + "</p>"


def plain_text(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    text = (
        text.replace("&#43;", "+")
        .replace("&#34;", '"')
        .replace("&#39;", "'")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
    )
    return re.sub(r"\s+", " ", text).strip()


def word_count(html: str) -> int:
    return len(plain_text(html).split())


def reading_label(html: str) -> str:
    count = word_count(html)
    fuzzy = 100 if count < 100 else int(round(count / 100.0) * 100)
    minutes = max(1, math.ceil(count / 213)) if count else 1
    return f"{fuzzy} Words|Read in about {minutes} Min"


class Post:
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
    ) -> None:
        self.title = title
        self.title_html = hugo_escape(title)
        self.when = when
        self.slug = slug
        self.categories = categories
        self.tags = tags
        self.body_html = body_html
        self.summary_html = summary_html
        self.description = description
        self.description_html = hugo_escape(description)
        self.path = f"/post/{slug}/"
        self.abs_url = f"{SITE_BASE}{self.path}"
        self.reading = reading_label(body_html)

    @property
    def footer_html(self) -> str:
        parts: list[str] = []
        if self.categories:
            anchors = "".join(
                f'<a class="category" href="/categories/{slug}/">{hugo_escape(name)}</a>'
                for name, slug in self.categories
            )
            parts.append(f'<span class="separator">{anchors}</span>')
        if self.tags:
            anchors = "".join(
                f'<a class="tag" href="/tags/{slug}/">{hugo_escape(name)}</a>'
                for name, slug in self.tags
            )
            parts.append(f'<span class="separator">{anchors}</span>')
        return "\n".join("                " + part for part in parts)


def render_single(post: Post) -> str:
    if not SINGLE_TEMPLATE.is_file():
        raise PublishError(f"missing template {SINGLE_TEMPLATE}")
    html = SINGLE_TEMPLATE.read_text(encoding="utf-8")
    html = html.replace("@@TITLE@@", post.title_html)
    html = html.replace("@@DESCRIPTION@@", post.description_html)
    html = html.replace("@@CANONICAL@@", post.path)
    html = html.replace("@@TWITTER_TITLE@@", post.title_html)
    html = html.replace("@@TWITTER_DESCRIPTION@@", post.description_html)
    html = html.replace("@@DATE@@", html_long_date(post.when))
    html = html.replace("@@READING@@", post.reading)
    html = html.replace("@@FOOTER@@", post.footer_html)
    if "@@" in html.replace("@@CONTENT@@", ""):
        raise PublishError("single-post template has an unfilled placeholder")
    if "</head>" not in html:
        raise PublishError("post template is missing </head>")
    html = html.replace(
        "</head>",
        "<style>\n" + DIGEST_TYPOGRAPHY_CSS + "\n</style>\n</head>",
        1,
    )
    content = post.body_html.strip("\n")
    html = html.replace("@@CONTENT@@", content)
    return html


def render_entry(href: str, title_html: str, when: datetime) -> str:
    short = html_short_date(when)
    return (
        "                    <ul class=\"listing\">\n"
        "                        <div class=\"listing-item\">\n"
        "                            <div class=\"listing-post\">"
        f'<a href="{href}" title="{title_html}">{title_html}</a>\n'
        "                                <div class=\"post-time\">"
        f'<span class="date"> {short} \n'
        "                                                                            </span></div>\n"
        "                            </div>\n"
        "                        </div>\n"
        "                    </ul>"
    )


def render_card(post: Post) -> str:
    cats = "".join(
        f'<a class="category" href="/categories/{slug}/">{hugo_escape(name)}</a>'
        for name, slug in post.categories
    )
    date_long = html_long_date(post.when)
    return (
        "        <div class=\"post  animated fadeInDown \">\n"
        "            \n"
        "            <div class=\"post-title\">\n"
        f'                <h3><a href="{post.path}">{post.title_html}</a>\n'
        "                </h3>\n"
        "            </div>\n"
        "            <div class=\"post-content\">\n"
        "                <div class=\"p_part\">\n"
        f"                    <p>{post.summary_html}</p>\n"
        "                    </div>\n"
        "            </div>\n"
        "\n"
        "            \n"
        "            <div class=\"post-footer\">\n"
        "                <div class=\"meta\">\n"
        "                    <div class=\"info\">\n"
        "                        <em class=\"fas fa-calendar-day\"></em>\n"
        f'                            <span class="date"> {date_long}\n'
        "                                             </span>\n"
        f'                        <span class="separator">{cats}</span>\n'
        "                        \n"
        "                    </div>\n"
        "                </div>\n"
        "            </div>\n"
        "        </div>"
    )


def find_archive(html: str) -> tuple[int, int]:
    open_tag = '<ul class="list-with-title">'
    start = html.find(open_tag)
    if start < 0:
        raise PublishError("archive listing not found")
    inner = start + len(open_tag)
    i = inner
    depth = 1
    while i < len(html):
        if html.startswith("<ul", i):
            depth += 1
            i += 3
            continue
        if html.startswith("</ul>", i):
            depth -= 1
            if depth == 0:
                return inner, i
            i += 5
            continue
        i += 1
    raise PublishError("archive listing is not closed")


def split_years(inner: str) -> tuple[str, list[tuple[int, str]]]:
    matches = list(YEAR_RE.finditer(inner))
    if not matches:
        return "\n            \n", []
    prefix = inner[: matches[0].start()]
    years: list[tuple[int, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(inner)
        years.append((int(match.group(1)), inner[match.start() : end]))
    return prefix, years


def split_tail(chunk: str) -> tuple[str, str]:
    if chunk.endswith(LONG_TAIL):
        return chunk[: -len(LONG_TAIL)], LONG_TAIL
    if chunk.endswith(SHORT_TAIL):
        return chunk[: -len(SHORT_TAIL)], SHORT_TAIL
    raise PublishError("unrecognized year block in an archive page")


def entry_href(entry: str) -> str:
    match = re.search(r'<a href="([^"]*)"', entry)
    if not match:
        raise PublishError("listing entry is missing a link")
    return match.group(1)


def entry_month_day(entry: str) -> tuple[int, int]:
    match = DATE_SHORT_RE.search(entry)
    if not match:
        raise PublishError("listing entry is missing a date")
    return MONTH_INDEX[match.group(1)], int(match.group(2))


def render_year(year: int, entries: list[str], tail: str) -> str:
    joined = "\n                \n".join(entries)
    return (
        f'                <div class="listing-title">{year}</div>\n'
        f"                \n"
        f"{joined}"
        f"{tail}"
    )


def _place_entry(entries: list[str], new_entry: str, when: datetime) -> list[str]:
    key = (when.month, when.day)
    href = entry_href(new_entry)
    kept = [entry for entry in entries if entry_href(entry) != href]
    placed = False
    out: list[str] = []
    for entry in kept:
        if not placed and entry_month_day(entry) <= key:
            out.append(new_entry)
            placed = True
        out.append(entry)
    if not placed:
        out.append(new_entry)
    return out


def upsert_archive_inner(inner: str, href: str, title_html: str, when: datetime) -> str:
    prefix, years = split_years(inner)
    cleaned: list[tuple[int, str]] = []
    for year, chunk in years:
        head, tail = split_tail(chunk)
        found = ENTRY_RE.findall(head)
        kept = [entry for entry in found if entry_href(entry) != href]
        if len(kept) == len(found):
            cleaned.append((year, chunk))
        elif kept:
            cleaned.append((year, render_year(year, kept, tail)))
    entry = render_entry(href, title_html, when)
    index = next((i for i, (year, _) in enumerate(cleaned) if year == when.year), None)
    if index is None:
        insert_at = 0
        while insert_at < len(cleaned) and cleaned[insert_at][0] > when.year:
            insert_at += 1
        is_last = insert_at == len(cleaned)
        if is_last and cleaned and cleaned[-1][1].endswith(LONG_TAIL):
            year, chunk = cleaned[-1]
            cleaned[-1] = (year, chunk[: -8])
        tail = LONG_TAIL if is_last else SHORT_TAIL
        cleaned.insert(insert_at, (when.year, render_year(when.year, [entry], tail)))
    else:
        year, chunk = cleaned[index]
        head, tail = split_tail(chunk)
        entries = _place_entry(ENTRY_RE.findall(head), entry, when)
        cleaned[index] = (year, render_year(year, entries, tail))
    if cleaned and not cleaned[-1][1].endswith("\n        "):
        year, chunk = cleaned[-1]
        cleaned[-1] = (year, chunk + "        ")
    return prefix + "".join(chunk for _, chunk in cleaned)


def replace_archive(html: str, href: str, title_html: str, when: datetime) -> str:
    start, end = find_archive(html)
    inner = upsert_archive_inner(html[start:end], href, title_html, when)
    return html[:start] + inner + html[end:]


def remove_archive_href(html: str, href: str) -> str:
    if href not in html:
        return html
    start, end = find_archive(html)
    inner = html[start:end]
    if href not in inner:
        return html
    prefix, years = split_years(inner)
    kept_years: list[tuple[int, str]] = []
    changed = False
    for year, chunk in years:
        if href not in chunk:
            kept_years.append((year, chunk))
            continue
        changed = True
        head, tail = split_tail(chunk)
        kept = [entry for entry in ENTRY_RE.findall(head) if entry_href(entry) != href]
        if kept:
            kept_years.append((year, render_year(year, kept, tail)))
    if not changed:
        return html
    if kept_years and not kept_years[-1][1].endswith("\n        "):
        year, chunk = kept_years[-1]
        kept_years[-1] = (year, chunk + "        ")
    new_inner = prefix + "".join(chunk for _, chunk in kept_years)
    if not kept_years:
        new_inner = "\n            \n        "
    return html[:start] + new_inner + html[end:]


def card_sort_key(card: str) -> tuple[int, int, int]:
    match = DATE_LONG_RE.search(card)
    if not match:
        raise PublishError("homepage card is missing a date")
    month = MONTH_INDEX[match.group(2)]
    return int(match.group(4)), month, int(match.group(3))


def card_href(card: str) -> str:
    match = re.search(r'<a href="([^"]+)"', card)
    if not match:
        raise PublishError("homepage card is missing a link")
    return match.group(1)


def replace_homepage(html: str, post: Post) -> str:
    matches = list(CARD_RE.finditer(html))
    if not matches:
        raise PublishError("homepage post list was not found")
    gaps = [html[a.end() : b.start()] for a, b in zip(matches, matches[1:])]
    gap = gaps[0] if gaps else "\n    \n\n"
    if any(item != gap for item in gaps):
        raise PublishError("homepage cards are not separated consistently")
    cards = [match.group(0) for match in matches]
    cards = [card for card in cards if card_href(card) != post.path]
    new_card = render_card(post)
    key = (post.when.year, post.when.month, post.when.day)
    placed = False
    out: list[str] = []
    for card in cards:
        if not placed and card_sort_key(card) <= key:
            out.append(new_card)
            placed = True
        out.append(card)
    if not placed:
        out.append(new_card)
    rebuilt = gap.join(out)
    return html[: matches[0].start()] + rebuilt + html[matches[-1].end() :]


def rss_item(title: str, link: str, when: datetime, description: str) -> str:
    return (
        "    <item>\n"
        f"      <title>{xml_escape(title)}</title>\n"
        f"      <link>{link}</link>\n"
        f"      <pubDate>{rss_date(when)}</pubDate>\n"
        "      \n"
        f"      <guid>{link}</guid>\n"
        f"      <description>{description}</description>\n"
        "    </item>"
    )


def remove_rss_item(xml: str, link: str) -> str:
    for match in ITEM_RE.finditer(xml):
        block = match.group(0)
        if f"<link>{link}</link>" not in block and f"<guid>{link}</guid>" not in block:
            continue
        start, end = match.start(), match.end()
        if xml.startswith("\n    \n", end):
            end += len("\n    \n")
        elif xml[max(0, start - len("\n    \n")) : start] == "\n    \n":
            start -= len("\n    \n")
        return xml[:start] + xml[end:]
    return xml


def upsert_rss_item(xml: str, title: str, link: str, when: datetime, description: str) -> str:
    xml = remove_rss_item(xml, link)
    block = rss_item(title, link, when, description)
    matches = list(ITEM_RE.finditer(xml))
    for match in matches:
        found = re.search(r"<pubDate>(.*?)</pubDate>", match.group(0))
        if not found:
            continue
        if when >= parse_rss_date(found.group(1)):
            return xml[: match.start()] + block + "\n    \n" + xml[match.start() :]
    if matches:
        last = matches[-1]
        return xml[: last.end()] + "\n    \n" + block + xml[last.end() :]
    return xml.replace("  </channel>", block + "\n    \n  </channel>", 1)


def bump_last_build(xml: str, when: datetime) -> str:
    new = rss_date(when)
    match = re.search(r"<lastBuildDate>(.*?)</lastBuildDate>", xml)
    if not match:
        return xml.replace(
            "<atom:link ",
            f"<lastBuildDate>{new}</lastBuildDate><atom:link ",
            1,
        )
    if when >= parse_rss_date(match.group(1)):
        return xml[: match.start(1)] + new + xml[match.end(1) :]
    return xml


def _feed_item(xml: str, link: str) -> tuple[str, datetime] | None:
    for match in ITEM_RE.finditer(xml):
        block = match.group(0)
        if f"<link>{link}</link>" not in block:
            continue
        title = re.search(r"<title>(.*?)</title>", block)
        pub = re.search(r"<pubDate>(.*?)</pubDate>", block)
        if not title or not pub:
            continue
        return title.group(1), parse_rss_date(pub.group(1))
    return None


def upsert_feed(xml: str, title: str, link: str, when: datetime, description: str) -> str:
    xml = bump_last_build(xml, when)
    return upsert_rss_item(xml, title, link, when, description)


def new_term_feed(kind: str, name: str, slug: str, item_title: str, item_link: str, when: datetime, description: str) -> str:
    link = f"{SITE_BASE}/{kind}/{slug}/"
    item = rss_item(item_title, item_link, when, description)
    return (
        '<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
        "  <channel>\n"
        f"    <title>{xml_escape(name)} on Fucking Life</title>\n"
        f"    <link>{link}</link>\n"
        f"    <description>Recent content in {xml_escape(name)} on Fucking Life</description>\n"
        "    <generator>Hugo -- gohugo.io</generator>\n"
        "    <language>en</language>\n"
        f"    <lastBuildDate>{rss_date(when)}</lastBuildDate>"
        f'<atom:link href="{link}index.xml" rel="self" type="application/rss+xml" />\n'
        f"{item}\n"
        "    \n"
        "  </channel>\n"
        "</rss>\n"
    )


def term_listing(html: str, href: str) -> tuple[date, str] | None:
    if href not in html:
        return None
    start, end = find_archive(html)
    _prefix, years = split_years(html[start:end])
    for year, chunk in years:
        if href not in chunk:
            continue
        head, _tail = split_tail(chunk)
        for entry in ENTRY_RE.findall(head):
            if entry_href(entry) != href:
                continue
            month, day = entry_month_day(entry)
            match = re.search(r'title="([^"]*)"', entry)
            title = match.group(1) if match else ""
            return date(year, month, day), title
    return None


def term_index_needs_update(html: str, href: str, title_html: str, when: datetime) -> bool:
    found = term_listing(html, href)
    if found is None:
        return True
    listed, title = found
    if title != title_html:
        return True
    return listed < when.date()


def clone_term_page(root: Path, kind: str, name: str, slug: str, href: str, title_html: str, when: datetime) -> str:
    if kind == "categories":
        skeleton_path = root / "categories" / "hugo" / "index.html"
        if not skeleton_path.is_file():
            raise PublishError("categories/hugo/index.html is required as chrome for new categories")
        page = skeleton_path.read_text(encoding="utf-8")
        page = page.replace("Ermao | hugo ", f"Ermao | {hugo_escape(name)} ", 1)
        page = page.replace('href="/categories/hugo/"', f'href="/categories/{slug}/"')
        page = page.replace('content="hugo"', f'content="{hugo_escape(name)}"', 1)
    else:
        skeleton_path = root / "tags" / "index.html"
        if not skeleton_path.is_file():
            raise PublishError("tags/index.html is required as chrome for new tags")
        page = skeleton_path.read_text(encoding="utf-8")
        page = page.replace("Ermao | Tags ", f"Ermao | {hugo_escape(name)} ", 1)
        page = page.replace('href="/tags/"', f'href="/tags/{slug}/"')
        page = page.replace('content="Tags"', f'content="{hugo_escape(name)}"', 1)
    fresh = upsert_archive_inner("\n            \n        ", href, title_html, when)
    start, end = find_archive(page)
    return page[:start] + fresh + page[end:]


def set_sitemap_lastmod(xml: str, loc: str, lastmod: str, mode: str) -> str:
    pattern = re.compile(
        r"(<loc>" + re.escape(loc) + r"</loc>)(?:(\s*)<lastmod>([^<]*)</lastmod>)?"
    )
    match = pattern.search(xml)
    if not match:
        block = (
            "  <url>\n"
            f"    <loc>{loc}</loc>\n"
            f"    <lastmod>{lastmod}</lastmod>\n"
            "  </url>\n"
        )
        needle = 'xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
        if needle not in xml:
            raise PublishError("sitemap.xml is missing the urlset opening")
        return xml.replace(needle, needle + block, 1)
    old = match.group(3)
    if old and mode == "max" and old >= lastmod:
        return xml
    if old:
        updated = f"{match.group(1)}{match.group(2)}<lastmod>{lastmod}</lastmod>"
        return xml[: match.start()] + updated + xml[match.end() :]
    updated = f"{match.group(1)}\n    <lastmod>{lastmod}</lastmod>"
    return xml[: match.start()] + updated + xml[match.end() :]


def insert_daily_nav(html: str) -> str:
    """Add the 日报 link to the Anatole menu and the left sidebar.

    The menu is the top nav. The sidebar link sits under the bio. Pages
    without that chrome, including the daily feed itself, are left as they are.
    """
    html = _insert_daily_menu_link(html)
    html = _insert_daily_sidebar_link(html)
    return html


def _insert_daily_menu_link(html: str) -> str:
    start = html.find('id="navMenu"')
    if start < 0:
        return html
    end = html.find("</ul>", start)
    if end < 0:
        return html
    block = html[start:end]
    if 'href="/daily/"' in block:
        return html
    match = re.search(r'\n([ \t]*)<li><a\b[^>]*href="/post/"', block)
    if not match:
        return html
    indent = match.group(1)
    item = (
        f'\n{indent}<li><a \n'
        f'{indent}       href="/daily/"\n'
        f'{indent}            \n'
        f'{indent}       title="">日报</a></li>'
    )
    at = start + match.start()
    return html[:at] + item + html[at:]


def _insert_daily_sidebar_link(html: str) -> str:
    start = html.find('<div class="sidebar')
    if start < 0:
        return html
    end = html.find('<div class="main">', start)
    if end < 0:
        return html
    if 'href="/daily/"' in html[start:end]:
        return html
    needle = (
        "            </div>\n"
        "        </div>\n"
        "    </div>\n"
        '    <ul class="social-links">'
    )
    at = html.find(needle, start)
    if at < 0 or at > end:
        return html
    # Inline size overrides the theme rule that enlarges every link in .title.
    link = (
        '            <p><a href="/daily/" '
        'style="font-size:15px;font-weight:650;letter-spacing:0;'
        'text-transform:none;color:#2660ab">日报</a></p>\n'
    )
    insert_at = at + len("            </div>\n")
    return html[:insert_at] + link + html[insert_at:]


class SiteWriter:
    def __init__(self, root: Path, dry_run: bool) -> None:
        self.root = root
        self.dry_run = dry_run
        self.changed: list[str] = []

    def write(self, relative: str, text: str) -> None:
        if relative.endswith(".html") and relative != DAILY_INDEX_REL:
            text = insert_daily_nav(text)
        path = self.root / relative
        existing = path.read_text(encoding="utf-8") if path.is_file() else None
        if existing == text:
            return
        if relative not in self.changed:
            self.changed.append(relative)
        if self.dry_run:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")

    def read(self, relative: str) -> str:
        path = self.root / relative
        if not path.is_file():
            raise PublishError(f"missing {relative}")
        return path.read_text(encoding="utf-8")


def _drop_post_from_taxonomy(writer: SiteWriter, kind: str, keep: set[str], post_href: str, abs_url: str) -> None:
    base = writer.root / kind
    if not base.is_dir():
        return
    for page in sorted(base.glob("*/index.html")):
        slug = page.parent.name
        text = page.read_text(encoding="utf-8")
        if slug in keep or post_href not in text:
            continue
        writer.write(f"{kind}/{slug}/index.html", remove_archive_href(text, post_href))
    for feed in sorted(base.glob("*/index.xml")):
        slug = feed.parent.name
        text = feed.read_text(encoding="utf-8")
        if slug in keep or abs_url not in text:
            continue
        writer.write(f"{kind}/{slug}/index.xml", remove_rss_item(text, abs_url))


def _upsert_term(
    writer: SiteWriter,
    kind: str,
    name: str,
    slug: str,
    post: Post,
    description_xml: str,
) -> None:
    term_href = f"/{kind}/{slug}/"
    page_rel = f"{kind}/{slug}/index.html"
    feed_rel = f"{kind}/{slug}/index.xml"
    page_path = writer.root / page_rel
    if page_path.is_file():
        current = page_path.read_text(encoding="utf-8")
        writer.write(page_rel, replace_archive(current, post.path, post.title_html, post.when))
    else:
        writer.write(
            page_rel,
            clone_term_page(writer.root, kind, name, slug, post.path, post.title_html, post.when),
        )
    feed_path = writer.root / feed_rel
    if feed_path.is_file():
        writer.write(
            feed_rel,
            upsert_feed(feed_path.read_text(encoding="utf-8"), post.title, post.abs_url, post.when, description_xml),
        )
    else:
        writer.write(
            feed_rel,
            new_term_feed(kind, name, slug, post.title, post.abs_url, post.when, description_xml),
        )
    index_rel = f"{kind}/index.html"
    index_html = writer.read(index_rel)
    title_html = hugo_escape(name)
    if term_index_needs_update(index_html, term_href, title_html, post.when):
        writer.write(index_rel, replace_archive(index_html, term_href, title_html, post.when))
    index_xml_rel = f"{kind}/index.xml"
    index_xml = writer.read(index_xml_rel)
    term_link = f"{SITE_BASE}{term_href}"
    existing_item = _feed_item(index_xml, term_link)
    if existing_item is not None:
        old_title, old_when = existing_item
        if old_title == xml_escape(name) and old_when >= post.when:
            return
    writer.write(index_xml_rel, upsert_feed(index_xml, name, term_link, post.when, ""))


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


def render_daily_page() -> str:
    if not DAILY_TEMPLATE.is_file():
        raise PublishError(f"missing template {DAILY_TEMPLATE}")
    html = DAILY_TEMPLATE.read_text(encoding="utf-8")
    if "@@" in html:
        raise PublishError("daily template has an unfilled placeholder")
    return html


def _read_existing_items(root: Path) -> list:
    path = root / DAILY_ITEMS_REL
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise PublishError("daily/items.json must be a list")
    return data


def publish_digest(root: Path, items: list, dry_run: bool = False) -> list[str]:
    """Upsert digest items and regenerate /daily/ plus the items JSON."""
    for relative in ("daily/feed.css", "daily/feed.js"):
        if not (root / relative).is_file():
            raise PublishError(f"missing {relative}")
    merged = upsert_items(_read_existing_items(root), items)
    writer = SiteWriter(root, dry_run)
    payload = json.dumps(merged, ensure_ascii=False, indent=2) + "\n"
    writer.write(DAILY_ITEMS_REL, payload)
    writer.write(DAILY_INDEX_REL, render_daily_page())
    for relative in ("index.html", "post/index.html"):
        if (root / relative).is_file():
            writer.write(relative, writer.read(relative))
    if merged and (root / "sitemap.xml").is_file():
        newest = max(item["date"] for item in merged)
        sitemap = set_sitemap_lastmod(
            writer.read("sitemap.xml"),
            f"{SITE_BASE}/daily/",
            sitemap_date(parse_when(newest)),
            "set",
        )
        writer.write("sitemap.xml", sitemap)
    return writer.changed


def _wants_post(args: argparse.Namespace, payload: dict) -> bool:
    if args.title or args.slug or args.body is not None or args.body_file or args.categories:
        return True
    return any(payload.get(key) for key in ("title", "slug", "body", "body_file"))


def publish(root: Path, post: Post, dry_run: bool = False) -> list[str]:
    writer = SiteWriter(root, dry_run)
    writer.write(f"post/{post.slug}/index.html", render_single(post))
    writer.write("index.html", replace_homepage(writer.read("index.html"), post))
    writer.write(
        "post/index.html",
        replace_archive(writer.read("post/index.html"), post.path, post.title_html, post.when),
    )
    description_xml = xml_escape(post.summary_html)
    writer.write(
        "index.xml",
        upsert_feed(writer.read("index.xml"), post.title, post.abs_url, post.when, description_xml),
    )
    writer.write(
        "post/index.xml",
        upsert_feed(writer.read("post/index.xml"), post.title, post.abs_url, post.when, description_xml),
    )
    category_slugs = {slug for _, slug in post.categories}
    tag_slugs = {slug for _, slug in post.tags}
    _drop_post_from_taxonomy(writer, "categories", category_slugs, post.path, post.abs_url)
    _drop_post_from_taxonomy(writer, "tags", tag_slugs, post.path, post.abs_url)
    for name, slug in post.categories:
        _upsert_term(writer, "categories", name, slug, post, description_xml)
    for name, slug in post.tags:
        _upsert_term(writer, "tags", name, slug, post, description_xml)
    lastmod = sitemap_date(post.when)
    sitemap = writer.read("sitemap.xml")
    sitemap = set_sitemap_lastmod(sitemap, post.abs_url, lastmod, "set")
    for loc in (
        f"{SITE_BASE}/",
        f"{SITE_BASE}/post/",
        f"{SITE_BASE}/categories/",
    ):
        sitemap = set_sitemap_lastmod(sitemap, loc, lastmod, "max")
    if post.tags:
        sitemap = set_sitemap_lastmod(sitemap, f"{SITE_BASE}/tags/", lastmod, "max")
    for _name, slug in post.categories:
        sitemap = set_sitemap_lastmod(sitemap, f"{SITE_BASE}/categories/{slug}/", lastmod, "max")
    for _name, slug in post.tags:
        sitemap = set_sitemap_lastmod(sitemap, f"{SITE_BASE}/tags/{slug}/", lastmod, "max")
    writer.write("sitemap.xml", sitemap)
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
        summary_html = first_paragraph(summary_source) if summary_source else first_paragraph(body_html)
    else:
        body_html = render_markdown(full)
        summary_html = first_paragraph(render_markdown(before) if before else body_html)
    body_html = mark_digest_meta(body_html)
    if summary_override:
        summary_html = "<p>" + hugo_escape(str(summary_override).strip()) + "</p>"
    if description_override:
        description = re.sub(r"\s+", " ", str(description_override)).strip()
    else:
        description = plain_text(summary_html)[:180]
    return Post(
        title=str(title).strip(),
        when=parse_when(str(when_raw)),
        slug=validate_slug(str(slug)),
        categories=categories,
        tags=tags,
        body_html=body_html,
        summary_html=summary_html,
        description=description,
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
    parser.add_argument("--dry-run", action="store_true", help="report files that would change and write nothing")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = Path(args.root).resolve() if args.root else HERE.parent
    if not (root / "index.html").is_file():
        raise PublishError(f"{root} does not look like the site root (no index.html)")
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
    action = "would update" if args.dry_run else "updated"
    if published_post is not None:
        print(f"published {published_post.path}")
    if item_rows is not None:
        print("published /daily/")
    seen: set[str] = set()
    unique = []
    for relative in changed:
        if relative in seen:
            continue
        seen.add(relative)
        unique.append(relative)
    if unique:
        for relative in unique:
            print(f"{action} {relative}")
    else:
        print("no file changes")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PublishError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
