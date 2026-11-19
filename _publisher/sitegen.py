"""Render the whole site from post records.

Every post lives as a JSON record in ``_publisher/posts/<slug>.json``. A build
reads all records and writes the homepage, post pages, column (category) and
tag pages, the archive, RSS feeds, the sitemap, ``404.html``, and the
``/daily/`` page. Nothing is spliced into existing HTML, so a build from the
same records always gives the same files.

``publish.py`` is the command-line entry point; this module has no CLI.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SITE_BASE = "https://fuCking-Life.github.io"
SITE_TITLE = "Fucking Life"
AUTHOR = "Ermao"
BIO = "做 Agent 和语音 AI，也研究 A 股短线。这里每天更新 AI 简报、交易资讯和学习笔记。"
GITHUB_URL = "https://github.com/fuCking-Life"
EMAIL = "464514962@qq.com"
COMMENTS_REPO = "fuCking-Life/fuCking-Life.github.io"
DISCLAIMER = "交易资讯仅为信息整理，不构成投资建议"
TZ = ZoneInfo("Asia/Shanghai")

HERE = Path(__file__).resolve().parent
POSTS_DIR = HERE / "posts"
DAILY_TEMPLATE = HERE / "templates" / "daily.html"
CSS_REL = "css/site.css"
JS_REL = "js/site.js"
PROFILE_IMAGE = "/images/profile.jpg"

WEEKDAYS_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
WEEKDAYS_ZH = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
# Opening lines of a digest that say when or what it covers, not what happened.
META_LINE = re.compile(r"^(?:时间窗口|覆盖范围|\d{4}年)")

# Directories a build owns. A subdirectory of these that holds only
# index.html / index.xml and matches no current post or term is removed.
OWNED_DIRS = ("post", "categories", "tags")
GENERATED_NAMES = {"index.html", "index.xml"}


class PublishError(SystemExit):
    def __init__(self, message: str) -> None:
        super().__init__(message)


# ---------------------------------------------------------------- text helpers


def esc(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&#34;")
        .replace("'", "&#39;")
    )


def xml_escape(text: str) -> str:
    return esc(text)


def urlize(name: str) -> str:
    """Path slug: lowercase, unicode letters kept, other runs become '-'."""
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


def plain_text(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    text = (
        text.replace("&#43;", "+")
        .replace("&#34;", '"')
        .replace("&#39;", "'")
        .replace("&quot;", '"')
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
    )
    return re.sub(r"\s+", " ", text).strip()


def rss_date(dt: datetime) -> str:
    aware = dt.astimezone(TZ)
    return (
        f"{WEEKDAYS_EN[aware.weekday()]}, {aware.day:02d} {MONTHS_EN[aware.month - 1]} "
        f"{aware.year} {aware.hour:02d}:{aware.minute:02d}:{aware.second:02d} +0800"
    )


def sitemap_date(dt: datetime) -> str:
    return dt.astimezone(TZ).strftime("%Y-%m-%dT%H:%M:%S+08:00")


def month_day(dt: datetime) -> str:
    return dt.astimezone(TZ).strftime("%m-%d")


def ymd(dt: datetime) -> str:
    return dt.astimezone(TZ).strftime("%Y-%m-%d")


def weekday_zh(dt: datetime) -> str:
    return WEEKDAYS_ZH[dt.astimezone(TZ).weekday()]


# Learning-note callouts: a blockquote that opens with one of these bold labels.
CALLOUTS = {
    "原则": "ln-key",
    "要点": "ln-key",
    "我的理解": "ln-key",
    "思考题": "ln-ask",
    "修正": "ln-fix",
    "举一反三": "ln-ext",
}
_CALLOUT_RE = re.compile(r"<blockquote>(\s*<p><strong>([^<]+)</strong>)")


def mark_callouts(html: str) -> str:
    """Class a blockquote by the bold word it opens with, such as **思考题**."""

    def repl(match: re.Match[str]) -> str:
        label = match.group(2).strip().rstrip("：:")
        cls = CALLOUTS.get(label)
        return f'<blockquote class="{cls}">{match.group(1)}' if cls else match.group(0)

    return _CALLOUT_RE.sub(repl, html)


# --------------------------------------------------------------------- columns


@dataclass(frozen=True)
class Column:
    """A category shown as its own homepage section and nav item."""

    name: str
    nav_label: str
    color: str  # CSS custom property suffix: --c-<color>
    layout: str  # "days" groups the newest day into cards; "entries" is a list
    limit: int
    description: str
    nav_href: str = ""  # defaults to the category page
    extra_link: tuple[str, str] | None = None  # (href, label) in the section head

    @property
    def slug(self) -> str:
        return urlize(self.name)

    @property
    def href(self) -> str:
        return f"/categories/{self.slug}/"

    @property
    def nav_target(self) -> str:
        return self.nav_href or self.href


COLUMNS = (
    Column(
        name="日报",
        nav_label="日报",
        color="daily",
        layout="days",
        limit=3,
        description="Luna 全双工语音日报和 AI 与移动端智能体简报，每天一期。",
        nav_href="/daily/",
        extra_link=("/daily/", "按领域筛选 →"),
    ),
    Column(
        name="每日交易资讯",
        nav_label="交易资讯",
        color="trade",
        layout="entries",
        limit=2,
        description="A 股每日复盘。只做信息整理，不构成投资建议。",
    ),
    Column(
        name="Agent 学习笔记",
        nav_label="学习笔记",
        color="learn",
        layout="entries",
        limit=3,
        description="学习 agent 设计的复盘：学到什么、哪里理解错了、还有什么没想通。",
    ),
)
COLUMN_BY_NAME = {column.name: column for column in COLUMNS}


# ----------------------------------------------------------------------- posts


@dataclass
class PostRecord:
    slug: str
    title: str
    when: datetime
    categories: list[str]
    tags: list[str]
    body_html: str
    summary_html: str
    description: str
    headline: str = ""

    @property
    def path(self) -> str:
        return f"/post/{self.slug}/"

    @property
    def url(self) -> str:
        return SITE_BASE + self.path

    def to_json(self) -> str:
        data = {
            "slug": self.slug,
            "title": self.title,
            "headline": self.headline,
            "date": sitemap_date(self.when),
            "categories": self.categories,
            "tags": self.tags,
            "description": self.description,
            "summary_html": self.summary_html,
            "body_html": self.body_html,
        }
        return json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    @classmethod
    def from_dict(cls, data: dict) -> "PostRecord":
        try:
            when = datetime.fromisoformat(data["date"]).astimezone(TZ)
            return cls(
                slug=data["slug"],
                title=data["title"],
                when=when,
                categories=list(data["categories"]),
                tags=list(data.get("tags", [])),
                body_html=data["body_html"],
                summary_html=data["summary_html"],
                description=data.get("description", ""),
                headline=data.get("headline", "") or "",
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PublishError(f"bad post record {data.get('slug', '?')}: {exc}") from exc

    # Display helpers --------------------------------------------------------

    @property
    def column(self) -> Column | None:
        for name in self.categories:
            if name in COLUMN_BY_NAME:
                return COLUMN_BY_NAME[name]
        return None

    @property
    def primary_category(self) -> str:
        return self.categories[0]

    @property
    def display_title(self) -> str:
        """``Agent 学习笔记（2026-11-19）：什么才算 agent`` shows as ``什么才算 agent``."""
        match = re.match(r"^.*?（\d{4}-\d{2}-\d{2}）[：:]\s*(.+)$", self.title)
        return match.group(1).strip() if match else self.title

    @property
    def series_title(self) -> str:
        """The title without a trailing ``（YYYY-MM-DD）``."""
        stripped = re.sub(r"\s*（\d{4}-\d{2}-\d{2}）\s*$", "", self.title).strip()
        return stripped or self.title

    @property
    def summary_text(self) -> str:
        """The listing summary without its opening 时间窗口 / 覆盖范围 / date lines."""
        paragraphs = re.findall(r"<p\b[^>]*>(.*?)</p>", self.summary_html, flags=re.S)
        if not paragraphs:
            return plain_text(self.summary_html)
        texts = [plain_text(p) for p in paragraphs]
        kept = [t for t in texts if t and not META_LINE.match(t)]
        return " ".join(kept) or " ".join(texts)

    @property
    def item_count(self) -> int | None:
        match = re.search(r"本期\s*(\d+)\s*条", plain_text(self.summary_html))
        return int(match.group(1)) if match else None

    @property
    def extra_tags(self) -> list[str]:
        return [tag for tag in self.tags if tag not in self.categories]

    @property
    def reading_minutes(self) -> int:
        text = plain_text(self.body_html)
        cjk = len(re.findall(r"[㐀-鿿豈-﫿]", text))
        words = len(re.findall(r"[A-Za-z0-9_]+", text))
        return max(1, math.ceil(cjk / 400 + words / 200))


def sort_posts(posts: list[PostRecord]) -> list[PostRecord]:
    """Newest first; posts at the same moment by slug, descending."""
    return sorted(posts, key=lambda post: (post.when, post.slug), reverse=True)


def load_records(posts_dir: Path = POSTS_DIR) -> list[PostRecord]:
    if not posts_dir.is_dir():
        return []
    records = []
    for path in sorted(posts_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        record = PostRecord.from_dict(data)
        if record.slug != path.stem:
            raise PublishError(f"{path.name} holds slug {record.slug!r}")
        records.append(record)
    return records


# ----------------------------------------------------------------------- shell

THEME_BOOT = (
    "<script>try{var t=localStorage.getItem('theme');"
    "if(t==='dark'||t==='light')document.documentElement.dataset.theme=t}catch(e){}</script>"
)
THEME_ICON = (
    '<svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">'
    '<circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" stroke-width="1.5"/>'
    '<path d="M8 1.5a6.5 6.5 0 0 1 0 13z" fill="currentColor"/></svg>'
)


@dataclass
class Shell:
    asset_version: str
    year: int
    nav: list[tuple[str, str, str]] = field(default_factory=list)  # (key, label, href)

    def page(
        self,
        *,
        title: str,
        description: str,
        path: str,
        body: str,
        current: str = "",
        kind: str = "website",
        extra_body: str = "",
    ) -> str:
        nav = "".join(
            f'<a{" class=\"on\" aria-current=\"page\"" if key == current else ""} href="{esc(href)}">{esc(label)}</a>'
            for key, label, href in self.nav
        )
        full_title = title if title == SITE_TITLE else f"{title} · {SITE_TITLE}"
        v = self.asset_version
        return (
            "<!doctype html>\n"
            '<html lang="zh-CN">\n'
            "<head>\n"
            '<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"<title>{esc(full_title)}</title>\n"
            f'<meta name="description" content="{esc(description)}">\n'
            f'<link rel="canonical" href="{esc(SITE_BASE + path)}">\n'
            f'<meta property="og:title" content="{esc(title)}">\n'
            f'<meta property="og:description" content="{esc(description)}">\n'
            f'<meta property="og:url" content="{esc(SITE_BASE + path)}">\n'
            f'<meta property="og:type" content="{kind}">\n'
            f'<meta property="og:site_name" content="{SITE_TITLE}">\n'
            f'<link rel="alternate" type="application/rss+xml" href="/index.xml" title="{SITE_TITLE}">\n'
            '<link rel="icon" href="/favicons/favicon.ico">\n'
            f"{THEME_BOOT}\n"
            f'<link rel="stylesheet" href="/{CSS_REL}?v={v}">\n'
            "</head>\n"
            "<body>\n"
            '<header class="site-header"><div class="wrap">'
            '<a class="brand" href="/">Fucking<span>·</span>Life</a>'
            f'<nav class="nav" aria-label="栏目">{nav}</nav>'
            f'<button type="button" class="theme-btn" aria-label="切换深浅色" title="切换深浅色">{THEME_ICON}</button>'
            "</div></header>\n"
            f'<main class="wrap">\n{body}\n</main>\n'
            '<footer class="site-footer"><div class="wrap">'
            f'<span>© {self.year} {AUTHOR} · <a href="/index.xml">RSS</a></span>'
            f"<span>{DISCLAIMER}</span>"
            "</div></footer>\n"
            f"{extra_body}"
            f'<script src="/{JS_REL}?v={v}" defer></script>\n'
            "</body>\n"
            "</html>\n"
        )


def build_nav() -> list[tuple[str, str, str]]:
    nav = [("home", "首页", "/")]
    nav.extend((column.name, column.nav_label, column.nav_target) for column in COLUMNS)
    nav.append(("archive", "归档", "/post/"))
    return nav


# ---------------------------------------------------------------------- pieces


def dot(color: str) -> str:
    return f'<i class="dot" style="background:var(--c-{color})"></i>'


def color_of(post: PostRecord) -> str:
    column = post.column
    return column.color if column else "other"


def entry_html(post: PostRecord, *, show_year: bool = False) -> str:
    when = ymd(post.when) if show_year else month_day(post.when)
    meta = [f"{when} {weekday_zh(post.when)}"]
    meta.extend(esc(tag) for tag in post.extra_tags)
    heading = post.headline or post.display_title
    summary = post.summary_text
    return (
        '<article class="entry">'
        f'<div class="meta">{" · ".join(meta)}</div>'
        f'<h3><a href="{post.path}">{esc(heading)}</a></h3>'
        + (f"<p>{esc(summary)}</p>" if summary else "")
        + "</article>"
    )


def day_card(post: PostRecord) -> str:
    count = post.item_count
    count_text = f" · {count} 条" if count else ""
    if post.headline:
        kicker = esc(post.series_title) + count_text
        heading = post.headline
    else:
        kicker = count_text.lstrip(" ·")
        heading = post.series_title
    summary = post.summary_text
    return (
        f'<a class="card" href="{post.path}">'
        + (f'<div class="kicker">{kicker}</div>' if kicker else "")
        + f"<h3>{esc(heading)}</h3>"
        + (f"<p>{esc(summary)}</p>" if summary else "")
        + "</a>"
    )


def section_head(title: str, color: str, links: list[tuple[str, str]]) -> str:
    more = "".join(f'<a class="more" href="{esc(href)}">{esc(label)}</a>' for href, label in links)
    return (
        '<div class="sec-head">'
        f"<h2>{dot(color)}{esc(title)}</h2>"
        f'<div class="sec-links">{more}</div>'
        "</div>"
    )


def column_section(column: Column, posts: list[PostRecord]) -> str:
    if not posts:
        return ""
    links = []
    if column.extra_link:
        links.append(column.extra_link)
    else:
        links.append((column.href, "全部 →"))
    parts = [f'<section class="section" id="{esc(column.slug)}">', section_head(column.name, column.color, links)]
    if column.layout == "days":
        parts.append(f'<p class="sec-desc">{esc(column.description)}</p>')
        newest = ymd(posts[0].when)
        today = [post for post in posts if ymd(post.when) == newest]
        rest = [post for post in posts if ymd(post.when) != newest][: column.limit]
        parts.append(
            '<div class="day">'
            f'<div class="day-label"><b>{month_day(posts[0].when)}</b>{weekday_zh(posts[0].when)}</div>'
            f'<div class="cards">{"".join(day_card(post) for post in today)}</div>'
            "</div>"
        )
        if rest:
            rows = "".join(
                f'<li><a href="{post.path}">{esc(post.series_title)}</a><span>{month_day(post.when)}</span></li>'
                for post in rest
            )
            parts.append(f'<ul class="compact">{rows}</ul>')
        parts.append(f'<p class="sec-all"><a href="{column.href}">全部{esc(column.name)} →</a></p>')
    else:
        parts.extend(entry_html(post) for post in posts[: column.limit])
    parts.append("</section>")
    return "".join(parts)


def other_section(name: str, posts: list[PostRecord]) -> str:
    slug = urlize(name)
    parts = [
        f'<section class="section" id="{esc(slug)}">',
        section_head(name, "other", [(f"/categories/{slug}/", "全部 →")]),
    ]
    parts.extend(entry_html(post) for post in posts[:3])
    parts.append("</section>")
    return "".join(parts)


# ----------------------------------------------------------------------- pages


def render_home(shell: Shell, posts: list[PostRecord]) -> str:
    intro = (
        '<section class="intro">'
        f'<img src="{PROFILE_IMAGE}" alt="{AUTHOR} 的头像" width="72" height="72">'
        "<div>"
        f"<h1>{AUTHOR}</h1>"
        f"<p>{esc(BIO)}</p>"
        '<div class="links">'
        f'<a href="{GITHUB_URL}" rel="me">GitHub</a>'
        f'<a href="mailto:{EMAIL}">邮箱</a>'
        '<a href="/index.xml">RSS</a>'
        "</div></div></section>"
    )
    sections = [intro]
    for column in COLUMNS:
        sections.append(column_section(column, [p for p in posts if column.name in p.categories]))
    claimed = {column.name for column in COLUMNS}
    others: dict[str, list[PostRecord]] = {}
    for post in posts:
        if any(name in claimed for name in post.categories):
            continue
        others.setdefault(post.primary_category, []).append(post)
    for name in sorted(others, key=lambda n: others[n][0].when, reverse=True):
        sections.append(other_section(name, others[name]))
    if not posts:
        sections.append('<p class="empty">还没有文章。</p>')
    return shell.page(
        title=SITE_TITLE,
        description=BIO,
        path="/",
        body="\n".join(s for s in sections if s),
        current="home",
    )


def comments_block() -> str:
    return (
        '<section class="comments" id="comments" '
        f'data-repo="{COMMENTS_REPO}" data-issue-term="pathname">'
        "<h2>评论</h2>"
        "</section>"
    )


def render_post(shell: Shell, post: PostRecord, prev: PostRecord | None, nxt: PostRecord | None) -> str:
    column = post.column
    category = post.primary_category
    crumb_href = column.href if column else f"/categories/{urlize(category)}/"
    crumb_color = column.color if column else "other"
    tags = "".join(
        f'<a class="tag" href="/tags/{esc(urlize(tag))}/">#{esc(tag)}</a>' for tag in post.extra_tags
    )
    head = (
        '<header class="post-head">'
        f'<div class="crumb"><a href="{crumb_href}" style="color:var(--c-{crumb_color})">{esc(category)}</a>'
        f' · <time datetime="{sitemap_date(post.when)}">{ymd(post.when)}</time></div>'
        f"<h1>{esc(post.display_title)}</h1>"
        f'<div class="post-meta"><span>约 {post.reading_minutes} 分钟</span>{tags}</div>'
        "</header>"
    )
    body = f'<article class="prose">\n{mark_callouts(post.body_html.strip())}\n</article>'

    def nav_link(target: PostRecord | None, label: str, cls: str) -> str:
        if target is None:
            return f'<span class="{cls} none"><small>{label}</small>没有了</span>'
        return f'<a class="{cls}" href="{target.path}"><small>{label}</small>{esc(target.display_title)}</a>'

    pager = (
        '<nav class="post-nav" aria-label="同栏目文章">'
        + nav_link(prev, "← 上一篇", "prev")
        + nav_link(nxt, "下一篇 →", "next")
        + "</nav>"
    )
    return shell.page(
        title=post.title,
        description=post.description or post.summary_text[:180],
        path=post.path,
        body=head + "\n" + body + "\n" + pager + "\n" + comments_block(),
        current=column.name if column else "",
        kind="article",
    )


def render_listing_page(
    shell: Shell,
    *,
    title: str,
    crumb: str,
    color: str,
    description: str,
    path: str,
    posts: list[PostRecord],
    current: str = "",
    links: list[tuple[str, str]] | None = None,
) -> str:
    extra = "".join(f'<a href="{esc(href)}">{esc(label)}</a>' for href, label in (links or []))
    head = (
        '<header class="page-head">'
        f'<div class="crumb">{esc(crumb)}</div>'
        f"<h1>{dot(color)}{esc(title)}</h1>"
        + (f'<p class="sec-desc">{esc(description)}</p>' if description else "")
        + (f'<div class="page-links">{extra}</div>' if extra else "")
        + "</header>"
    )
    entries = "".join(entry_html(post, show_year=True) for post in posts) or '<p class="empty">还没有文章。</p>'
    return shell.page(
        title=title,
        description=description or f"{title} · {SITE_TITLE}",
        path=path,
        body=head + f'\n<div class="listing">{entries}</div>',
        current=current,
    )


def render_archive(shell: Shell, posts: list[PostRecord]) -> str:
    groups: dict[int, list[PostRecord]] = {}
    for post in posts:
        groups.setdefault(post.when.astimezone(TZ).year, []).append(post)
    parts = [
        '<header class="page-head"><div class="crumb">全部文章</div>'
        f'<h1>归档</h1><p class="sec-desc">共 {len(posts)} 篇。</p></header>'
    ]
    for year in sorted(groups, reverse=True):
        rows = "".join(
            f'<li><span class="d">{month_day(post.when)}</span>{dot(color_of(post))}'
            f'<a href="{post.path}">{esc(post.title)}</a></li>'
            for post in groups[year]
        )
        parts.append(f'<section class="year"><h2>{year}</h2><ul class="archive">{rows}</ul></section>')
    return shell.page(
        title="归档",
        description=f"{SITE_TITLE} 的全部文章。",
        path="/post/",
        body="\n".join(parts),
        current="archive",
    )


def render_term_index(shell: Shell, kind: str, terms: list[tuple[str, list[PostRecord]]]) -> str:
    label = "栏目" if kind == "categories" else "标签"
    rows = "".join(
        f'<li><a href="/{kind}/{esc(urlize(name))}/">{"#" if kind == "tags" else ""}{esc(name)}</a>'
        f"<span>{len(posts)} 篇</span></li>"
        for name, posts in terms
    )
    return shell.page(
        title=label,
        description=f"{SITE_TITLE} 的全部{label}。",
        path=f"/{kind}/",
        body=(
            f'<header class="page-head"><div class="crumb">索引</div><h1>{label}</h1></header>'
            f'<ul class="terms">{rows}</ul>'
        ),
    )


def render_404(shell: Shell) -> str:
    return shell.page(
        title="页面不存在",
        description="页面不存在。",
        path="/404.html",
        body=(
            '<section class="notfound"><h1>页面不存在</h1>'
            "<p>链接可能写错了，或者这是一篇已经下线的旧文章（2026 年以前的文章都已删除）。</p>"
            '<p><a href="/">回到首页 →</a></p></section>'
        ),
    )


def render_daily_page() -> str:
    if not DAILY_TEMPLATE.is_file():
        raise PublishError(f"missing template {DAILY_TEMPLATE}")
    html = DAILY_TEMPLATE.read_text(encoding="utf-8")
    if "@@" in html:
        raise PublishError("daily template has an unfilled placeholder")
    return html


# ------------------------------------------------------------------------ feeds


def rss_channel(title: str, path: str, description: str, items: list[tuple[str, str, datetime, str]]) -> str:
    newest = max((when for _t, _l, when, _d in items), default=None)
    rows = []
    for item_title, link, when, desc in items:
        rows.append(
            "    <item>\n"
            f"      <title>{xml_escape(item_title)}</title>\n"
            f"      <link>{xml_escape(link)}</link>\n"
            f"      <pubDate>{rss_date(when)}</pubDate>\n"
            f"      <guid>{xml_escape(link)}</guid>\n"
            f"      <description>{xml_escape(desc)}</description>\n"
            "    </item>\n"
        )
    build = f"    <lastBuildDate>{rss_date(newest)}</lastBuildDate>\n" if newest else ""
    return (
        '<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
        "  <channel>\n"
        f"    <title>{xml_escape(title)}</title>\n"
        f"    <link>{SITE_BASE}{path}</link>\n"
        f"    <description>{xml_escape(description)}</description>\n"
        "    <language>zh-CN</language>\n"
        f"{build}"
        f'    <atom:link href="{SITE_BASE}{path}index.xml" rel="self" type="application/rss+xml" />\n'
        + "".join(rows)
        + "  </channel>\n</rss>\n"
    )


def post_items(posts: list[PostRecord]) -> list[tuple[str, str, datetime, str]]:
    return [(post.title, post.url, post.when, post.summary_html) for post in posts]


def render_sitemap(entries: list[tuple[str, datetime | None]]) -> str:
    rows = []
    for path, when in entries:
        lastmod = f"\n    <lastmod>{sitemap_date(when)}</lastmod>" if when else ""
        rows.append(f"  <url>\n    <loc>{xml_escape(SITE_BASE + path)}</loc>{lastmod}\n  </url>\n")
    return (
        '<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(rows)
        + "</urlset>\n"
    )


# ------------------------------------------------------------------------ build


def asset_version(root: Path) -> str:
    digest = hashlib.sha256()
    for rel in (CSS_REL, JS_REL):
        path = root / rel
        if not path.is_file():
            raise PublishError(f"missing {rel}")
        digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


def group_terms(posts: list[PostRecord], attr: str) -> list[tuple[str, list[PostRecord]]]:
    """Terms with their posts (newest first); terms ordered by newest post."""
    groups: dict[str, list[PostRecord]] = {}
    slugs: dict[str, str] = {}
    for post in posts:
        for name in getattr(post, attr):
            slug = urlize(name)
            if slug in slugs and slugs[slug] != name:
                raise PublishError(f"terms {slugs[slug]!r} and {name!r} share the slug {slug!r}")
            slugs[slug] = name
            groups.setdefault(name, []).append(post)
    return sorted(groups.items(), key=lambda kv: (kv[1][0].when, kv[0]), reverse=True)


def build_site(root: Path, records: list[PostRecord], daily_dates: list[str] | None = None) -> dict[str, str]:
    """Return every generated file as {relative path: text}."""
    posts = sort_posts(records)
    year = max((post.when.astimezone(TZ).year for post in posts), default=datetime.now(TZ).year)
    shell = Shell(asset_version=asset_version(root), year=year, nav=build_nav())
    files: dict[str, str] = {}

    files["index.html"] = render_home(shell, posts)
    files["404.html"] = render_404(shell)
    files["post/index.html"] = render_archive(shell, posts)

    by_category: dict[str, list[PostRecord]] = {}
    for post in posts:
        by_category.setdefault(post.primary_category, []).append(post)
    for post in posts:
        siblings = by_category[post.primary_category]
        index = siblings.index(post)
        newer = siblings[index - 1] if index > 0 else None
        older = siblings[index + 1] if index + 1 < len(siblings) else None
        files[f"post/{post.slug}/index.html"] = render_post(shell, post, older, newer)

    categories = group_terms(posts, "categories")
    tags = group_terms(posts, "tags")
    for name, term_posts in categories:
        slug = urlize(name)
        column = COLUMN_BY_NAME.get(name)
        links = []
        if column and column.extra_link:
            links.append(column.extra_link)
        files[f"categories/{slug}/index.html"] = render_listing_page(
            shell,
            title=name,
            crumb="栏目",
            color=column.color if column else "other",
            description=column.description if column else "",
            path=f"/categories/{slug}/",
            posts=term_posts,
            current=name if column and not column.nav_href else "",
            links=links,
        )
        files[f"categories/{slug}/index.xml"] = rss_channel(
            f"{name} · {SITE_TITLE}", f"/categories/{slug}/", f"{name} 栏目的文章", post_items(term_posts)
        )
    for name, term_posts in tags:
        slug = urlize(name)
        files[f"tags/{slug}/index.html"] = render_listing_page(
            shell,
            title=f"#{name}",
            crumb="标签",
            color="other",
            description="",
            path=f"/tags/{slug}/",
            posts=term_posts,
        )
        files[f"tags/{slug}/index.xml"] = rss_channel(
            f"#{name} · {SITE_TITLE}", f"/tags/{slug}/", f"带 #{name} 标签的文章", post_items(term_posts)
        )
    files["categories/index.html"] = render_term_index(shell, "categories", categories)
    files["tags/index.html"] = render_term_index(shell, "tags", tags)
    files["categories/index.xml"] = rss_channel(
        f"栏目 · {SITE_TITLE}",
        "/categories/",
        "全部栏目",
        [(name, f"{SITE_BASE}/categories/{urlize(name)}/", ps[0].when, "") for name, ps in categories],
    )
    files["tags/index.xml"] = rss_channel(
        f"标签 · {SITE_TITLE}",
        "/tags/",
        "全部标签",
        [(name, f"{SITE_BASE}/tags/{urlize(name)}/", ps[0].when, "") for name, ps in tags],
    )

    feed = post_items(posts)
    files["index.xml"] = rss_channel(SITE_TITLE, "/", BIO, feed)
    files["post/index.xml"] = rss_channel(f"归档 · {SITE_TITLE}", "/post/", "全部文章", feed)

    files["daily/index.html"] = render_daily_page()

    newest = posts[0].when if posts else None
    daily_newest = None
    sitemap: list[tuple[str, datetime | None]] = [("/", newest), ("/post/", newest)]
    if daily_dates:
        daily_newest = datetime.strptime(max(daily_dates), "%Y-%m-%d").replace(hour=8, tzinfo=TZ)
    sitemap.append(("/daily/", daily_newest))
    sitemap.append(("/categories/", newest))
    sitemap.extend((f"/categories/{urlize(name)}/", ps[0].when) for name, ps in categories)
    if tags:
        sitemap.append(("/tags/", newest))
        sitemap.extend((f"/tags/{urlize(name)}/", ps[0].when) for name, ps in tags)
    sitemap.extend((post.path, post.when) for post in posts)
    files["sitemap.xml"] = render_sitemap(sitemap)
    return files


def stale_dirs(root: Path, files: dict[str, str]) -> tuple[list[Path], list[Path]]:
    """Owned subdirectories no current file lives in.

    Returns (removable, kept): a directory is removable when it holds only
    generated index.html / index.xml files; anything else is reported and kept.
    """
    expected = {Path(rel).parts[:2] for rel in files if len(Path(rel).parts) > 2}
    removable: list[Path] = []
    kept: list[Path] = []
    for owned in OWNED_DIRS:
        base = root / owned
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if not child.is_dir() or (owned, child.name) in expected:
                continue
            contents = [p for p in child.rglob("*") if p.is_file()]
            if contents and all(p.name in GENERATED_NAMES and p.parent == child for p in contents):
                removable.append(child)
            elif not contents:
                removable.append(child)
            else:
                kept.append(child)
    return removable, kept
