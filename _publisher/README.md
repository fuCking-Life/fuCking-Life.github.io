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

`--category` and `--tag` can be repeated. A category is required. Tags are optional. The visible name is kept (`Agent App` stays `Agent App`) and the directory is a lowercase slug (`agent-app`, `日报`).

`--date` is interpreted in Asia/Shanghai. A date with no clock time uses 08:00:00. A value that already has a zone (`2026-11-17T00:30:00Z`) is converted to Shanghai time.

The body is Markdown unless you pass `--format html` or set `"format": "html"` in JSON. The theme already prints the title, so the body should start with the text rather than a heading that repeats the title. Put `<!--more-->` on its own line to choose the listing summary; otherwise the summary is the first paragraph. `--summary` and `--description` override those.

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

Hashed files under `css/` and `js/` are not rewritten. Existing post HTML is not rewritten. `/life/`, `/about/`, `/page/`, and `404.html` are left alone.

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
