/* Daily feed. Cards follow this order: date line, 当天日报第 N 条,
   relevance, title, summary, optional insight, tags, source, links,
   optional 发帖日期. */
(function () {
    "use strict";

    var COLORS = ["#6d28d9", "#7c3aed", "#2563eb", "#f59e0b", "#ea580c"];
    var DOMAINS = [
        "语音 Agent", "全双工", "打断与轮次", "语音识别 ASR", "语音合成 TTS",
        "端到端语音", "端侧", "iOS", "苹果生态", "个人 Agent", "Agent 架构",
        "工具与 MCP", "大模型", "多模态", "评测与榜单", "开源", "论文", "智能座舱"
    ];
    var TYPES = [
        "产品发布", "模型发布", "开源", "技术解读", "观点讨论",
        "评测结果", "论文", "行业数据"
    ];
    var WEEKDAYS = ["星期日", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六"];

    var state = {
        view: "daily",
        match: "all",
        minRelevance: 0,
        domains: new Set(),
        types: new Set(),
        tags: new Set(),
        date: "",
        query: "",
        sort: "time"
    };
    var ITEMS = [];
    var booted = false;

    function readUrl() {
        var params = new URLSearchParams(location.search);
        var view = params.get("view");
        if (view === "daily" || view === "all" || view === "tags") state.view = view;
        var match = params.get("match");
        if (match === "all" || match === "any") state.match = match;
        var rel = Number(params.get("rel"));
        if (rel >= 1 && rel <= 5) state.minRelevance = rel;
        var sort = params.get("sort");
        if (sort === "time" || sort === "relevance") state.sort = sort;
        state.date = params.get("date") || "";
        state.query = params.get("q") || "";
        params.getAll("domain").forEach(function (name) { state.domains.add(name); });
        params.getAll("type").forEach(function (name) { state.types.add(name); });
        params.getAll("tag").forEach(function (name) { state.tags.add(name); });
    }

    function writeUrl() {
        var params = new URLSearchParams();
        if (state.view !== "daily") params.set("view", state.view);
        if (state.match !== "all") params.set("match", state.match);
        if (state.minRelevance) params.set("rel", String(state.minRelevance));
        if (state.sort !== "time") params.set("sort", state.sort);
        if (state.date) params.set("date", state.date);
        if (state.query) params.set("q", state.query);
        state.domains.forEach(function (name) { params.append("domain", name); });
        state.types.forEach(function (name) { params.append("type", name); });
        state.tags.forEach(function (name) { params.append("tag", name); });
        var query = params.toString();
        var next = query ? location.pathname + "?" + query : location.pathname;
        history.replaceState(null, "", next);
    }

    function searchText(item) {
        var parts = [item.title || "", item.summary || ""];
        if (item.insight) parts.push(item.insight.heading || "", item.insight.body || "");
        var source = item.source || {};
        parts.push(source.name || "", source.handle || "", source.identity || "");
        return parts.join("\n").toLowerCase();
    }

    function passesBase(item, ignoreDate) {
        if (state.minRelevance && item.relevance < state.minRelevance) return false;
        if (!ignoreDate && state.date && item.date !== state.date) return false;
        var needle = state.query.trim().toLowerCase();
        if (needle && searchText(item).indexOf(needle) === -1) return false;
        return true;
    }

    function passesFacets(item) {
        var domains = Array.from(state.domains);
        var types = Array.from(state.types);
        var tags = Array.from(state.tags);
        if (!domains.length && !types.length && !tags.length) return true;
        var hits = domains.map(function (name) { return item.domains.indexOf(name) !== -1; })
            .concat(types.map(function (name) { return item.types.indexOf(name) !== -1; }))
            .concat(tags.map(function (name) { return (item.tags || []).indexOf(name) !== -1; }));
        if (state.match === "any") return hits.some(Boolean);
        return hits.every(Boolean);
    }

    function visible(item, ignoreDate) {
        return passesBase(item, ignoreDate) && passesFacets(item);
    }

    function weekdayLabel(iso) {
        var bits = iso.split("-").map(Number);
        var day = new Date(Date.UTC(bits[0], bits[1] - 1, bits[2])).getUTCDay();
        return WEEKDAYS[day];
    }

    function briefLabel(value) {
        return value === "lumi" ? "Lumi" : "agent-app";
    }

    function ordinals(items) {
        var byDay = {};
        items.forEach(function (item) {
            (byDay[item.date] || (byDay[item.date] = [])).push(item);
        });
        var out = {};
        Object.keys(byDay).forEach(function (date) {
            byDay[date].sort(function (a, b) {
                return a.seq - b.seq || (a.id < b.id ? -1 : 1);
            }).forEach(function (item, index) {
                out[item.id] = index + 1;
            });
        });
        return out;
    }

    function compareItems(a, b) {
        if (state.sort === "relevance" && a.relevance !== b.relevance) {
            return b.relevance - a.relevance;
        }
        if (a.date !== b.date) return a.date < b.date ? 1 : -1;
        if (a.seq !== b.seq) return a.seq - b.seq;
        return a.id < b.id ? -1 : 1;
    }

    function el(tag, className, text) {
        var node = document.createElement(tag);
        if (className) node.className = className;
        if (text != null) node.textContent = text;
        return node;
    }

    function safeUrl(url) {
        if (!url) return "";
        if (url.indexOf("https://") === 0 || url.indexOf("http://") === 0 || url.charAt(0) === "/") {
            if (url.indexOf("//") === 0) return "";
            return url;
        }
        return "";
    }

    function sourceLine(source) {
        source = source || {};
        var handle = source.handle || "";
        var name = source.name || "";
        var identity = source.identity || "";
        var shown = handle ? (handle.charAt(0) === "@" ? handle : "@" + handle) : name;
        var parts = ["来源 " + shown];
        if (identity) parts.push(identity);
        else if (name && handle) parts.push(name);
        return parts.join(" · ");
    }

    function renderCard(item, ordinal) {
        var article = el("article", "card");
        article.style.setProperty("--score", COLORS[(item.relevance || 1) - 1]);
        article.dataset.id = item.id;

        var date = el("p", "card-date");
        var time = el("time", null, item.date);
        time.setAttribute("datetime", item.date);
        date.appendChild(time);
        date.appendChild(el("span", "brief", briefLabel(item.source_brief)));
        date.appendChild(el("span", "ordinal", "当天日报第 " + ordinal + " 条"));
        article.appendChild(date);

        var rel = el("p", "relevance");
        rel.appendChild(el("span", "relevance-label", "相关度"));
        var bars = el("span", "relevance-bars");
        for (var i = 1; i <= 5; i += 1) {
            var mark = document.createElement("i");
            mark.style.background = i <= item.relevance ? COLORS[item.relevance - 1] : "#e6e1d8";
            bars.appendChild(mark);
        }
        rel.appendChild(bars);
        var score = el("span", "relevance-score", String(item.relevance));
        score.style.color = COLORS[item.relevance - 1];
        rel.appendChild(score);
        article.appendChild(rel);

        var links = item.links || [];
        var titleHref = safeUrl(links.length ? links[0].url : item.archive_url);
        var heading = el("h3", "card-title");
        if (titleHref) {
            var titleLink = el("a", null, item.title);
            titleLink.href = titleHref;
            if (titleHref.indexOf("http") === 0) {
                titleLink.target = "_blank";
                titleLink.rel = "noopener noreferrer";
            }
            heading.appendChild(titleLink);
        } else {
            heading.textContent = item.title;
        }
        article.appendChild(heading);
        article.appendChild(el("p", "card-summary", item.summary));

        if (item.insight && item.insight.heading && item.insight.body) {
            var quote = el("blockquote", "insight");
            quote.appendChild(el("p", "insight-heading", item.insight.heading));
            quote.appendChild(el("p", null, item.insight.body));
            article.appendChild(quote);
        }

        if (item.tags && item.tags.length) {
            var tags = el("ul", "tags");
            item.tags.forEach(function (tag) {
                var li = el("li");
                var button = el("button", state.tags.has(tag) ? "is-on" : "", tag);
                button.type = "button";
                button.dataset.tag = tag;
                li.appendChild(button);
                tags.appendChild(li);
            });
            article.appendChild(tags);
        }

        article.appendChild(el("p", "source", sourceLine(item.source)));

        var row = el("ul", "card-links");
        links.forEach(function (link) {
            var href = safeUrl(link.url);
            if (!href) return;
            var li = el("li");
            var anchor = el("a", null, link.label);
            anchor.href = href;
            if (href.indexOf("http") === 0) {
                anchor.target = "_blank";
                anchor.rel = "noopener noreferrer";
            }
            li.appendChild(anchor);
            row.appendChild(li);
        });
        if (item.archive_url && !links.some(function (link) { return link.url === item.archive_url; })) {
            var archive = el("li");
            var archiveLink = el("a", null, "归档");
            archiveLink.href = item.archive_url;
            archive.appendChild(archiveLink);
            row.appendChild(archive);
        }
        if (row.childNodes.length) article.appendChild(row);

        if (item.posted_at && item.posted_at !== item.date) {
            article.appendChild(el("p", "posted", "发帖日期 " + item.posted_at));
        }
        return article;
    }

    function fillFacet(container, names, selected, kind, baseItems) {
        container.textContent = "";
        names.forEach(function (name) {
            var count = baseItems.filter(function (item) {
                var list = kind === "domain" ? item.domains : item.types;
                return list.indexOf(name) !== -1;
            }).length;
            var button = el("button", "facet-row" + (selected.has(name) ? " is-on" : ""));
            button.type = "button";
            button.dataset.facet = kind;
            button.dataset.value = name;
            button.appendChild(el("span", "box"));
            button.appendChild(el("span", "name", name));
            button.appendChild(el("span", "count", String(count)));
            container.appendChild(button);
        });
    }

    function renderFilters() {
        document.querySelectorAll("[data-match]").forEach(function (button) {
            button.classList.toggle("is-active", button.dataset.match === state.match);
        });
        document.querySelectorAll("[data-rel]").forEach(function (button) {
            var score = Number(button.dataset.rel);
            var on = state.minRelevance && score >= state.minRelevance;
            button.classList.toggle("is-on", on);
            button.classList.toggle("is-chosen", score === state.minRelevance);
            button.setAttribute("aria-pressed", score === state.minRelevance ? "true" : "false");
        });
        var base = ITEMS.filter(function (item) { return passesBase(item, false); });
        var domains = DOMAINS.filter(function (name) {
            return ITEMS.some(function (item) { return item.domains.indexOf(name) !== -1; });
        });
        var types = TYPES.filter(function (name) {
            return ITEMS.some(function (item) { return item.types.indexOf(name) !== -1; });
        });
        fillFacet(document.getElementById("domain-list"), domains, state.domains, "domain", base);
        fillFacet(document.getElementById("type-list"), types, state.types, "type", base);
    }

    function renderChart() {
        var host = document.getElementById("chart");
        var card = document.getElementById("chart-card");
        host.textContent = "";
        var rows = ITEMS.filter(function (item) { return visible(item, true); });
        var dates = [];
        rows.forEach(function (item) {
            if (dates.indexOf(item.date) === -1) dates.push(item.date);
        });
        dates.sort();
        if (!dates.length) {
            card.hidden = true;
            return;
        }
        card.hidden = false;
        var counts = dates.map(function (date) {
            return rows.filter(function (item) { return item.date === date; }).length;
        });
        var max = Math.max.apply(null, counts.concat([1]));
        var wrap = el("div", "chart");
        dates.forEach(function (date, index) {
            var button = el("button", "bar" + (state.date === date ? " is-on" : ""));
            button.type = "button";
            button.dataset.date = date;
            button.appendChild(el("span", "n", String(counts[index])));
            var bar = el("b");
            bar.style.height = Math.max(8, Math.round((counts[index] / max) * 72)) + "px";
            button.appendChild(bar);
            button.appendChild(el("span", null, date.slice(5)));
            wrap.appendChild(button);
        });
        host.appendChild(wrap);
    }

    function chip(label, set, clear, value) {
        var button = el("button", "chip" + (set ? " is-set" : ""), label);
        button.type = "button";
        if (set) {
            button.dataset.clear = clear;
            if (value) button.dataset.value = value;
        } else {
            button.disabled = true;
        }
        return button;
    }

    function renderChips() {
        var host = document.getElementById("chips");
        host.textContent = "";
        host.appendChild(chip(state.date || "全部日期", Boolean(state.date), "date"));
        if (state.tags.size) {
            state.tags.forEach(function (tag) {
                host.appendChild(chip(tag, true, "tag", tag));
            });
        } else {
            host.appendChild(chip("全部标签", false, "tag"));
        }
        state.domains.forEach(function (name) {
            host.appendChild(chip(name, true, "domain", name));
        });
        state.types.forEach(function (name) {
            host.appendChild(chip(name, true, "type", name));
        });
        if (state.minRelevance) {
            host.appendChild(chip("相关度 ≥ " + state.minRelevance, true, "rel"));
        }
        if (state.query) host.appendChild(chip("搜索：" + state.query, true, "query"));
    }

    function renderTagBoard(base) {
        var board = document.getElementById("tag-board");
        board.textContent = "";
        if (state.view !== "tags") {
            board.hidden = true;
            return;
        }
        board.hidden = false;
        var counts = {};
        base.forEach(function (item) {
            (item.tags || []).forEach(function (tag) {
                counts[tag] = (counts[tag] || 0) + 1;
            });
        });
        Object.keys(counts).sort(function (a, b) {
            return counts[b] - counts[a] || a.localeCompare(b, "zh");
        }).forEach(function (tag) {
            var button = el("button", state.tags.has(tag) ? "is-on" : "", tag + " " + counts[tag]);
            button.type = "button";
            button.dataset.tag = tag;
            board.appendChild(button);
        });
    }

    function renderList() {
        var host = document.getElementById("feed-list");
        var shown = ITEMS.filter(function (item) { return visible(item, false); }).sort(compareItems);
        document.getElementById("result-title").textContent = "全部消息 " + shown.length + " 条";
        host.textContent = "";
        if (!shown.length) {
            host.appendChild(el("p", "empty", "没有符合条件的消息。"));
            return;
        }
        var numbers = ordinals(ITEMS);
        if (state.view !== "daily") {
            shown.forEach(function (item) {
                host.appendChild(renderCard(item, numbers[item.id]));
            });
            return;
        }
        var dates = [];
        shown.forEach(function (item) {
            if (dates.indexOf(item.date) === -1) dates.push(item.date);
        });
        dates.sort(function (a, b) { return a < b ? 1 : -1; });
        dates.forEach(function (date) {
            var rows = shown.filter(function (item) { return item.date === date; });
            var heading = el("h3", "day-heading");
            heading.appendChild(el("strong", null, date + " " + weekdayLabel(date)));
            heading.appendChild(el("span", null, rows.length + " 条"));
            host.appendChild(heading);
            rows.forEach(function (item) {
                host.appendChild(renderCard(item, numbers[item.id]));
            });
        });
    }

    function render() {
        document.querySelectorAll("[data-view]").forEach(function (button) {
            var on = button.dataset.view === state.view;
            button.classList.toggle("is-active", on);
            if (on) button.setAttribute("aria-current", "page");
            else button.removeAttribute("aria-current");
        });
        document.querySelectorAll("[data-sort]").forEach(function (button) {
            button.classList.toggle("is-active", button.dataset.sort === state.sort);
        });
        var toggle = document.querySelector("[data-open-filters]");
        var open = document.getElementById("filters").classList.contains("is-open");
        toggle.setAttribute("aria-expanded", open ? "true" : "false");
        if (!booted) {
            document.getElementById("q").value = state.query;
            booted = true;
        }
        var base = ITEMS.filter(function (item) { return passesBase(item, false); });
        renderFilters();
        renderChart();
        renderChips();
        renderTagBoard(base);
        renderList();
        writeUrl();
    }

    function toggleSet(set, value) {
        if (set.has(value)) set.delete(value);
        else set.add(value);
    }

    function onClick(event) {
        var node = event.target.closest("[data-view], [data-match], [data-rel], [data-facet], [data-tag], [data-date], [data-sort], [data-clear], [data-open-filters], [data-close-filters]");
        if (!node) return;
        if (node.dataset.view) state.view = node.dataset.view;
        else if (node.dataset.match) state.match = node.dataset.match;
        else if (node.dataset.rel) {
            var score = Number(node.dataset.rel);
            state.minRelevance = state.minRelevance === score ? 0 : score;
        } else if (node.dataset.facet) {
            var set = node.dataset.facet === "domain" ? state.domains : state.types;
            toggleSet(set, node.dataset.value);
        } else if (node.dataset.tag) toggleSet(state.tags, node.dataset.tag);
        else if (node.dataset.date) state.date = state.date === node.dataset.date ? "" : node.dataset.date;
        else if (node.dataset.sort) state.sort = node.dataset.sort;
        else if (node.dataset.clear) {
            var kind = node.dataset.clear;
            if (kind === "date") state.date = "";
            else if (kind === "rel") state.minRelevance = 0;
            else if (kind === "query") state.query = document.getElementById("q").value = "";
            else if (kind === "tag") state.tags.delete(node.dataset.value);
            else if (kind === "domain") state.domains.delete(node.dataset.value);
            else if (kind === "type") state.types.delete(node.dataset.value);
        } else if (node.dataset.openFilters != null) {
            document.getElementById("filters").classList.toggle("is-open");
        } else if (node.dataset.closeFilters != null) {
            document.getElementById("filters").classList.remove("is-open");
        }
        render();
    }

    function showError(message) {
        var host = document.getElementById("feed-list");
        host.textContent = "";
        host.appendChild(el("p", "empty", message));
    }

    document.body.addEventListener("click", onClick);
    document.getElementById("search-form").addEventListener("submit", function (event) {
        event.preventDefault();
    });
    document.getElementById("q").addEventListener("input", function (event) {
        state.query = event.target.value;
        render();
    });
    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
            document.getElementById("filters").classList.remove("is-open");
            render();
        }
    });

    readUrl();
    fetch("items.json", { cache: "no-cache" })
        .then(function (response) {
            if (!response.ok) throw new Error("status " + response.status);
            return response.json();
        })
        .then(function (items) {
            if (!Array.isArray(items)) throw new Error("items");
            ITEMS = items;
            render();
        })
        .catch(function () {
            showError("日报数据没有加载出来。");
        });
})();
