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
        page = publish.render_single(
            publish.Post(
                title="t",
                when=publish.parse_when("2026-11-17"),
                slug="sample",
                categories=[("日报", "日报")],
                tags=[],
                body_html=html,
                summary_html="<p>时间窗口：今天</p>",
                description="时间窗口：今天",
            )
        )
        self.assertIn(".post .post-content { font-size: 15px; }", page)
        self.assertIn(".post .post-content .digest-meta {", page)
        self.assertIn('href="/css/style.min.12394ad1dd77abc5f2b8ac9fff89a6f77f11f1db9126284cdea41236936f5c56.css"', page)

    def test_more_separator_is_not_rendered(self):
        before, full = publish.split_more("摘要段落。\n\n<!--more-->\n\n正文段落。\n")
        self.assertEqual(before, "摘要段落。")
        self.assertNotIn("<!--more-->", full)
        summary = publish.first_paragraph(publish.render_markdown(before))
        body = publish.render_markdown(full)
        self.assertIn("摘要段落", summary)
        self.assertIn("正文段落", body)
        self.assertNotIn("<!--more-->", body)


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="site-publish-"))
        for name in (
            "index.html",
            "index.xml",
            "sitemap.xml",
            "404.html",
            "robots.txt",
        ):
            shutil.copy2(SITE / name, self.tmp / name)
        for folder in ("post", "categories", "tags", "life", "page", "about", "css", "js"):
            shutil.copytree(SITE / folder, self.tmp / folder)
        self.untouched = [
            "post/inithugo/index.html",
            "post/cocoapods/privatespecs/index.html",
            "post/yy/yyasynclayer/index.html",
            "life/initlife/index.html",
            "life/index.html",
            "page/2/index.html",
            "page/1/index.html",
            "about/index.html",
            "404.html",
            "categories/hugo/index.html",
            "categories/objc/index.html",
            "css/style.min.12394ad1dd77abc5f2b8ac9fff89a6f77f11f1db9126284cdea41236936f5c56.css",
            "js/anatole-header.min.0c05c0a90d28c968a1cad4fb31abd0b8e1264e788ccefed022ae1d3b6f627514.js",
        ]
        self.before = {rel: _sha(self.tmp / rel) for rel in self.untouched}
        self.integrity = 'sha256-EjlK0d13q8XyuKyf/4mm938R8duRJihM3qQSNpNvXFY='

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _publish(self, **kwargs):
        post = publish.Post(
            title=kwargs["title"],
            when=publish.parse_when(kwargs["date"]),
            slug=publish.validate_slug(kwargs["slug"]),
            categories=kwargs["categories"],
            tags=kwargs.get("tags", []),
            body_html=kwargs["body_html"],
            summary_html=kwargs["summary_html"],
            description=kwargs["description"],
        )
        return publish.publish(self.tmp, post), post

    def test_archive_and_homepage_round_trip_without_edits(self):
        home = (self.tmp / "index.html").read_text(encoding="utf-8")
        matches = list(publish.CARD_RE.finditer(home))
        self.assertEqual(len(matches), 12)
        gaps = [home[a.end() : b.start()] for a, b in zip(matches, matches[1:])]
        self.assertTrue(gaps)
        self.assertTrue(all(gap == gaps[0] for gap in gaps))
        rebuilt = gaps[0].join(match.group(0) for match in matches)
        self.assertEqual(home[matches[0].start() : matches[-1].end()], rebuilt)
        for relative in ("post/index.html", "categories/index.html", "categories/objc/index.html"):
            html = (self.tmp / relative).read_text(encoding="utf-8")
            start, end = publish.find_archive(html)
            inner = html[start:end]
            prefix, years = publish.split_years(inner)
            self.assertEqual(prefix + "".join(chunk for _, chunk in years), inner)
            for _year, chunk in years:
                head, tail = publish.split_tail(chunk)
                entries = publish.ENTRY_RE.findall(head)
                self.assertGreaterEqual(len(entries), 1)
                self.assertTrue(chunk.endswith(tail))

    def test_publish_prepends_and_is_idempotent(self):
        body_html = publish.render_markdown(BODY)
        summary = publish.first_paragraph(body_html)
        changed, post = self._publish(
            title="发布器测试标题",
            date="2026-11-17",
            slug="lumi-2026-11-17",
            categories=[("日报", "日报"), ("lumi", "lumi")],
            tags=[("lumi", "lumi"), ("日报", "日报")],
            body_html=body_html,
            summary_html=summary,
            description=publish.plain_text(summary)[:180],
        )
        self.assertIn("post/lumi-2026-11-17/index.html", changed)
        page = (self.tmp / "post/lumi-2026-11-17/index.html").read_text(encoding="utf-8")
        self.assertIn(self.integrity, page)
        self.assertIn('integrity="sha256-DAXAqQ0oyWihytT7MavQuOEmTniMzv7QIq4dO29idRQ="', page)
        self.assertIn('href="/post/lumi-2026-11-17/"', page)
        self.assertIn("发布器测试标题", page)
        self.assertIn("Tue, Nov 17, 2026", page)
        self.assertIn('href="/categories/日报/"', page)
        self.assertIn('href="/tags/lumi/"', page)
        self.assertIn('<h1 id="固定标题">', page)
        self.assertIn("busuanzi_value_page_pv", page)
        self.assertIn("utteranc.es/client.js", page)
        self.assertNotIn("@@", page)

        home = (self.tmp / "index.html").read_text(encoding="utf-8")
        self.assertLess(
            home.find("/post/lumi-2026-11-17/"),
            home.find("/post/cocoapods/privatespecs/"),
        )
        self.assertIn("这是发布器测试用的第一段，不是新闻。", home)
        self.assertEqual(home.count("/post/lumi-2026-11-17/"), 1)
        self.assertIn('<div class="pagination">', home)

        posts = (self.tmp / "post/index.html").read_text(encoding="utf-8")
        self.assertLess(posts.find(">2026</div>"), posts.find(">2021</div>"))
        self.assertIn("Nov 17", posts)
        self.assertEqual(posts.count("/post/lumi-2026-11-17/"), 1)
        self.assertIn("/post/inithugo/", posts)

        for rel in self.untouched:
            self.assertEqual(self.before[rel], _sha(self.tmp / rel), rel)

        root_feed = ET.fromstring((self.tmp / "index.xml").read_text(encoding="utf-8"))
        post_feed = ET.fromstring((self.tmp / "post/index.xml").read_text(encoding="utf-8"))
        for feed in (root_feed, post_feed):
            titles = [item.findtext("title") for item in feed.findall("./channel/item")]
            self.assertEqual(titles[0], "发布器测试标题")
            self.assertEqual(titles.count("发布器测试标题"), 1)
        self.assertIn("虽迟但到的30岁", [item.findtext("title") for item in root_feed.findall("./channel/item")])
        self.assertNotIn(
            "虽迟但到的30岁",
            [item.findtext("title") for item in post_feed.findall("./channel/item")],
        )

        sitemap = ET.fromstring((self.tmp / "sitemap.xml").read_text(encoding="utf-8"))
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        locs = [node.text for node in sitemap.findall("sm:url/sm:loc", ns)]
        self.assertEqual(locs.count(post.abs_url), 1)
        self.assertIn("https://fuCking-Life.github.io/categories/日报/", locs)
        self.assertIn("https://fuCking-Life.github.io/tags/lumi/", locs)
        self.assertIn("https://fuCking-Life.github.io/post/inithugo/", locs)

        category_page = (self.tmp / "categories" / "日报" / "index.html").read_text(encoding="utf-8")
        self.assertIn("Ermao | 日报", category_page)
        self.assertIn(self.integrity, category_page)
        self.assertEqual(category_page.count("/post/lumi-2026-11-17/"), 1)
        lumi_page = (self.tmp / "categories" / "lumi" / "index.html").read_text(encoding="utf-8")
        self.assertIn("/post/lumi-2026-11-17/", lumi_page)
        tag_page = (self.tmp / "tags" / "日报" / "index.html").read_text(encoding="utf-8")
        self.assertIn("Ermao | 日报", tag_page)
        tags_index = (self.tmp / "tags" / "index.html").read_text(encoding="utf-8")
        self.assertIn("/tags/日报/", tags_index)
        self.assertIn("/tags/lumi/", tags_index)

        cat_feed = ET.fromstring((self.tmp / "categories" / "日报" / "index.xml").read_text(encoding="utf-8"))
        self.assertEqual(cat_feed.findtext("./channel/item/title"), "发布器测试标题")
        term_feed = ET.fromstring((self.tmp / "categories" / "index.xml").read_text(encoding="utf-8"))
        term_titles = [item.findtext("title") for item in term_feed.findall("./channel/item")]
        self.assertEqual(set(term_titles[:2]), {"日报", "lumi"})
        self.assertEqual(term_titles[2], "life")

        again, _post = self._publish(
            title="发布器测试标题",
            date="2026-11-17",
            slug="lumi-2026-11-17",
            categories=[("日报", "日报"), ("lumi", "lumi")],
            tags=[("lumi", "lumi"), ("日报", "日报")],
            body_html=body_html,
            summary_html=summary,
            description=publish.plain_text(summary)[:180],
        )
        self.assertEqual(again, [])
        self.assertEqual((self.tmp / "index.html").read_text(encoding="utf-8").count("/post/lumi-2026-11-17/"), 1)

    def test_older_post_sorts_below_and_existing_category_keeps_old_posts(self):
        body_html = publish.render_markdown(OLDER)
        summary = publish.first_paragraph(body_html)
        self._publish(
            title="发布器测试标题",
            date="2026-11-17T09:30:00",
            slug="lumi-2026-11-17",
            categories=[("日报", "日报")],
            tags=[],
            body_html=body_html,
            summary_html=summary,
            description="desc",
        )
        self._publish(
            title="较早的测试",
            date="2020-01-02",
            slug="agent-app-2020-01-02",
            categories=[("日报", "日报"), ("objc", "objc")],
            tags=[("agent-app", "agent-app")],
            body_html=body_html,
            summary_html=summary,
            description="desc",
        )
        home = (self.tmp / "index.html").read_text(encoding="utf-8")
        self.assertLess(home.find("/post/lumi-2026-11-17/"), home.find("/post/cocoapods/privatespecs/"))
        self.assertLess(home.find("/post/cocoapods/privatespecs/"), home.find("/post/agent-app-2020-01-02/"))
        objc = (self.tmp / "categories" / "objc" / "index.html").read_text(encoding="utf-8")
        self.assertIn("/post/objc_essence_0/", objc)
        self.assertIn("/post/agent-app-2020-01-02/", objc)
        self.assertLess(objc.find(">2021</div>"), objc.find(">2020</div>"))
        daily = (self.tmp / "categories" / "日报" / "index.html").read_text(encoding="utf-8")
        self.assertLess(daily.find("/post/lumi-2026-11-17/"), daily.find("/post/agent-app-2020-01-02/"))
        page = (self.tmp / "post/lumi-2026-11-17/index.html").read_text(encoding="utf-8")
        self.assertIn("Tue, Nov 17, 2026", page)
        rss = (self.tmp / "index.xml").read_text(encoding="utf-8")
        self.assertIn("Tue, 17 Nov 2026 09:30:00 +0800", rss)

    def test_html_body_and_explicit_summary(self):
        changed, _post = self._publish(
            title="HTML 测试",
            date="2026-11-17T00:30:00Z",
            slug="agent-app-2026-11-17",
            categories=[("Agent App", "agent-app")],
            tags=[],
            body_html="<p>第一段保持原样。</p><h2 id=\"x\">第二节</h2>",
            summary_html="<p>" + publish.hugo_escape("手工摘要") + "</p>",
            description="手工摘要",
        )
        self.assertTrue(changed)
        page = (self.tmp / "post/agent-app-2026-11-17/index.html").read_text(encoding="utf-8")
        self.assertIn("<p>第一段保持原样。</p><h2 id=\"x\">第二节</h2>", page)
        self.assertIn("Tue, Nov 17, 2026", page)
        rss = (self.tmp / "index.xml").read_text(encoding="utf-8")
        self.assertIn("Tue, 17 Nov 2026 08:30:00 +0800", rss)
        home = (self.tmp / "index.html").read_text(encoding="utf-8")
        self.assertIn("手工摘要", home)
        self.assertTrue((self.tmp / "categories" / "agent-app" / "index.html").is_file())

    def test_republish_can_rename_and_move_category(self):
        body_html = "<p>同一篇。</p>"
        summary = "<p>同一篇。</p>"
        common = dict(
            date="2026-11-17",
            slug="lumi-2026-11-17",
            body_html=body_html,
            summary_html=summary,
            description="同一篇。",
        )
        self._publish(title="旧标题", categories=[("日报", "日报")], tags=[("lumi", "lumi")], **common)
        self._publish(title="新标题", categories=[("lumi", "lumi")], tags=[], **common)
        home = (self.tmp / "index.html").read_text(encoding="utf-8")
        self.assertEqual(home.count("/post/lumi-2026-11-17/"), 1)
        self.assertIn("新标题", home)
        self.assertNotIn("旧标题", home)
        daily = (self.tmp / "categories" / "日报" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("/post/lumi-2026-11-17/", daily)
        moved = (self.tmp / "categories" / "lumi" / "index.html").read_text(encoding="utf-8")
        self.assertIn("新标题", moved)
        tags = (self.tmp / "tags" / "lumi" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("/post/lumi-2026-11-17/", tags)

    def test_rejects_path_escape(self):
        with self.assertRaises(publish.PublishError):
            publish.validate_slug("../index")
        with self.assertRaises(publish.PublishError):
            publish.validate_slug("bad slug")

    def test_cli_on_copy(self):
        body = self.tmp / "body.md"
        body.write_text(BODY, encoding="utf-8")
        proc = subprocess.run(
            [
                sys.executable,
                str(HERE / "publish.py"),
                "--root",
                str(self.tmp),
                "--title",
                "命令行测试",
                "--date",
                "2026-11-17",
                "--slug",
                "lumi-2026-11-17",
                "--category",
                "日报",
                "--tag",
                "lumi",
                "--body-file",
                str(body),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("published /post/lumi-2026-11-17/", proc.stdout)
        self.assertTrue((self.tmp / "post/lumi-2026-11-17/index.html").is_file())
        proc = subprocess.run(
            [sys.executable, str(HERE / "publish.py"), "--root", str(self.tmp), "--slug", "nope"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 2)


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

    def test_sidebar_link_is_idempotent(self):
        html = (SITE / "index.html").read_text(encoding="utf-8")
        once = publish.insert_daily_nav(html)
        twice = publish.insert_daily_nav(once)
        self.assertEqual(once, twice)
        self.assertEqual(once.count('href="/daily/"'), 2)
        self.assertIn(">日报</a>", once)
        self.assertGreaterEqual(once.find('href="/daily/"'), 0)
        self.assertLess(once.find('href="/daily/"'), once.find('href="/post/"'))


class DigestPublishTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="site-digest-"))
        for name in ("index.html", "index.xml", "sitemap.xml", "404.html", "robots.txt"):
            shutil.copy2(SITE / name, self.tmp / name)
        for folder in ("post", "categories", "tags", "css", "js"):
            shutil.copytree(SITE / folder, self.tmp / folder)
        daily = self.tmp / "daily"
        daily.mkdir()
        shutil.copy2(SITE / "daily" / "feed.css", daily / "feed.css")
        shutil.copy2(SITE / "daily" / "feed.js", daily / "feed.js")
        self.frozen = [
            "post/yy/yyasynclayer/index.html",
            "post/inithugo/index.html",
            "css/style.min.12394ad1dd77abc5f2b8ac9fff89a6f77f11f1db9126284cdea41236936f5c56.css",
            "js/anatole-header.min.0c05c0a90d28c968a1cad4fb31abd0b8e1264e788ccefed022ae1d3b6f627514.js",
        ]
        self.before = {rel: _sha(self.tmp / rel) for rel in self.frozen}

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_publish_items_upserts_and_leaves_old_posts(self):
        seed = HERE / "drafts" / "items-2026-11-17.json"
        proc = subprocess.run(
            [sys.executable, str(HERE / "publish.py"), "--root", str(self.tmp), "--items", str(seed)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("published /daily/", proc.stdout)
        stored = json.loads((self.tmp / "daily" / "items.json").read_text(encoding="utf-8"))
        self.assertEqual(len(stored), 17)
        page = (self.tmp / "daily" / "index.html").read_text(encoding="utf-8")
        self.assertIn("搜索标题、讲解、来源", page)
        self.assertIn("只看相关度不低于所选档的消息，", page)
        self.assertIn("再点一次取消。", page)
        self.assertNotIn("报日", page)
        home = (self.tmp / "index.html").read_text(encoding="utf-8")
        posts = (self.tmp / "post" / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="/daily/"', home)
        self.assertIn('href="/daily/"', posts)
        sitemap = (self.tmp / "sitemap.xml").read_text(encoding="utf-8")
        self.assertIn("https://fuCking-Life.github.io/daily/", sitemap)
        for rel in self.frozen:
            self.assertEqual(self.before[rel], _sha(self.tmp / rel), rel)

        proc = subprocess.run(
            [sys.executable, str(HERE / "publish.py"), "--root", str(self.tmp), "--items", str(seed)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("no file changes", proc.stdout)

        changed = sample_item(id="lumi-2026-11-17-voice-reader", seq=1, title="替换后的标题")
        payload = self.tmp / "one.json"
        payload.write_text(json.dumps([changed]), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(HERE / "publish.py"), "--root", str(self.tmp), "--items", str(payload)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        stored = json.loads((self.tmp / "daily" / "items.json").read_text(encoding="utf-8"))
        self.assertEqual(len(stored), 17)
        titles = {item["id"]: item["title"] for item in stored}
        self.assertEqual(titles["lumi-2026-11-17-voice-reader"], "替换后的标题")
        self.assertIn("Qwen4-Max", " ".join(titles.values()))
        for rel in self.frozen:
            self.assertEqual(self.before[rel], _sha(self.tmp / rel), rel)


if __name__ == "__main__":
    unittest.main()
