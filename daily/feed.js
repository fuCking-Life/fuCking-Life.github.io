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
    var VIEWS = { daily: true, all: true, tags: true, favorites: true };
    /* Ids stay in localStorage. Sync sends a hash of the code, never the code itself. */
    var FAV_KEY = "daily-favorites";
    var SYNC_CODE_KEY = "daily-favorites-sync-code";
    var SYNC_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789";
    var SYNC_PREFIX = "fl-daily-favorites-v1:";
    var SYNC_URL = "https://yjgurwjfuezrwhqapmcg.supabase.co";
    /* Publishable anon key. The table is not exposed; RPCs are the only API. */
    var SYNC_KEY = "sb_publishable_xM2Jr8yYvoLKLm4Q2SbJ1g_EWdrpcW4";
    var SHARE_BASE = "https://fucking-life.github.io/daily/?view=favorites#sync=";

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
    var favorites = new Set();
    var booted = false;
    var sessionCode = "";
    var syncEpoch = 0;
    var syncPushGen = 0;
    var syncSignature = "";
    var syncUi = {
        mode: "idle",
        pendingCode: "",
        notice: "",
        retry: "",
        busy: false,
        copied: "",
        invalidHash: false
    };

    function readFavorites() {
        favorites = new Set();
        try {
            var raw = localStorage.getItem(FAV_KEY);
            if (!raw) return;
            var list = JSON.parse(raw);
            if (!Array.isArray(list)) return;
            list.forEach(function (id) {
                if (typeof id === "string" && id) favorites.add(id);
            });
        } catch (err) {
            favorites = new Set();
        }
    }

    function writeFavorites() {
        try {
            localStorage.setItem(FAV_KEY, JSON.stringify(Array.from(favorites)));
        } catch (err) {
            /* Keep the in-memory set when storage is unavailable. */
        }
    }

    function toggleFavorite(id) {
        if (favorites.has(id)) favorites.delete(id);
        else favorites.add(id);
        writeFavorites();
        syncEpoch += 1;
        if (readSyncCode()) pushFavorites();
    }

    function cleanIds(list) {
        var out = [];
        var seen = {};
        if (!Array.isArray(list)) return out;
        list.forEach(function (id) {
            if (typeof id !== "string" || !id || seen[id]) return;
            seen[id] = true;
            out.push(id);
        });
        return out;
    }

    function sameIdList(ids) {
        var current = Array.from(favorites);
        if (current.length !== ids.length) return false;
        for (var i = 0; i < ids.length; i += 1) {
            if (current[i] !== ids[i]) return false;
        }
        return true;
    }

    function replaceFavorites(ids) {
        favorites = new Set(cleanIds(ids));
        writeFavorites();
    }

    function normalizeCode(value) {
        return String(value || "").toLowerCase().replace(/[^abcdefghjkmnpqrstuvwxyz23456789]/g, "");
    }

    function isValidCode(value) {
        return normalizeCode(value).length === 16;
    }

    function formatCode(value) {
        var normalized = normalizeCode(value);
        return normalized.slice(0, 4) + "-" + normalized.slice(4, 8) + "-" + normalized.slice(8, 12) + "-" + normalized.slice(12, 16);
    }

    function extractCode(raw) {
        var text = String(raw || "").trim();
        var lower = text.toLowerCase();
        var marker = "#sync=";
        var at = lower.indexOf(marker);
        if (at !== -1) {
            text = text.slice(at + marker.length);
            var end = text.search(/[&\s]/);
            if (end !== -1) text = text.slice(0, end);
            try { text = decodeURIComponent(text); } catch (err) { /* keep the raw slice */ }
        }
        return text;
    }

    function readSyncCode() {
        var stored = "";
        try { stored = localStorage.getItem(SYNC_CODE_KEY) || ""; } catch (err) { stored = ""; }
        var raw = stored || sessionCode;
        if (!isValidCode(raw)) return "";
        return formatCode(raw);
    }

    function storeSyncCode(code) {
        sessionCode = formatCode(code);
        try {
            localStorage.setItem(SYNC_CODE_KEY, sessionCode);
            return true;
        } catch (err) {
            return false;
        }
    }

    function shareLink(code) {
        return SHARE_BASE + encodeURIComponent(formatCode(code));
    }

    function toHex(buffer) {
        var bytes = new Uint8Array(buffer);
        var hex = "";
        for (var i = 0; i < bytes.length; i += 1) {
            hex += (bytes[i] < 16 ? "0" : "") + bytes[i].toString(16);
        }
        return hex;
    }

    function hashCode(code) {
        var payload = new TextEncoder().encode(SYNC_PREFIX + normalizeCode(code));
        return crypto.subtle.digest("SHA-256", payload).then(toHex);
    }

    function randomCode() {
        var chars = [];
        var buffer = new Uint8Array(16);
        while (chars.length < 16) {
            crypto.getRandomValues(buffer);
            for (var i = 0; i < buffer.length && chars.length < 16; i += 1) {
                if (buffer[i] >= 248) continue;
                chars.push(SYNC_ALPHABET.charAt(buffer[i] % 31));
            }
        }
        return formatCode(chars.join(""));
    }

    function rpc(name, body) {
        return fetch(SYNC_URL + "/rest/v1/rpc/" + name, {
            method: "POST",
            cache: "no-store",
            credentials: "omit",
            headers: {
                apikey: SYNC_KEY,
                Authorization: "Bearer " + SYNC_KEY,
                "Content-Type": "application/json"
            },
            body: JSON.stringify(body)
        }).then(function (response) {
            if (!response.ok) throw new Error("sync");
            return response.json();
        });
    }

    function failSync(message, retry) {
        syncUi.busy = false;
        syncUi.notice = message;
        syncUi.retry = retry || "";
        render();
    }

    function pushFavorites() {
        var code = readSyncCode();
        if (!code) return;
        var generation = syncPushGen + 1;
        syncPushGen = generation;
        var ids = Array.from(favorites);
        hashCode(code).then(function (hash) {
            if (generation !== syncPushGen || readSyncCode() !== code) return null;
            return rpc("set_daily_favorites", { p_code_hash: hash, p_item_ids: ids });
        }).then(function (saved) {
            if (saved == null || generation !== syncPushGen || readSyncCode() !== code) return;
            var next = cleanIds(saved);
            syncUi.notice = "";
            syncUi.retry = "";
            if (!sameIdList(next)) {
                replaceFavorites(next);
                render();
                return;
            }
            renderSyncPanel();
        }).catch(function () {
            if (generation !== syncPushGen || readSyncCode() !== code) return;
            failSync("同步未成功，收藏已留在本机。可再试一次。", "push");
        });
    }

    function pullRemote() {
        var code = readSyncCode();
        if (!code) return;
        var epoch = syncEpoch;
        hashCode(code).then(function (hash) {
            return rpc("get_daily_favorites", { p_code_hash: hash });
        }).then(function (remote) {
            if (readSyncCode() !== code) return;
            var ids = cleanIds(remote);
            if (syncEpoch !== epoch) {
                ids.forEach(function (id) { favorites.add(id); });
                writeFavorites();
                render();
                pushFavorites();
                return;
            }
            var changed = !sameIdList(ids);
            if (changed) replaceFavorites(ids);
            syncUi.notice = "";
            syncUi.retry = "";
            if (changed) render();
            else renderSyncPanel();
        }).catch(function () {
            if (readSyncCode() !== code) return;
            failSync("同步未成功，收藏已留在本机。可再试一次。", "pull");
        });
    }

    function connectCode(raw) {
        if (syncUi.busy) return;
        var extracted = extractCode(raw);
        if (!isValidCode(extracted)) {
            syncUi.notice = "同步码格式不对。";
            syncUi.retry = "";
            return;
        }
        if (!window.crypto || !crypto.subtle) {
            syncUi.notice = "这台浏览器不能开通同步。";
            return;
        }
        var code = formatCode(extracted);
        var epoch = syncEpoch + 1;
        syncEpoch = epoch;
        syncUi.busy = true;
        syncUi.pendingCode = code;
        syncUi.notice = "正在同步…";
        syncUi.retry = "";
        var localIds = Array.from(favorites);
        hashCode(code).then(function (hash) {
            return rpc("get_daily_favorites", { p_code_hash: hash }).then(function (remote) {
                var merged = cleanIds(remote).concat(localIds);
                merged = cleanIds(merged);
                return rpc("set_daily_favorites", { p_code_hash: hash, p_item_ids: merged });
            });
        }).then(function (saved) {
            var ids = cleanIds(saved);
            Array.from(favorites).forEach(function (id) {
                if (ids.indexOf(id) === -1) ids.push(id);
            });
            ids = cleanIds(ids);
            var kept = storeSyncCode(code);
            replaceFavorites(ids);
            syncPushGen += 1;
            syncUi.busy = false;
            syncUi.mode = "reveal";
            syncUi.pendingCode = "";
            syncUi.invalidHash = false;
            syncUi.retry = "";
            syncUi.notice = kept ? "" : "同步码没能保存在本机，请先复制。刷新后需要重新输入。";
            clearSyncHash();
            render();
            if (!sameIdList(cleanIds(saved))) pushFavorites();
        }).catch(function () {
            syncUi.pendingCode = code;
            failSync("同步未成功，收藏已留在本机。可再试一次。", "join");
        });
    }

    function enableSync() {
        if (syncUi.busy || readSyncCode()) return;
        if (!window.crypto || !crypto.subtle || !crypto.getRandomValues) {
            syncUi.notice = "这台浏览器不能开通同步。";
            return;
        }
        var code = randomCode();
        var ids = Array.from(favorites);
        syncUi.busy = true;
        syncUi.notice = "正在同步…";
        syncUi.retry = "";
        hashCode(code).then(function (hash) {
            return rpc("set_daily_favorites", { p_code_hash: hash, p_item_ids: ids });
        }).then(function (saved) {
            var keptIds = cleanIds(Array.isArray(saved) ? saved : ids);
            Array.from(favorites).forEach(function (itemId) {
                if (keptIds.indexOf(itemId) === -1) keptIds.push(itemId);
            });
            var kept = storeSyncCode(code);
            replaceFavorites(keptIds);
            syncUi.busy = false;
            syncUi.mode = "reveal";
            syncUi.retry = "";
            syncUi.notice = kept ? "" : "同步码没能保存在本机，请先复制。刷新后需要重新输入。";
            render();
            if (!sameIdList(cleanIds(Array.isArray(saved) ? saved : []))) pushFavorites();
        }).catch(function () {
            failSync("同步未成功，收藏已留在本机。可再试一次。", "enable");
        });
    }

    function disconnectSync() {
        sessionCode = "";
        try { localStorage.removeItem(SYNC_CODE_KEY); } catch (err) { /* already gone */ }
        syncPushGen += 1;
        syncEpoch += 1;
        syncUi.mode = "idle";
        syncUi.pendingCode = "";
        syncUi.busy = false;
        syncUi.retry = "";
        syncUi.copied = "";
        syncUi.invalidHash = false;
        syncUi.notice = "已在本机断开同步，收藏仍留在这台设备上。";
    }

    function copyText(text) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            return navigator.clipboard.writeText(text);
        }
        return new Promise(function (resolve, reject) {
            var input = document.createElement("textarea");
            input.value = text;
            document.body.appendChild(input);
            input.select();
            try {
                document.execCommand("copy");
                resolve();
            } catch (err) {
                reject(err);
            }
            document.body.removeChild(input);
        });
    }

    function copySync(text, kind) {
        copyText(text).then(function () {
            syncUi.copied = kind;
            renderSyncPanel();
            window.setTimeout(function () {
                if (syncUi.copied !== kind) return;
                syncUi.copied = "";
                renderSyncPanel();
            }, 1600);
        }).catch(function () {
            failSync("没有复制成功，请手动选择同步码。", "");
        });
    }

    function retrySync() {
        var kind = syncUi.retry;
        syncUi.notice = "";
        syncUi.retry = "";
        if (kind === "pull") pullRemote();
        else if (kind === "join") connectCode(syncUi.pendingCode);
        else if (kind === "enable") enableSync();
        else pushFavorites();
    }

    function handleSync(action) {
        if (syncUi.busy && action !== "cancel" && action !== "dismiss" && action !== "disconnect") return;
        if (action === "enable") enableSync();
        else if (action === "input") {
            syncUi.mode = "input";
            syncUi.notice = "";
        } else if (action === "cancel" || action === "dismiss") {
            if (syncUi.mode === "confirm") clearSyncHash();
            syncUi.mode = "idle";
            syncUi.pendingCode = "";
            syncUi.invalidHash = false;
            syncUi.busy = false;
        } else if (action === "confirm") connectCode(syncUi.pendingCode);
        else if (action === "reveal") syncUi.mode = "reveal";
        else if (action === "hide") syncUi.mode = "idle";
        else if (action === "disconnect") disconnectSync();
        else if (action === "copy-code") copySync(readSyncCode(), "code");
        else if (action === "copy-link") copySync(shareLink(readSyncCode()), "link");
        else if (action === "retry") retrySync();
    }

    function syncButton(action, label, strong) {
        var button = el("button", strong ? "sync-go" : "", label);
        button.type = "button";
        button.dataset.sync = action;
        if (syncUi.busy && action !== "cancel" && action !== "disconnect" && action !== "dismiss") button.disabled = true;
        return button;
    }

    function readHashCode() {
        var hash = location.hash || "";
        if (hash.indexOf("#sync=") !== 0) return "";
        var raw = hash.slice("#sync=".length);
        try { raw = decodeURIComponent(raw); } catch (err) { /* keep the raw slice */ }
        return raw.trim();
    }

    function clearSyncHash() {
        if ((location.hash || "").indexOf("#sync=") !== 0) return;
        history.replaceState(null, "", location.pathname + location.search);
    }

    function prepareSyncFromHash() {
        var raw = readHashCode();
        if (!raw) return;
        state.view = "favorites";
        if (!isValidCode(raw)) {
            syncUi.mode = "confirm";
            syncUi.invalidHash = true;
            return;
        }
        var code = formatCode(raw);
        if (readSyncCode() === code) {
            syncUi.notice = "这台设备已经在使用这个同步码。";
            clearSyncHash();
            return;
        }
        syncUi.mode = "confirm";
        syncUi.pendingCode = code;
    }

    function ensureSyncPanel() {
        if (document.getElementById("sync-panel")) return;
        var panel = document.createElement("div");
        panel.id = "sync-panel";
        panel.className = "sync-panel";
        panel.hidden = true;
        panel.setAttribute("aria-label", "跨设备同步");
        var stage = document.querySelector(".stage");
        var chart = document.getElementById("chart-card");
        if (!stage) return;
        if (chart) stage.insertBefore(panel, chart);
        else stage.insertBefore(panel, stage.firstChild);
    }

    function favoriteCount() {
        var count = 0;
        ITEMS.forEach(function (item) {
            if (item && favorites.has(item.id)) count += 1;
        });
        return count;
    }

    /* Digest publishes rewrite index.html from a template without this view. */
    function ensureFavoritesNav() {
        var nav = document.querySelector("nav.views");
        if (!nav || nav.querySelector('[data-view="favorites"]')) return;
        var button = document.createElement("button");
        button.type = "button";
        button.dataset.view = "favorites";
        button.textContent = "收藏";
        nav.appendChild(button);
    }

    function readUrl() {
        var params = new URLSearchParams(location.search);
        var view = params.get("view");
        if (VIEWS[view]) state.view = view;
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
        history.replaceState(null, "", next + (location.hash || ""));
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

    function inView(item) {
        if (state.view !== "favorites") return true;
        return Boolean(item && favorites.has(item.id));
    }

    function visible(item, ignoreDate) {
        return inView(item) && passesBase(item, ignoreDate) && passesFacets(item);
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

    function starIcon() {
        var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        svg.setAttribute("viewBox", "0 0 24 24");
        svg.setAttribute("aria-hidden", "true");
        var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
        path.setAttribute("d", "M12 2.8l2.55 5.45 5.95.62-4.45 3.95 1.3 5.78L12 15.9 6.65 18.6l1.3-5.78L3.5 8.87l5.95-.62z");
        svg.appendChild(path);
        return svg;
    }

    function favoriteButton(item) {
        var on = favorites.has(item.id);
        var button = el("button", "fav" + (on ? " is-on" : ""));
        button.type = "button";
        button.dataset.fav = item.id;
        button.setAttribute("aria-pressed", on ? "true" : "false");
        button.setAttribute("aria-label", on ? "取消收藏" : "收藏");
        button.appendChild(starIcon());
        button.appendChild(el("span", null, on ? "已收藏" : "收藏"));
        return button;
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
        var top = el("div", "card-top");
        top.appendChild(date);
        top.appendChild(favoriteButton(item));
        article.appendChild(top);

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
        var base = ITEMS.filter(function (item) {
            return inView(item) && passesBase(item, false);
        });
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
        var title = state.view === "favorites" ? "收藏" : "全部消息";
        document.getElementById("result-title").textContent = title + " " + shown.length + " 条";
        host.textContent = "";
        if (!shown.length) {
            var message = "没有符合条件的消息。";
            if (state.view === "favorites" && favoriteCount() === 0) {
                message = readSyncCode()
                    ? "还没有收藏。在卡片上点一下收藏即可。"
                    : "还没有收藏。在卡片上点一下收藏即可。开通同步后，其他设备也能看到。";
            }
            host.appendChild(el("p", "empty", message));
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

    function renderSyncPanel() {
        var panel = document.getElementById("sync-panel");
        if (!panel) return;
        var show = state.view === "favorites";
        if (!show) {
            panel.hidden = true;
            syncSignature = "hidden";
            return;
        }
        panel.hidden = false;
        var code = readSyncCode();
        var signature = [
            syncUi.mode,
            code,
            syncUi.pendingCode,
            syncUi.notice,
            syncUi.busy ? "1" : "0",
            syncUi.copied,
            syncUi.invalidHash ? "1" : "0",
            syncUi.retry
        ].join("|");
        if (signature === syncSignature) return;
        var field = document.getElementById("sync-code-input");
        var draft = field ? field.value : "";
        var focused = field && document.activeElement === field;
        syncSignature = signature;
        panel.textContent = "";
        if (syncUi.mode === "confirm") buildSyncConfirm(panel, code);
        else if (syncUi.mode === "input") buildSyncInput(panel);
        else if (code && syncUi.mode === "reveal") buildSyncReveal(panel, code);
        else if (code) buildSyncConnected(panel);
        else buildSyncOff(panel);
        var notice = el("p", "sync-notice", syncUi.notice || "");
        notice.setAttribute("role", "status");
        notice.setAttribute("aria-live", "polite");
        if (!syncUi.notice) notice.hidden = true;
        panel.appendChild(notice);
        if (syncUi.retry && !syncUi.busy) {
            var actions = panel.querySelector(".sync-actions");
            if (!actions) {
                actions = el("div", "sync-actions");
                panel.appendChild(actions);
            }
            actions.appendChild(syncButton("retry", "重试"));
        }
        var next = document.getElementById("sync-code-input");
        if (next && draft) next.value = draft;
        if (focused && next) next.focus();
    }

    function buildSyncOff(panel) {
        panel.appendChild(el("p", "sync-note", "收藏只保存在这台设备上。开通同步后，其他手机或浏览器可以用同步码看到同样的收藏。"));
        var actions = el("div", "sync-actions");
        actions.appendChild(syncButton("enable", "开通跨设备同步", true));
        actions.appendChild(syncButton("input", "输入同步码"));
        panel.appendChild(actions);
    }

    function buildSyncInput(panel) {
        var form = document.createElement("form");
        form.id = "sync-form";
        form.className = "sync-form";
        var label = el("label", "sync-label", "同步码");
        label.setAttribute("for", "sync-code-input");
        var input = document.createElement("input");
        input.id = "sync-code-input";
        input.type = "text";
        input.autocomplete = "off";
        input.spellcheck = false;
        input.setAttribute("autocapitalize", "off");
        input.setAttribute("autocorrect", "off");
        input.placeholder = "例如 xk7m-pq2n-w9rs-4tab";
        input.setAttribute("aria-describedby", "sync-input-hint");
        var hint = el("p", "sync-note", "连接后，本机已有的收藏会和云端合并一次。");
        hint.id = "sync-input-hint";
        var submit = el("button", "sync-go", "连接");
        submit.type = "submit";
        if (syncUi.busy) submit.disabled = true;
        form.appendChild(label);
        form.appendChild(input);
        form.appendChild(hint);
        form.appendChild(submit);
        form.appendChild(syncButton("cancel", "取消"));
        panel.appendChild(form);
    }

    function buildSyncConfirm(panel, code) {
        var actions = el("div", "sync-actions");
        if (syncUi.invalidHash) {
            panel.appendChild(el("p", "sync-note", "链接里的同步码格式不对。"));
            actions.appendChild(syncButton("dismiss", "知道了"));
            panel.appendChild(actions);
            return;
        }
        panel.appendChild(el("p", "sync-note", "要用同步码 " + syncUi.pendingCode + " 连接这台设备吗？本机已有的收藏会和云端合并一次。"));
        if (code && code !== syncUi.pendingCode) {
            panel.appendChild(el("p", "sync-note", "连接后，本机会改用这个同步码。"));
        }
        actions.appendChild(syncButton("confirm", "连接", true));
        actions.appendChild(syncButton("cancel", "取消"));
        panel.appendChild(actions);
    }

    function buildSyncConnected(panel) {
        panel.appendChild(el("p", "sync-status", "跨设备同步已开启"));
        panel.appendChild(el("p", "sync-note", "其他设备输入同步码，或打开分享链接，就能看到同样的收藏。"));
        var actions = el("div", "sync-actions");
        actions.appendChild(syncButton("reveal", "查看同步码"));
        actions.appendChild(syncButton("disconnect", "在本机断开同步"));
        panel.appendChild(actions);
    }

    function buildSyncReveal(panel, code) {
        panel.appendChild(el("p", "sync-status", "跨设备同步已开启"));
        panel.appendChild(el("p", "sync-label", "同步码"));
        panel.appendChild(el("p", "sync-code", code));
        panel.appendChild(el("p", "sync-label", "分享链接"));
        panel.appendChild(el("p", "sync-link", shareLink(code)));
        var actions = el("div", "sync-actions");
        actions.appendChild(syncButton("copy-code", syncUi.copied === "code" ? "已复制" : "复制同步码"));
        actions.appendChild(syncButton("copy-link", syncUi.copied === "link" ? "已复制" : "复制链接"));
        actions.appendChild(syncButton("hide", "收起"));
        actions.appendChild(syncButton("disconnect", "在本机断开同步"));
        panel.appendChild(actions);
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
        var base = ITEMS.filter(function (item) {
            return inView(item) && passesBase(item, false);
        });
        renderFilters();
        renderChart();
        renderChips();
        renderTagBoard(base);
        renderList();
        renderSyncPanel();
        writeUrl();
    }

    function toggleSet(set, value) {
        if (set.has(value)) set.delete(value);
        else set.add(value);
    }

    function cssEscape(value) {
        if (window.CSS && CSS.escape) return CSS.escape(value);
        return String(value).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
    }

    function onClick(event) {
        var node = event.target.closest("[data-sync], [data-fav], [data-view], [data-match], [data-rel], [data-facet], [data-tag], [data-date], [data-sort], [data-clear], [data-open-filters], [data-close-filters]");
        if (!node) return;
        var focusFav = "";
        if (node.dataset.sync) handleSync(node.dataset.sync);
        else if (node.dataset.fav) {
            focusFav = node.dataset.fav;
            toggleFavorite(focusFav);
        } else if (node.dataset.view) state.view = node.dataset.view;
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
        if (!focusFav) return;
        var again = document.querySelector('[data-fav="' + cssEscape(focusFav) + '"]');
        if (again) again.focus();
        else {
            var nav = document.querySelector('[data-view="favorites"]');
            if (nav) nav.focus();
        }
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
        if (event.key !== "Escape") return;
        if (syncUi.mode === "input" || syncUi.mode === "confirm" || syncUi.mode === "reveal") {
            if (syncUi.mode === "confirm") clearSyncHash();
            syncUi.mode = "idle";
            syncUi.pendingCode = "";
            syncUi.invalidHash = false;
            syncUi.busy = false;
            render();
            return;
        }
        document.getElementById("filters").classList.remove("is-open");
        render();
    });
    document.body.addEventListener("submit", function (event) {
        if (!event.target || event.target.id !== "sync-form") return;
        event.preventDefault();
        var input = document.getElementById("sync-code-input");
        connectCode(input ? input.value : "");
        render();
    });

    ensureFavoritesNav();
    ensureSyncPanel();
    readFavorites();
    readUrl();
    prepareSyncFromHash();
    fetch("items.json", { cache: "no-cache" })
        .then(function (response) {
            if (!response.ok) throw new Error("status " + response.status);
            return response.json();
        })
        .then(function (items) {
            if (!Array.isArray(items)) throw new Error("items");
            ITEMS = items;
            render();
            if (readSyncCode()) pullRemote();
        })
        .catch(function () {
            showError("日报数据没有加载出来。");
        });
})();
