# Static publisher

This site is the compiled output of Hugo 0.82 with a customized Anatole theme. The Hugo project (`config.toml`, `content/`, `themes/`) is not in the repository, so new posts are inserted into the existing HTML, RSS, and sitemap instead of rebuilding the site.

The publisher lives in `_publisher/` because GitHub Pages runs Jekyll on this repository, and Jekyll does not publish directories whose names start with `_`. Nothing here is linked from the site navigation. If a `.nojekyll` file is added later, this folder would be copied to the site as ordinary files; it would still not be linked from the nav.

Requires Python 3.9 or newer. The standard library is enough (`zoneinfo` dates). No packages to install.

## Publish a post

From the repository root:

```bash
python3 _publisher/publish.py \
  --title "2026-11-17 日报" \
  --date 2026-11-17 \
  --slug lumi-2026-11-17 \
  --category 日报 \
  --tag lumi \
  --body-file ./digest.md
```

Suggested URLs:

- `/post/lumi-YYYY-MM-DD/` with category `日报` and tag `lumi`
- `/post/agent-app-YYYY-MM-DD/` with category `日报` and tag `agent-app`
- `/post/trading-YYYY-MM-DD/` with category `每日交易资讯` and tag `每日交易资讯`

`每日交易资讯` is a separate column at `/categories/每日交易资讯/`. It is not part of the `/daily/` feed. Publishing a post with that category or tag adds a nav item and a sidebar link labeled `每日交易资讯` on the pages that publish rewrites (the homepage, `/post/`, the new post, and the category and tag pages). The link target is `/categories/每日交易资讯/`. The `日报` link stays. This does not change the `/daily/` vocabulary or `source_brief`.

`--category` and `--tag` can be repeated. A category is required. Tags are optional. The visible name is kept (`Agent App` stays `Agent App`) and the directory is a lowercase slug (`agent-app`, `日报`).

`--date` is interpreted in Asia/Shanghai. A date with no clock time uses 08:00:00. A value that already has a zone (`2026-11-17T00:30:00Z`) is converted to Shanghai time.

The body is Markdown unless you pass `--format html` or set `"format": "html"` in JSON. The theme already prints the title, so the body should start with the text rather than a heading that repeats the title. Put `<!--more-->` on its own line to choose the listing summary (every paragraph before that line); otherwise the summary is the first paragraph. `--summary` and `--description` override those.

Each new post page includes a `<style>` block for digest typography. Hashed files in `css/` and `js/` are not rewritten. Opening paragraphs that start with `时间窗口`, `覆盖范围`, or a date such as `2026年` are marked `class="digest-meta"`.

JSON input (flags override the same fields when you pass them):

```bash
python3 _publisher/publish.py --input post.json
```

```json
{
  "title": "2026-11-17 日报",
  "date": "2026-11-17",
  "slug": "lumi-2026-11-17",
  "categories": ["日报"],
  "tags": ["lumi"],
  "body_file": "digest.md",
  "format": "markdown"
}
```

`--body-file -` reads the body from stdin. `--dry-run` prints the files it would write. Publishing the same slug again replaces that post instead of adding a second copy. `--root` defaults to the repository root.

## Daily feed

`/daily/` is the filterable feed for these briefs. Long posts such as `/post/lumi-2026-11-17/` stay as archives. The page is static HTML, CSS, and JS in `daily/` (Jekyll publishes that directory). Hashed files in `css/` and `js/` are not part of the feed.

Items live in `daily/items.json`. The feed shows a 领域 or 类型 only when at least one item uses it. Counts beside those names follow the current search, relevance, and date, and ignore the domain, type, and tag checkboxes. 相关度 is a 1–5 score against Yonas's interests: agents (architecture, harness, memory, planning, tool use and MCP, multi-agent, computer use, coding agents, long-horizon reliability), LLMs, physical AI / embodied intelligence, and on-device iOS voice agents (Luna). Do not mark every item as 5.

`date` is the Asia/Shanghai calendar day of the digest (`YYYY-MM-DD`, no clock time).

### Vocabulary

Use these names as written. An item may have more than one 领域 and more than one 类型.

领域:

- 语音 Agent
- 全双工
- 打断与轮次
- 语音识别 ASR
- 语音合成 TTS
- 端到端语音
- 端侧
- iOS
- 苹果生态
- 个人 Agent
- Agent 架构
- 工具与 MCP
- 大模型
- 多模态
- 评测与榜单
- 开源
- 论文
- 智能座舱

类型:

- 产品发布
- 模型发布
- 开源
- 技术解读
- 观点讨论
- 评测结果
- 论文
- 行业数据

`source_brief` is `lumi` or `agent-app`.

### Item JSON

`daily/items.json` is a list. The publisher upserts by `id` and rewrites that file plus `daily/index.html`. Pass a list, or an object with an `items` array. Unknown fields are rejected.

