/**
 * 轻量局部导航：侧栏与主区内同源 GET 链接触发，仅替换主内容区，避免整页重建侧栏。
 */
(function () {
  "use strict";

  var BODY_ID = "admin-page-body";
  var TITLE_ID = "admin-page-title";
  var PJAX_HEADER = "X-Admin-Pjax";

  function sameOrigin(href) {
    try {
      var u = new URL(href, window.location.origin);
      return u.origin === window.location.origin;
    } catch (e) {
      return false;
    }
  }

  /**
   * 评价报告书编辑页依赖 extra_head（KaTeX / 大段专用 CSS）。
   * PJAX 只替换 #admin-page-body，不会带上头资源，章切换会表现为“渲染失败”。
   */
  function requiresFullPage(href) {
    try {
      var path = new URL(href, window.location.origin).pathname;
    } catch (e) {
      return false;
    }
    if (path.indexOf("/evaluation-reports/") < 0) return false;
    return (
      /\/evaluation-reports\/\d+\/edit(\/|$)/.test(path) ||
      /\/evaluation-reports\/templates\/[^/]+\/edit(\/|$)/.test(path)
    );
  }

  function currentPageRequiresFull() {
    return !!(
      document.querySelector("[data-admin-pjax-require-full]") ||
      document.getElementById("se-app") ||
      document.getElementById("fe-app") ||
      document.getElementById("te-app") ||
      document.getElementById("fl-app")
    );
  }

  function shouldHandleLink(a, event) {
    if (!a || a.tagName !== "A" || !a.href) return false;
    if (event && (event.defaultPrevented || event.button !== 0)) return false;
    if (event && (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)) return false;
    if (a.target && a.target !== "_self") return false;
    if (a.hasAttribute("download")) return false;
    if (a.getAttribute("data-admin-pjax") === "off") return false;
    if (a.closest && a.closest('[data-admin-pjax="off"]')) return false;
    if (requiresFullPage(a.href) || currentPageRequiresFull()) return false;
    var rel = (a.getAttribute("rel") || "").toLowerCase();
    if (rel.indexOf("external") >= 0 || rel.indexOf("noopener") >= 0) return false;
    if (!sameOrigin(a.href)) return false;
    var method = (a.getAttribute("data-method") || "").toLowerCase();
    if (method && method !== "get") return false;
    return true;
  }

  function isSidebarNav(el) {
    return el && el.closest && el.closest("aside nav");
  }

  function runScripts(root) {
    if (!root) return;
    root.querySelectorAll("script").forEach(function (old) {
      var s = document.createElement("script");
      Array.prototype.forEach.call(old.attributes, function (attr) {
        s.setAttribute(attr.name, attr.value);
      });
      s.textContent = old.textContent;
      old.parentNode.replaceChild(s, old);
    });
  }

  function setActiveSidebar(href) {
    var path;
    try {
      path = new URL(href, window.location.origin).pathname;
    } catch (e) {
      return;
    }
    document.querySelectorAll("aside nav a.sidebar-item, aside nav a[href]").forEach(function (a) {
      if (!a.href) return;
      try {
        var ap = new URL(a.href, window.location.origin).pathname;
        if (ap === path) {
          a.classList.add("active");
        } else {
          a.classList.remove("active");
        }
      } catch (e2) {
        /* ignore */
      }
    });
  }

  function navigate(href, push) {
    var body = document.getElementById(BODY_ID);
    if (!body || requiresFullPage(href) || currentPageRequiresFull()) {
      window.location.href = href;
      return;
    }
    body.setAttribute("aria-busy", "true");
    body.classList.add("opacity-60", "pointer-events-none");

    fetch(href, {
      credentials: "same-origin",
      headers: { Accept: "text/html", "X-Requested-With": "XMLHttpRequest", "X-Admin-Pjax": "1" },
    })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.text();
      })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        var nextBody = doc.getElementById(BODY_ID);
        if (!nextBody) {
          window.location.href = href;
          return;
        }
        body.innerHTML = nextBody.innerHTML;
        runScripts(body);

        var nextTitle = doc.getElementById(TITLE_ID);
        var curTitle = document.getElementById(TITLE_ID);
        if (nextTitle && curTitle) {
          curTitle.textContent = nextTitle.textContent;
        }

        if (push !== false) {
          history.pushState({ adminPjax: true }, "", href);
        }
        setActiveSidebar(href);
        document.title = doc.title || document.title;
        window.dispatchEvent(new CustomEvent("admin-pjax:loaded", { detail: { href: href } }));
      })
      .catch(function () {
        window.location.href = href;
      })
      .finally(function () {
        body.removeAttribute("aria-busy");
        body.classList.remove("opacity-60", "pointer-events-none");
      });
  }

  document.addEventListener("click", function (event) {
    var a = event.target && event.target.closest ? event.target.closest("a") : null;
    if (!a || !shouldHandleLink(a, event)) return;
    if (!isSidebarNav(a) && !(a.closest && a.closest("#" + BODY_ID))) return;
    event.preventDefault();
    navigate(a.href, true);
  });

  window.addEventListener("popstate", function (event) {
    if (event.state && event.state.adminPjax) {
      navigate(window.location.href, false);
    }
  });
})();
