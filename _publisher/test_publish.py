#!/usr/bin/env python3
"""Checks for the static publisher. These do not add a post to the real site."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
SITE = HERE.parent
sys.path.insert(0, str(HERE))
import publish  # noqa: E402
import sitegen  # noqa: E402


BODY = """# 固定标题

这是发布器测试用的第一段，不是新闻。

## 小节

- 一
- 二

```python
print(1 + 1)
```

见 [示例](https://example.com/a+b) 与 `code+span`。
"""

OLDER = """第一段是较早的测试段落。

第二段不应当出现在摘要里。
"""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class MarkdownTests(unittest.TestCase):
    def test_headings_lists_code_and_escapes(self):
        html = publish.render_markdown(BODY)
        self.assertIn('<h1 id="固定标题">固定标题</h1>', html)
        self.assertIn('<h2 id="小节">小节</h2>', html)
        self.assertIn("<ul><li><p>一</p></li><li><p>二</p></li></ul>", html)
        self.assertIn('class="language-python"', html)
        self.assertIn("print(1 &#43; 1)", html)
        self.assertIn('href="https://example.com/a&#43;b"', html)
        self.assertIn("<code>code&#43;span</code>", html)

    def test_heading_slug_matches_existing_hugo_style(self):
        html = publish.render_markdown("## 2.  安装Hugo\n\n## 2.1 创建ShellProject\n")
        self.assertIn('id="2--安装hugo"', html)
        self.assertIn('id="21-创建shellproject"', html)

    def test_list_continuation_and_digest_meta(self):
        html = publish.render_markdown(
            "时间窗口：今天\n\n"
            "覆盖范围：本周\n\n"
            "2026年11月17日\n\n"
            "## 今日重点\n\n"
            "1. **标题**  \n"
            "   续行仍在条目里。\n"
            "   [帖子](https://example.com/a)\n"
        )
        html = publish.mark_digest_meta(html)
        self.assertIn('<p class="digest-meta">时间窗口：今天</p>', html)
        self.assertIn('<p class="digest-meta">覆盖范围：本周</p>', html)
        self.assertIn('<p class="digest-meta">2026年11月17日</p>', html)
        self.assertIn("<li><p><strong>标题</strong><br>\n续行仍在条目里。\n", html)
        self.assertIn('href="https://example.com/a"', html)
        self.assertLess(html.find("续行仍在条目里"), html.find("</li>"))

    def test_more_separator_is_not_rendered(self):
        before, full = publish.split_more("摘要段落。\n\n<!--more-->\n\n正文段落。\n")
        self.assertEqual(before, "摘要段落。")
        self.assertNotIn("<!--more-->", full)
        summary = publish.first_paragraph(publish.render_markdown(before))
        body = publish.render_markdown(full)
        self.assertIn("摘要段落", summary)
        self.assertIn("正文段落", body)
        self.assertNotIn("<!--more-->", body)

    def test_more_keeps_every_paragraph_before_the_separator(self):
        rendered = publish.render_markdown(
            "时间窗口：今天\n\n这是首页摘要。\n\n<!--more-->\n\n这是正文独有。\n"
        )
        before, full = publish.split_more(
            "时间窗口：今天\n\n这是首页摘要。\n\n<!--more-->\n\n这是正文独有。\n"
        )
        summary = publish._listing_summary(publish.render_markdown(before).strip())
        self.assertIn("时间窗口：今天", summary)
        self.assertIn("这是首页摘要。", summary)
        self.assertNotIn("这是正文独有。", summary)
        self.assertIn("这是正文独有。", publish.render_markdown(full))
        self.assertIn("<p>", rendered)


def make_site(tmp: Path, with_records: bool = True) -> None:
    """A throwaway site root: the hand-written assets plus (optionally) the real records."""
    for folder in ("css", "js", "images", "favicons"):
        shutil.copytree(SITE / folder, tmp / folder)
    daily = tmp / "daily"
    daily.mkdir()
    for name in ("feed.css", "feed.js"):
        shutil.copy2(SITE / "daily" / name, daily / name)
    posts = tmp / "_publisher" / "posts"
    if with_records:
        shutil.copytree(SITE / "_publisher" / "posts", posts)
    else:
        posts.mkdir(parents=True)


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HERE / "publish.py"), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**__import__("os").environ, "PYTHONUTF8": "1"},
    )


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="site-publish-"))
        make_site(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def read(self, rel: str) -> str:
        return (self.tmp / rel).read_text(encoding="utf-8")

    def _post(self, **kwargs) -> "publish.Post":
        return publish.Post(
            title=kwargs["title"],
            when=publish.parse_when(kwargs["date"]),
            slug=publish.validate_slug(kwargs["slug"]),
            categories=[(n, publish.urlize(n)) for n in kwargs["categories"]],
            tags=[(n, publish.urlize(n)) for n in kwargs.get("tags", [])],
            body_html=kwargs.get("body_html", publish.render_markdown(BODY)),
            summary_html=kwargs.get("summary_html", "<p>测试摘要。</p>"),
            description=kwargs.get("description", "测试摘要。"),
            headline=kwargs.get("headline", ""),
        )

    def test_checked_in_site_matches_a_fresh_build(self):
        """The committed pages are exactly what the records build to."""
        items = json.loads((SITE / "daily" / "items.json").read_text(encoding="utf-8"))
        files = sitegen.build_site(SITE, sitegen.load_records(SITE / "_publisher" / "posts"), [i["date"] for i in items])
        stale = []
        for rel, text in files.items():
            path = SITE / rel
            if not path.is_file() or path.read_text(encoding="utf-8").replace("\r\n", "\n") != text:
                stale.append(rel)
        self.assertEqual(stale, [], "run: python _publisher/publish.py --rebuild")
        removable, kept = sitegen.stale_dirs(SITE, files)
        self.assertEqual((removable, kept), ([], []))

    def test_rebuild_is_stable(self):
        first = publish.rebuild_only(self.tmp)
        self.assertIn("index.html", first)
        self.assertEqual(publish.rebuild_only(self.tmp), [])

    def test_publish_adds_post_everywhere_and_is_idempotent(self):
        publish.rebuild_only(self.tmp)
        post = self._post(
            title="Luna 全双工语音日报（2026-11-21）",
            date="2026-11-21",
            slug="lumi-2026-11-21",
            categories=["日报"],
            tags=["lumi"],
            summary_html="<p>时间窗口：昨天到今天</p>\n<p>本期 7 条：测试条目。</p>",
            headline="测试头条",
        )
        changed = publish.publish(self.tmp, post)
        self.assertIn("_publisher/posts/lumi-2026-11-21.json", changed)
        self.assertIn("post/lumi-2026-11-21/index.html", changed)
        home = self.read("index.html")
        day = home[home.index('id="日报"'):home.index('id="每日交易资讯"')]
        self.assertIn("<b>11-21</b>周六", day)
        self.assertIn("<h3>测试头条</h3>", day)
        self.assertIn("Luna 全双工语音日报 · 7 条", day)
        self.assertIn("<p>本期 7 条：测试条目。</p>", day)
        self.assertNotIn("时间窗口", day)
        # The previous day's cards drop to the compact list.
        self.assertIn('<ul class="compact"><li><a href="/post/lumi-2026-11-20/">', day)
        self.assertIn('<a href="/post/agent-app-2026-11-20/">', day)
        self.assertIn('<a href="/post/lumi-2026-11-19/">', day)
        page = self.read("post/lumi-2026-11-21/index.html")
        self.assertIn("<h1>Luna 全双工语音日报（2026-11-21）</h1>", page)
        self.assertIn('<a class="tag" href="/tags/lumi/">#lumi</a>', page)
        self.assertIn('data-repo="fuCking-Life/fuCking-Life.github.io"', page)
        self.assertIn("/post/lumi-2026-11-21/", self.read("post/index.html"))
        self.assertIn("/post/lumi-2026-11-21/", self.read("categories/日报/index.html"))
        self.assertIn("/post/lumi-2026-11-21/", self.read("tags/lumi/index.html"))
        for rel in ("index.xml", "post/index.xml", "categories/日报/index.xml", "tags/lumi/index.xml", "sitemap.xml"):
            text = self.read(rel)
            ET.fromstring(text.encode("utf-8"))
            self.assertIn("https://fuCking-Life.github.io/post/lumi-2026-11-21/", text, rel)
        rss = ET.fromstring(self.read("index.xml").encode("utf-8"))
        first = rss.find("channel/item")
        self.assertEqual(first.findtext("title"), "Luna 全双工语音日报（2026-11-21）")
        self.assertEqual(first.findtext("pubDate"), "Sat, 21 Nov 2026 08:00:00 +0800")
        self.assertEqual(publish.publish(self.tmp, post), [])

    def test_learning_note_title_callouts_and_lead(self):
        body = publish.mark_digest_meta(
            publish.render_markdown(
                "覆盖范围：测试项目。\n\n正文。\n\n> **修正：** 不是反思。\n\n> **原则：** 交给模型。\n\n> 普通引用。\n"
            )
        )
        post = self._post(
            title="Agent 学习笔记（2026-11-22）：测试主题",
            date="2026-11-22",
            slug="learn-agent-2026-11-22",
            categories=["Agent 学习笔记"],
            tags=["LangGraph"],
            body_html=body,
            summary_html="<p>覆盖范围：测试项目。</p>\n<p>正文。</p>",
        )
        publish.publish(self.tmp, post)
        page = self.read("post/learn-agent-2026-11-22/index.html")
        self.assertIn("<h1>测试主题</h1>", page)
        self.assertIn('<p class="digest-meta">覆盖范围：测试项目。</p>', page)
        self.assertIn('<blockquote class="ln-fix">', page)
        self.assertIn('<blockquote class="ln-key">', page)
        self.assertIn("<blockquote>\n<p>普通引用。</p>", page)
        self.assertIn('class="on" aria-current="page" href="/categories/agent-学习笔记/"', page)
        home = self.read("index.html")
        notes = home[home.index('id="agent-学习笔记"'):]
        self.assertIn('<h3><a href="/post/learn-agent-2026-11-22/">测试主题</a></h3><p>正文。</p>', notes)
        self.assertIn("11-22 周日 · LangGraph", notes)
        # Prev/next stay inside the column.
        self.assertIn('href="/post/learn-agent-2026-11-19/"><small>← 上一篇</small>什么才算 agent', page)

    def test_republish_moves_category_and_drops_empty_terms(self):
        first = self._post(title="临时文章", date="2026-11-20", slug="tmp-move", categories=["测试栏目"], tags=["临时"])
        publish.publish(self.tmp, first)
        self.assertTrue((self.tmp / "categories" / "测试栏目" / "index.html").is_file())
        home = self.read("index.html")
        self.assertIn('id="测试栏目"', home)
        moved = self._post(title="临时文章", date="2026-11-20", slug="tmp-move", categories=["Agent 学习笔记"])
        changed = publish.publish(self.tmp, moved)
        self.assertIn("categories/测试栏目/ (removed)", changed)
        self.assertIn("tags/临时/ (removed)", changed)
        self.assertFalse((self.tmp / "categories" / "测试栏目").exists())
        self.assertFalse((self.tmp / "tags" / "临时").exists())
        self.assertNotIn("测试栏目", self.read("index.html"))
        self.assertNotIn("测试栏目", self.read("categories/index.html"))
        self.assertIn("/post/tmp-move/", self.read("categories/agent-学习笔记/index.html"))

    def test_remove_post_from_cli(self):
        publish.rebuild_only(self.tmp)
        self.assertTrue((self.tmp / "post/trading-2026-11-18/index.html").is_file())
        proc = run_cli("--root", str(self.tmp), "--remove", "trading-2026-11-18")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("removed /post/trading-2026-11-18/", proc.stdout)
        self.assertIn("removed post/trading-2026-11-18/", proc.stdout)
        self.assertFalse((self.tmp / "_publisher/posts/trading-2026-11-18.json").exists())
        self.assertFalse((self.tmp / "post/trading-2026-11-18").exists())
        self.assertNotIn("trading-2026-11-18", self.read("index.html"))
        self.assertNotIn("trading-2026-11-18", self.read("sitemap.xml"))
        proc = run_cli("--root", str(self.tmp), "--remove", "trading-2026-11-18")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("no post record", proc.stderr)

    def test_cli_publish_with_headline_and_dry_run(self):
        body = self.tmp / "body.md"
        body.write_text("时间窗口：今天\n\n首页摘要。\n\n<!--more-->\n\n## 小节\n\n正文。\n", encoding="utf-8")
        args = [
            "--root", str(self.tmp), "--title", "每日交易资讯（2026-11-20）", "--date", "2026-11-20",
            "--slug", "trading-2026-11-20", "--category", "每日交易资讯", "--tag", "每日交易资讯",
            "--headline", "测试 交易 头条", "--body-file", str(body),
        ]
        proc = run_cli(*args, "--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("would update post/trading-2026-11-20/index.html", proc.stdout)
        self.assertFalse((self.tmp / "post" / "trading-2026-11-20").exists())
        self.assertFalse((self.tmp / "_publisher/posts/trading-2026-11-20.json").exists())
        proc = run_cli(*args)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("published /post/trading-2026-11-20/", proc.stdout)
        record = json.loads(self.read("_publisher/posts/trading-2026-11-20.json"))
        self.assertEqual(record["headline"], "测试 交易 头条")
        self.assertEqual(record["date"], "2026-11-20T08:00:00+08:00")
        self.assertNotIn("正文", record["summary_html"])
        home = self.read("index.html")
        trade = home[home.index('id="每日交易资讯"'):home.index('id="agent-学习笔记"')]
        self.assertIn("测试 交易 头条", trade)
        self.assertIn("<p>首页摘要。</p>", trade)
        self.assertEqual(trade.count('class="entry"'), 2)
        proc = run_cli(*args)
        self.assertIn("no file changes", proc.stdout)

    def test_html_body_and_explicit_summary(self):
        payload = self.tmp / "post.json"
        payload.write_text(
            json.dumps(
                {
                    "title": "HTML 正文",
                    "date": "2026-11-20T21:30:00+08:00",
                    "slug": "html-body",
                    "categories": ["日报"],
                    "body": "<p>原样 <em>HTML</em>。</p>",
                    "format": "html",
                    "summary": "手写 <摘要>",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        proc = run_cli("--root", str(self.tmp), "--input", str(payload))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        page = self.read("post/html-body/index.html")
        self.assertIn("<p>原样 <em>HTML</em>。</p>", page)
        record = json.loads(self.read("_publisher/posts/html-body.json"))
        self.assertEqual(record["summary_html"], "<p>手写 &lt;摘要&gt;</p>")
        self.assertEqual(record["date"], "2026-11-20T21:30:00+08:00")

    def test_foreign_files_keep_a_stale_directory(self):
        legacy = self.tmp / "post" / "legacy"
        legacy.mkdir(parents=True)
        (legacy / "index.html").write_text("old", encoding="utf-8")
        (legacy / "photo.png").write_bytes(b"png")
        proc = run_cli("--root", str(self.tmp), "--rebuild")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("left post/legacy/ in place", proc.stderr)
        self.assertTrue((legacy / "photo.png").is_file())
        (legacy / "photo.png").unlink()
        proc = run_cli("--root", str(self.tmp), "--rebuild")
        self.assertIn("removed post/legacy/", proc.stdout)
        self.assertFalse(legacy.exists())

    def test_empty_site_builds(self):
        empty = Path(tempfile.mkdtemp(prefix="site-empty-"))
        try:
            make_site(empty, with_records=False)
            publish.rebuild_only(empty)
            self.assertIn("还没有文章", (empty / "index.html").read_text(encoding="utf-8"))
            ET.fromstring((empty / "index.xml").read_bytes())
        finally:
            shutil.rmtree(empty)

    def test_rejects_path_escape_and_missing_category(self):
        for bad in ("../x", "a//b", "a/../b", "a\\b", "a b"):
            with self.assertRaises(publish.PublishError):
                publish.validate_slug(bad)
        proc = run_cli("--root", str(self.tmp), "--title", "t", "--date", "2026-11-20", "--slug", "x", "--body", "b")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("category", proc.stderr)


class RecordTests(unittest.TestCase):
    def record(self, **kw):
        base = dict(
            slug="s", title="t", when=publish.parse_when("2026-11-19"), categories=["日报"], tags=[],
            body_html="<p>正文</p>", summary_html="<p>摘要</p>", description="",
        )
        base.update(kw)
        return sitegen.PostRecord(**base)

    def test_titles(self):
        self.assertEqual(self.record(title="Agent 学习笔记（2026-11-19）：什么才算 agent").display_title, "什么才算 agent")
        self.assertEqual(self.record(title="每日交易资讯（2026-11-19）").display_title, "每日交易资讯（2026-11-19）")
        self.assertEqual(self.record(title="Luna 全双工语音日报（2026-11-19）").series_title, "Luna 全双工语音日报")

    def test_summary_text_and_count(self):
        rec = self.record(summary_html="<p>时间窗口：昨天</p>\n<p>本期 10 条：甲、乙。</p>")
        self.assertEqual(rec.summary_text, "本期 10 条：甲、乙。")
        self.assertEqual(rec.item_count, 10)
        only_meta = self.record(summary_html="<p>2026年11月17日（星期二）</p>")
        self.assertEqual(only_meta.summary_text, "2026年11月17日（星期二）")
        self.assertIsNone(only_meta.item_count)

    def test_round_trip(self):
        rec = self.record(headline="头条", tags=["a"])
        again = sitegen.PostRecord.from_dict(json.loads(rec.to_json()))
        self.assertEqual(again.to_json(), rec.to_json())

    def test_reading_minutes(self):
        self.assertEqual(self.record(body_html="<p>" + "字" * 1200 + "</p>").reading_minutes, 3)
        self.assertEqual(self.record(body_html="<p>短</p>").reading_minutes, 1)


def sample_item(**overrides):
    item = {
        "id": "sample-item",
        "date": "2026-11-17",
        "seq": 1,
        "source_brief": "lumi",
        "title": "标题 <示例>",
        "summary": "讲解正文",
        "insight": {"heading": "对 Luna 的启发", "body": "启发正文"},
        "relevance": 4,
        "domains": ["语音 Agent"],
        "types": ["技术解读"],
        "tags": ["标签甲"],
        "source": {"name": "Ada", "handle": "@ada", "identity": "研究员"},
        "links": [{"label": "原帖", "url": "https://example.com/a"}],
        "archive_url": "/post/lumi-2026-11-17/",
        "posted_at": "11-16 20:05",
    }
    item.update(overrides)
    return item


class DigestTests(unittest.TestCase):
    def test_card_field_order_and_escapes(self):
        html = publish.render_item_card(sample_item(), 2)
        order = [
            "2026-11-17",
            "当天日报第 2 条",
            "相关度",
            "标题 &lt;示例&gt;",
            "讲解正文",
            "对 Luna 的启发",
            "启发正文",
            ">标签甲<",
            "来源 @ada · 研究员",
            "原帖",
            "归档",
            "发帖日期 11-16 20:05",
        ]
        positions = [html.find(part) for part in order]
        self.assertTrue(all(pos >= 0 for pos in positions), html)
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("报日", html)
        self.assertNotIn("对座舱的启发", html)

    def test_card_omits_missing_insight_and_same_day_posted_at(self):
        raw = sample_item(insight=None, posted_at="2026-11-17", links=[], tags=[])
        html = publish.render_item_card(raw, 1)
        self.assertNotIn("blockquote", html)
        self.assertNotIn("发帖日期", html)
        self.assertIn("当天日报第 1 条", html)
        self.assertIn(">归档</a>", html)

    def test_upsert_replaces_by_id_and_assigns_seq(self):
        first = sample_item(id="keep-me", seq=1, title="旧")
        other = sample_item(id="other", seq=2, title="另一条", source_brief="agent-app")
        incoming = sample_item(id="keep-me", seq=1, title="新")
        fresh = sample_item(id="new-item", title="追加")
        fresh.pop("seq")
        merged = publish.upsert_items([first, other], [incoming, fresh])
        ids = [item["id"] for item in merged]
        self.assertEqual(ids, ["keep-me", "other", "new-item"])
        self.assertEqual(merged[0]["title"], "新")
        self.assertEqual(merged[1]["title"], "另一条")
        self.assertEqual(merged[2]["seq"], 3)
        updated = sample_item(id="new-item", title="追加")
        updated.pop("seq")
        again = publish.upsert_items(merged, [updated])
        self.assertEqual([item["seq"] for item in again], [1, 2, 3])
        self.assertEqual(publish.day_ordinals(merged)["other"], 2)

    def test_rejects_bad_relevance_domain_and_duplicate(self):
        bad = sample_item(relevance=True)
        with self.assertRaises(publish.PublishError):
            publish.validate_item(bad)
        bad = sample_item(domains=["座舱"])
        with self.assertRaises(publish.PublishError):
            publish.validate_item(bad)
        with self.assertRaises(publish.PublishError):
            publish.upsert_items([], [sample_item(id="same"), sample_item(id="same", seq=2)])

    def test_filters_and_search(self):
        rows = [
            sample_item(id="high", relevance=5, domains=["论文", "开源"], types=["论文"], tags=["甲"]),
            sample_item(id="mid", relevance=3, domains=["论文"], types=["技术解读"], tags=["乙"],
                        source={"name": "", "handle": "@JonPTaylor", "identity": ""}),
        ]
        rows = [publish.validate_item(row) for row in rows]
        self.assertEqual(
            [item["id"] for item in publish.filter_items(rows, min_relevance=5)],
            ["high"],
        )
        both = publish.filter_items(rows, match="all", domains=["论文", "开源"])
        self.assertEqual([item["id"] for item in both], ["high"])
        either = publish.filter_items(rows, match="any", domains=["开源"], types=["技术解读"])
        self.assertEqual({item["id"] for item in either}, {"high", "mid"})
        found = publish.filter_items(rows, query="@jonptaylor")
        self.assertEqual([item["id"] for item in found], ["mid"])
        self.assertEqual(publish.filter_items(rows, query="甲"), [])

    def test_seed_matches_the_drafts(self):
        path = HERE / "drafts" / "items-2026-11-17.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = publish.upsert_items([], raw)
        self.assertEqual(len(items), 17)
        self.assertEqual(len({item["id"] for item in items}), 17)
        self.assertTrue(all(item["date"] == "2026-11-17" for item in items))
        titles = "\n".join(item["title"] for item in items)
        self.assertEqual(titles.count("Voice Reader"), 1)
        self.assertEqual(titles.count("Marimba"), 1)
        self.assertEqual(sum("d1.2" in item["title"] for item in items), 1)
        self.assertIn("Gurman", titles)
        self.assertIn("Qwen4-Max", titles)
        self.assertIn("WorkSpaces", titles)
        blob = path.read_text(encoding="utf-8")
        self.assertNotIn("报日", blob)
        self.assertNotIn("对座舱的启发", blob)
        self.assertIn("FastDialgoueSidon", blob)
        self.assertIn("设计时器", blob)
        lumi = [item for item in items if item["source_brief"] == "lumi"]
        agent = [item for item in items if item["source_brief"] == "agent-app"]
        self.assertEqual(len(lumi), 11)
        self.assertEqual(len(agent), 6)
        self.assertTrue(all(item["insight"]["heading"] == "对 Luna 的启发" for item in lumi))
        self.assertTrue(all(item["insight"] is None for item in agent))
        self.assertIn(5, {item["relevance"] for item in items})
        self.assertIn(2, {item["relevance"] for item in items})
        self.assertTrue(all(1 <= item["relevance"] <= 5 for item in items))
        script = (SITE / "daily" / "feed.js").read_text(encoding="utf-8")
        for name in publish.DOMAINS + publish.TYPES:
            self.assertIn(json.dumps(name, ensure_ascii=False)[1:-1], script)
        ordinals = publish.day_ordinals(items)
        self.assertEqual(ordinals["lumi-2026-11-17-voice-reader"], 1)
        self.assertEqual(ordinals["agent-app-2026-11-17-aws-workspaces"], 17)
        for rel in ("daily/feed.js", "daily/feed.css", "_publisher/templates/daily.html"):
            text = (SITE / rel).read_text(encoding="utf-8")
            self.assertNotIn("报日", text, rel)
        for rel in ("daily/feed.js", "_publisher/templates/daily.html"):
            self.assertIn("日报", (SITE / rel).read_text(encoding="utf-8"), rel)


class DigestPublishTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="site-digest-"))
        make_site(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_publish_items_upserts_and_keeps_posts(self):
        seed = HERE / "drafts" / "items-2026-11-17.json"
        proc = run_cli("--root", str(self.tmp), "--items", str(seed))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("published /daily/", proc.stdout)
        stored = json.loads((self.tmp / "daily" / "items.json").read_text(encoding="utf-8"))
        self.assertEqual(len(stored), 17)
        page = (self.tmp / "daily" / "index.html").read_text(encoding="utf-8")
        self.assertIn("搜索标题、讲解、来源", page)
        self.assertIn("只看相关度不低于所选档的消息，", page)
        self.assertIn("再点一次取消。", page)
        self.assertIn('data-view="favorites">收藏</button>', page)
        self.assertIn('id="sync-panel"', page)
        self.assertNotIn("报日", page)
        home = (self.tmp / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="/daily/"', home)
        self.assertIn("/post/learn-agent-2026-11-19/", home)
        sitemap = (self.tmp / "sitemap.xml").read_text(encoding="utf-8")
        self.assertIn(
            "<loc>https://fuCking-Life.github.io/daily/</loc>\n    <lastmod>2026-11-17T08:00:00+08:00</lastmod>",
            sitemap,
        )

        proc = run_cli("--root", str(self.tmp), "--items", str(seed))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("no file changes", proc.stdout)

        changed = sample_item(id="lumi-2026-11-17-voice-reader", seq=1, title="替换后的标题")
        payload = self.tmp / "one.json"
        payload.write_text(json.dumps([changed]), encoding="utf-8")
        proc = run_cli("--root", str(self.tmp), "--items", str(payload))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        stored = json.loads((self.tmp / "daily" / "items.json").read_text(encoding="utf-8"))
        self.assertEqual(len(stored), 17)
        titles = {item["id"]: item["title"] for item in stored}
        self.assertEqual(titles["lumi-2026-11-17-voice-reader"], "替换后的标题")
        self.assertIn("Qwen4-Max", " ".join(titles.values()))

    def test_template_matches_the_live_daily_page(self):
        shell = sitegen.Shell(asset_version=sitegen.asset_version(SITE), year=2026, nav=sitegen.build_nav())
        rendered = sitegen.render_daily_page(shell, SITE)
        live = (SITE / "daily" / "index.html").read_text(encoding="utf-8")
        self.assertEqual(rendered, live.replace("\r\n", "\n"))
        # The feed page shares the site header, theme switch and stylesheet.
        self.assertIn('<header class="site-header">', live)
        self.assertIn('<a class="on" aria-current="page" href="/daily/">日报</a>', live)
        self.assertIn('href="/css/site.css?v=', live)
        self.assertIn('class="theme-btn"', live)
        self.assertNotIn("@@", live)


class DigestStructureTests(unittest.TestCase):
    BODY = (
        '<p class="digest-meta">时间窗口：昨天</p>\n'
        "<p>本期 2 条：甲、乙。</p>\n"
        '<h3 id="a">1. 甲消息</h3>\n'
        "<p>相关度 5/5。标签：端上语音、开源发布。</p>\n"
        "<p>来源：某人。发帖日期：2026-11-20。</p>\n"
        "<p>正文甲。</p>\n"
        "<blockquote>\n<p><strong>对 Luna 的启发</strong>\n启发甲。</p></blockquote>\n"
        '<p>原帖：<a href="https://example.com/a">原帖</a></p>\n'
        "<h3>分组标题</h3>\n"
        "<p><strong>乙消息</strong></p>\n"
        "<p>相关度 3/5。标签：论文。</p>\n"
        "<p>正文乙。</p>\n"
        "<h2>结语</h2>\n<p>收尾。</p>"
    )

    def test_items_are_grouped_with_a_contents_list(self):
        html, items = sitegen.structure_digest(self.BODY)
        self.assertEqual(items, [("a", "甲消息", 5), ("item-2", "乙消息", 3)])
        self.assertLess(html.index('<nav class="toc"'), html.index('id="a"'))
        self.assertGreater(html.index('<nav class="toc"'), html.index("本期 2 条"))
        self.assertIn('<span class="it-no">1</span><span>甲消息</span>', html)
        self.assertIn('<span class="it-chip">端上语音</span><span class="it-chip">开源发布</span>', html)
        self.assertEqual(html.count('<i class="on"></i>'), 5 + 3)
        self.assertIn('<p class="it-src">来源', html)
        self.assertIn('<p class="it-links">原帖', html)
        self.assertIn('<blockquote class="it-insight">', html)
        self.assertNotIn("相关度 5/5。", html)
        # A non-item heading ends the item before it and stays in the flow.
        self.assertLess(html.index("</section>"), html.index("<h3>分组标题</h3>"))
        self.assertIn("<h2>结语</h2>\n<p>收尾。</p>", html)

    def test_plain_posts_are_left_alone(self):
        body = "<p>一段话。</p>\n<h3>小标题</h3>\n<p>又一段。</p>"
        self.assertEqual(sitegen.structure_digest(body), (body, []))

    def test_summary_drops_the_trading_disclaimer(self):
        rec = sitegen.PostRecord(
            slug="trading-x", title="t", when=sitegen.datetime(2026, 11, 20, tzinfo=sitegen.TZ),
            categories=["每日交易资讯"], tags=[], body_html="",
            summary_html="<p>本文只做信息整理，不构成投资建议。成交缩量。</p>", description="",
        )
        self.assertEqual(rec.summary_text, "成交缩量。")


if __name__ == "__main__":
    unittest.main()