```json
{
  "id": "lumi-2026-11-17-voice-reader",
  "date": "2026-11-17",
  "seq": 1,
  "source_brief": "lumi",
  "title": "一句话摘要。",
  "summary": "讲解。",
  "insight": {"heading": "对 Luna 的启发", "body": "只在来源里已有这段时填写。"},
  "relevance": 4,
  "domains": ["语音 Agent", "端侧"],
  "types": ["模型发布"],
  "tags": ["Pipecat"],
  "source": {"name": "", "handle": "@JonPTaylor", "identity": ""},
  "links": [{"label": "原帖", "url": "https://example.com/post"}],
  "archive_url": "/post/lumi-2026-11-17/",
  "posted_at": "11-16 20:05"
}
```

`insight` may be omitted or `null`. `posted_at` is optional and is the source's own time string; leave it out when the digest date already says it. `seq` orders items inside the same day (当天日报第 N 条). If you omit `seq` on a new id, the publisher appends one. `source` needs a `name` or a `handle`. Link URLs are `http(s)` or a site path.

Publish items alone:

```bash
python3 _publisher/publish.py --items _publisher/drafts/items-2026-11-17.json
```

An `--input` object may include both post fields and `items`. The archive post is written only when a markdown or HTML body is present. Item ids already in `daily/items.json` are replaced; other items stay. The Anatole menu and the left sidebar gain a 日报 link on pages the publisher rewrites, including the homepage and `/post/` from a digest publish. `daily/feed.css` and `daily/feed.js` must already be in the site; the publisher does not regenerate them.

Check the script without touching this checkout:

```bash
python3 _publisher/test_publish.py
```

## Files that change

For a new post the publisher writes or updates:

| Path | What changes |
| --- | --- |
| `post/<slug>/index.html` | New post page. Chrome (sidebar, nav, footer, CSS/JS integrity hashes, comments) is copied from the existing single-post layout. |
| `index.html` | Homepage card inserted by date, newest first. |
| `post/index.html` | Posts archive entry, grouped by year. |
| `index.xml` | Site RSS item. This feed also contains the Life section, so the new post is ordered by date among those items. |
| `post/index.xml` | Posts RSS item. |
| `sitemap.xml` | New post URL. `lastmod` on `/`, `/post/`, `/categories/`, the category, and any tags moves forward when the post is newer. |
| `categories/<slug>/index.html` and `index.xml` | Created or updated, and the term is added to `categories/index.html` and `categories/index.xml`. |
| `tags/<slug>/index.html` and `index.xml` | Same for each tag, including `tags/index.html` and `tags/index.xml`. |
| `daily/items.json` | Digest items, upserted by `id` when `--items` or an `items` array is present. |
| `daily/index.html` | The feed page, rewritten from `_publisher/templates/daily.html`. |
| `index.html` and `post/index.html` | A digest publish also ensures the 日报 link is in the nav and sidebar. |
| rewritten HTML pages | A post whose category or tag is 每日交易资讯 also gains that nav and sidebar link, pointing at `/categories/每日交易资讯/`. The 日报 link stays. |
| `sitemap.xml` | `/daily/` is added or its `lastmod` is set from the newest item date. |

Hashed files under `css/` and `js/` are not rewritten. Existing post HTML is not rewritten, except that a normal post publish still inserts the 日报 link into pages it already rewrites. `/life/`, `/about/`, `/page/`, and `404.html` are left alone. `daily/feed.css` and `daily/feed.js` are hand-written.

## Limits

- The homepage and `/post/` already list every old post on one page. The empty pagination block is preserved, and `/page/2/` is an older unlinked render (its title is still "Ermao(二毛)"). New posts are inserted into the existing first page by date. The publisher does not split the list into Hugo-style pages.
- New category and tag pages copy their shell from `categories/hugo/index.html` and `tags/index.html`. Those two files need to stay in the Anatole layout.
- Reading time follows this site's Hugo numbers: whitespace-separated words, then the fuzzy count (`100` when there are fewer than 100) and `ceil(words / 213)` minutes. Chinese text without spaces counts as few words, which is why the old posts say "100 Words".
- Markdown covers headings, paragraphs, lists, links, images, emphasis, fenced code, tables, and blockquotes. Fenced code uses the theme's `highlight` / `chroma` wrapper but is not token-colored the way Hugo's Chroma pass was. Pass `--format html` when you already have HTML.
- Listing order is by calendar date, newest first. A new post dated today is placed at the top. On a category or tag index, a term moves only when this post is newer than the date already shown for that term, or when the term's label changed.
- Republishing can move a post to different categories or tags. The old term page drops the post. The term itself stays on the category or tag index, because other posts may still use it.
- `post/yy/yyasynclayer/` is not in the homepage, the posts archive, or the sitemap. It is left as it is.
- The copyright line stays `© Ermao 2022`, matching every current page.
- GitHub Pages publishes the `master` branch. This pull request does not change the live site until it is merged. After that, run the publisher, commit the HTML it writes, and push to `master`.
- The daily feed reads `daily/items.json` in the browser. With one digest day the volume chart has a single bar; the publisher does not invent older dates. 当天日报第 N 条 is the item's place in that day's full list, not its place after filtering.
