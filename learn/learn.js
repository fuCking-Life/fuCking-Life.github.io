/* /learn/ — learning calendar.
 * plan.json: the fixed day-by-day plan. log.json: what was actually learned, written by Claude at the end of a session.
 * Ticks made on this page live only in this browser (localStorage) and are labelled 本机 until log.json has them. */
(function () {
    "use strict";

    var TICK_KEY = "learn-ticks-v1";
    var WEEKDAYS = ["日", "一", "二", "三", "四", "五", "六"];

    var plan = null;
    var log = { days: {} };
    var local = {};          // task id -> ISO date it was ticked here
    var synced = {};         // task id -> true when log.json has it
    var dayOf = {};          // task id -> plan day
    var days = [];           // every plan day, in order
    var weeks = [];
    var today = shanghaiToday();
    var openDate = "";

    function $(id) { return document.getElementById(id); }

    function esc(s) {
        return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[c];
        });
    }

    function shanghaiToday() {
        try {
            var parts = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date());
            var get = function (t) { return parts.filter(function (p) { return p.type === t; })[0].value; };
            return get("year") + "-" + get("month") + "-" + get("day");
        } catch (err) {
            return new Date().toISOString().slice(0, 10);
        }
    }

    function parseDate(iso) { var p = iso.split("-"); return new Date(Date.UTC(+p[0], +p[1] - 1, +p[2])); }
    function isoOf(d) { return d.toISOString().slice(0, 10); }
    function addDays(iso, n) { var d = parseDate(iso); d.setUTCDate(d.getUTCDate() + n); return isoOf(d); }
    function diffDays(a, b) { return Math.round((parseDate(b) - parseDate(a)) / 86400000); }
    function weekday(iso) { return parseDate(iso).getUTCDay(); }
    function md(iso) { var p = iso.split("-"); return (+p[1]) + "/" + (+p[2]); }

    function readLocal() {
        try { local = JSON.parse(localStorage.getItem(TICK_KEY) || "{}") || {}; } catch (err) { local = {}; }
    }
    function writeLocal() {
        try { localStorage.setItem(TICK_KEY, JSON.stringify(local)); } catch (err) { /* private mode: ticks last for this visit */ }
    }

    function isDone(id) { return !!synced[id] || !!local[id]; }
    function dayDone(d) { return d.tasks.filter(function (t) { return isDone(t.id); }).length; }
    function dayState(d) {
        var n = dayDone(d), total = d.tasks.length;
        if (total && n === total) return "done";
        if (n > 0) return "part";
        if (d.date < today) return "miss";
        return "future";
    }

    function load(url) {
        return fetch(url, { cache: "no-store" }).then(function (r) {
            if (!r.ok) throw new Error(url);
            return r.json();
        });
    }

    function index() {
        weeks = plan.weeks;
        days = [];
        weeks.forEach(function (w) { w.days.forEach(function (d) { days.push(d); d.tasks.forEach(function (t) { dayOf[t.id] = d; }); }); });
        synced = {};
        Object.keys(log.days || {}).forEach(function (date) {
            (log.days[date].done || []).forEach(function (id) { synced[id] = true; });
        });
        // A tick that log.json now records is no longer "本机".
        var changed = false;
        Object.keys(local).forEach(function (id) {
            if (synced[id] || !dayOf[id]) { delete local[id]; changed = true; }
        });
        if (changed) writeLocal();
    }

    /* ------------------------------------------------------------ stats */

    function activeDates() {
        var set = {};
        Object.keys(log.days || {}).forEach(function (date) {
            if ((log.days[date].done || []).length) set[date] = true;
        });
        Object.keys(local).forEach(function (id) { set[local[id]] = true; });
        return set;
    }

    function streak() {
        var set = activeDates();
        var cur = set[today] ? today : addDays(today, -1);
        var n = 0;
        while (set[cur]) { n += 1; cur = addDays(cur, -1); }
        return n;
    }

    function renderHero() {
        $("lp-title").textContent = plan.title;
        $("lp-sub").textContent = plan.subtitle + "。" + plan.hours + "。";
        var total = 0, done = 0;
        days.forEach(function (d) { total += d.tasks.length; done += dayDone(d); });
        var pct = total ? Math.round(done * 100 / total) : 0;
        var last = days[days.length - 1].date;   // weeks can be appended later; the plan ends on its last day
        var left = Math.max(0, diffDays(today, last));
        var first = days[0].date;
        var span = diffDays(first, last) + 1;
        var started = today >= first;
        var dayN = started ? Math.min(diffDays(first, today) + 1, span) : 0;
        $("lp-stats").innerHTML =
            stat(pct + "%", "总进度", true) +
            stat(done + " / " + total, "已完成任务") +
            stat(streak() + " 天", "连续学习") +
            stat(started ? ("第 " + dayN + " / " + span + " 天") : ("还有 " + diffDays(today, first) + " 天开始"), left ? ("计划还剩 " + left + " 天") : "计划已走完");
        var bar = $("lp-bar");
        bar.setAttribute("aria-valuenow", String(pct));
        requestAnimationFrame(function () { bar.firstElementChild.style.width = pct + "%"; });
    }

    function stat(value, label, key) {
        return '<div class="lp-stat' + (key ? " is-key" : "") + '"><b>' + esc(value) + "</b><span>" + esc(label) + "</span></div>";
    }

    /* ------------------------------------------------------------ today */

    function todayDay() {
        var exact = days.filter(function (d) { return d.date === today; })[0];
        if (exact) return { day: exact, exact: true };
        var next = days.filter(function (d) { return d.date > today; })[0];
        return { day: next || days[days.length - 1], exact: false };
    }

    function renderToday() {
        var pick = todayDay();
        var d = pick.day;
        var behind = days.filter(function (x) { return x.date < today && dayState(x) !== "done"; });
        var behindTasks = 0;
        behind.forEach(function (x) { behindTasks += x.tasks.length - dayDone(x); });
        var kicker = pick.exact ? "今天 · " + md(d.date) + " 周" + WEEKDAYS[weekday(d.date)] : "下一个学习日 · " + md(d.date);
        var html = '<p class="lp-kicker">' + esc(kicker) + "</p>" +
            "<h2>" + esc(d.label) + "</h2>" +
            '<p class="lp-goal">' + esc(d.goal) + "</p>" +
            taskList(d);
        if (dayState(d) === "done") html += '<p class="lp-cheer">今天的任务全部完成 ✓ 记得对 Claude 说“今天就到这”，把总结同步过来。</p>';
        if (behindTasks) {
            html += '<p class="lp-catchup">之前还有 ' + behindTasks + " 项没做完，最早一天是 " + md(behind[0].date) +
                '。<button type="button" class="lp-linkbtn" data-open="' + esc(behind[0].date) + '">去补上 →</button></p>';
        }
        $("lp-today").innerHTML = html;
    }

    function taskList(d) {
        return '<ul class="lp-tasks">' + d.tasks.map(function (t) {
            var done = isDone(t.id), isSynced = !!synced[t.id];
            var tag = isSynced ? "" : (local[t.id] ? '<span class="tag" title="只记在这个浏览器里，学完会同步">本机</span>' : "");
            return '<li class="' + (done ? "is-done" : "") + (isSynced ? " is-synced" : "") + '"><label>' +
                '<input type="checkbox" data-task="' + esc(t.id) + '"' + (done ? " checked" : "") + (isSynced ? ' disabled title="已同步"' : "") + ">" +
                '<span class="t">' + esc(t.text) + tag + "</span>" +
                '<span class="m">' + esc(t.min) + " 分钟</span>" +
                "</label></li>";
        }).join("") + "</ul>";
    }

    /* --------------------------------------------------------- calendar */

    function renderCalendar() {
        var html = weeks.map(function (w) {
            var first = w.days[0].date;
            var lead = (weekday(first) + 6) % 7;   // Monday = 0
            var cells = [];
            for (var i = 0; i < lead; i += 1) cells.push('<div class="lp-day is-empty" aria-hidden="true"></div>');
            var total = 0, done = 0;
            w.days.forEach(function (d) {
                total += d.tasks.length;
                done += dayDone(d);
                cells.push(dayCell(d));
            });
            while (cells.length % 7) cells.push('<div class="lp-day is-empty" aria-hidden="true"></div>');
            var pct = total ? Math.round(done * 100 / total) : 0;
            return '<div class="lp-week">' +
                '<div class="lp-week-label"><b>' + esc(w.title) + " · " + pct + "%</b><span>" + esc(w.theme) + "</span>" +
                '<div class="lp-bar"><span style="width:' + pct + '%"></span></div></div>' +
                '<div class="lp-week-days">' + cells.join("") + "</div></div>";
        }).join("");
        $("lp-weeks").innerHTML = html;
    }

    function dayCell(d) {
        var state = dayState(d);
        var n = dayDone(d), total = d.tasks.length;
        var entry = (log.days || {})[d.date];
        var cls = "lp-day is-" + state + (d.date === today ? " is-today" : "");
        var summary = entry && entry.summary ? entry.summary : "";
        var label = md(d.date) + " " + d.label + "，完成 " + n + "/" + total + (summary ? "。" + summary : "");
        return '<button type="button" class="' + cls + '" data-open="' + esc(d.date) + '" aria-label="' + esc(label) + '">' +
            '<span class="d"><span>' + esc(md(d.date)) + "</span>" + (state === "done" ? '<span class="ok">✓</span>' : "") + "</span>" +
            '<span class="l">' + esc(d.label) + "</span>" +
            (summary ? '<span class="s">' + esc(summary) + "</span>" : "") +
            '<span class="mini"><i style="width:' + (total ? Math.round(n * 100 / total) : 0) + '%"></i></span>' +
            "</button>";
    }

    /* ----------------------------------------------------------- detail */

    function openDetail(date, fromHash) {
        var d = days.filter(function (x) { return x.date === date; })[0];
        if (!d) return;
        openDate = date;
        var entry = (log.days || {})[date] || {};
        var n = dayDone(d);
        var minutes = d.tasks.reduce(function (s, t) { return s + (t.min || 0); }, 0);
        var html = "<h2 id=\"lp-d-title\">" + esc(d.label) + "</h2>" +
            '<p class="lp-meta">' + esc(date) + " 周" + WEEKDAYS[weekday(date)] + " · 完成 " + n + "/" + d.tasks.length + " · 计划约 " + Math.round(minutes / 6) / 10 + " 小时</p>" +
            '<p class="lp-goal">' + esc(d.goal) + "</p>" +
            "<h3>任务</h3>" + taskList(d);
        if (entry.summary || (entry.points || []).length) {
            html += "<h3>当天总结</h3>";
            if (entry.summary) html += '<p class="lp-sum">' + esc(entry.summary) + "</p>";
            if ((entry.points || []).length) {
                html += '<ul class="lp-points">' + entry.points.map(function (p) { return "<li>" + esc(p) + "</li>"; }).join("") + "</ul>";
            }
        }
        if (entry.note) {
            html += '<a class="lp-report" href="' + esc(entry.note) + '">查看详细学习报告 →</a>';
        } else if (entry.summary) {
            html += '<p class="lp-pending">这一天的详细笔记在私有学习仓库里，还没有发到 blog。</p>';
        } else {
            html += '<p class="lp-pending">学完后，这里会出现当天的总结和学习报告链接。</p>';
        }
        $("lp-detail").innerHTML = html;
        $("lp-modal").hidden = false;
        document.body.style.overflow = "hidden";
        if (!fromHash && location.hash !== "#" + date) history.replaceState(null, "", "#" + date);
        var x = document.querySelector(".lp-x");
        if (x) x.focus();
    }

    function closeDetail() {
        if ($("lp-modal").hidden) return;
        $("lp-modal").hidden = true;
        document.body.style.overflow = "";
        var back = document.querySelector('.lp-day[data-open="' + openDate + '"]');
        openDate = "";
        if (location.hash) history.replaceState(null, "", location.pathname + location.search);
        if (back) back.focus();
    }

    /* ------------------------------------------------------------ ticks */

    var toastTimer = 0;
    function toast(text) {
        var el = $("lp-toast");
        el.textContent = text;
        el.classList.add("on");
        clearTimeout(toastTimer);
        toastTimer = setTimeout(function () { el.classList.remove("on"); }, 2200);
    }

    function toggle(id, on) {
        if (synced[id]) return;
        if (on) local[id] = today; else delete local[id];
        writeLocal();
        var d = dayOf[id];
        renderAll();
        if (openDate) openDetail(openDate, true);
        if (on && d) {
            var n = dayDone(d);
            toast(n === d.tasks.length ? "「" + d.label + "」全部完成！" : "完成一项 · " + n + "/" + d.tasks.length);
        }
    }

    function renderAll() {
        renderHero();
        renderToday();
        renderCalendar();
    }

    function bind() {
        document.addEventListener("click", function (e) {
            var opener = e.target.closest("[data-open]");
            if (opener) { openDetail(opener.getAttribute("data-open")); return; }
            if (e.target.closest("[data-close]")) closeDetail();
        });
        document.addEventListener("change", function (e) {
            var box = e.target.closest("input[data-task]");
            if (box) toggle(box.getAttribute("data-task"), box.checked);
        });
        document.addEventListener("keydown", function (e) {
            if (e.key === "Escape") closeDetail();
        });
        window.addEventListener("hashchange", function () {
            var h = location.hash.slice(1);
            if (h) openDetail(h, true); else closeDetail();
        });
    }

    readLocal();
    bind();
    Promise.all([load("plan.json"), load("log.json").catch(function () { return { days: {} }; })]).then(function (res) {
        plan = res[0];
        log = res[1] && res[1].days ? res[1] : { days: {} };
        index();
        renderAll();
        var h = location.hash.slice(1);
        if (h) openDetail(h, true);
    }).catch(function () {
        $("lp-today").innerHTML = "<p>学习计划加载失败，请刷新再试。</p>";
    });
})();
