// Fucking Life: theme switch and comments. Hand-written; the publisher never rewrites this file.
(function () {
  var root = document.documentElement;

  function systemDark() {
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }

  function currentTheme() {
    var set = root.dataset.theme;
    if (set === "dark" || set === "light") return set;
    return systemDark() ? "dark" : "light";
  }

  function save(theme) {
    try { localStorage.setItem("theme", theme); } catch (e) { /* private mode: keep it for this page only */ }
  }

  var button = document.querySelector(".theme-btn");
  if (button) {
    button.addEventListener("click", function () {
      var next = currentTheme() === "dark" ? "light" : "dark";
      root.dataset.theme = next;
      save(next);
      syncComments(next);
    });
  }

  // utterances comments, loaded with the theme that is showing.
  var comments = document.getElementById("comments");
  function syncComments(theme) {
    var frame = document.querySelector(".utterances-frame");
    if (!frame || !frame.contentWindow) return;
    frame.contentWindow.postMessage(
      { type: "set-theme", theme: theme === "dark" ? "github-dark" : "github-light" },
      "https://utteranc.es"
    );
  }
  if (comments && comments.dataset.repo) {
    var script = document.createElement("script");
    script.src = "https://utteranc.es/client.js";
    script.async = true;
    script.crossOrigin = "anonymous";
    script.setAttribute("repo", comments.dataset.repo);
    script.setAttribute("issue-term", comments.dataset.issueTerm || "pathname");
    script.setAttribute("theme", currentTheme() === "dark" ? "github-dark" : "github-light");
    comments.appendChild(script);
  }
})();
