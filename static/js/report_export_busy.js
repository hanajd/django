/**
 * 报告导出 / 合并：全屏忙碌提示（不确定进度 + 已等待秒数），避免长耗时被误认为页面卡死。
 * 全页 POST 期间保持显示，直至导航完成；全局只加载一次，兼容 PJAX 后仍用事件委托。
 */
(function () {
    "use strict";

    var OVERLAY_ID = "report-export-busy-overlay";
    var STYLE_ID = "report-export-busy-style";
    var timerId = null;
    var tipId = null;
    var startedAt = 0;
    var active = false;

    var KIND_COPY = {
        export: {
            title: "正在导出报告",
            detail: "系统正在根据现场记录生成报告 PDF，耗时取决于模板与数据量，通常需要数秒到数十秒。",
            tips: [
                "正在渲染版式与字段，请稍候…",
                "文件较大或含多页插图时会更久一些…",
                "请勿关闭、刷新或后退本页…",
                "完成后将自动跳转到结果页…",
            ],
        },
        merge: {
            title: "正在合并报告",
            detail: "系统正在合并勾选的报告 PDF（含目录与页码处理），份数越多耗时越长。",
            tips: [
                "正在拼合页面与目录，请稍候…",
                "多份报告合订可能需要更长时间…",
                "请勿关闭、刷新或后退本页…",
                "完成后将自动跳转到结果页…",
            ],
        },
    };

    function ensureStyle() {
        if (document.getElementById(STYLE_ID)) return;
        var style = document.createElement("style");
        style.id = STYLE_ID;
        style.textContent =
            "@keyframes report-export-busy-slide{" +
            "0%{transform:translateX(-100%)}" +
            "100%{transform:translateX(280%)}" +
            "}" +
            "@keyframes report-export-busy-pulse{" +
            "0%,100%{opacity:1}" +
            "50%{opacity:.55}" +
            "}" +
            "#" +
            OVERLAY_ID +
            " .report-export-busy-thumb{" +
            "width:40%;min-width:4.5rem;" +
            "animation:report-export-busy-slide 1.4s ease-in-out infinite" +
            "}" +
            "#" +
            OVERLAY_ID +
            " .report-export-busy-pulse{" +
            "animation:report-export-busy-pulse 1.8s ease-in-out infinite" +
            "}";
        document.head.appendChild(style);
    }

    function ensureOverlay() {
        ensureStyle();
        var el = document.getElementById(OVERLAY_ID);
        if (el) return el;
        el = document.createElement("div");
        el.id = OVERLAY_ID;
        el.className =
            "hidden fixed inset-0 z-[200] flex items-center justify-center bg-slate-900/40 px-4 backdrop-blur-[1px]";
        el.setAttribute("role", "alertdialog");
        el.setAttribute("aria-modal", "true");
        el.setAttribute("aria-live", "polite");
        el.setAttribute("aria-busy", "false");
        el.innerHTML =
            '<div class="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-6 shadow-2xl sm:p-8">' +
            '  <div class="mb-4 flex items-start gap-3">' +
            '    <span class="report-export-busy-pulse inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-indigo-100 text-indigo-600">' +
            '      <svg class="h-5 w-5 animate-spin" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" aria-hidden="true">' +
            '        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>' +
            '        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>' +
            "      </svg>" +
            "    </span>" +
            '    <div class="min-w-0">' +
            '      <p id="report-export-busy-title" class="text-base font-semibold text-slate-900">正在处理</p>' +
            '      <p id="report-export-busy-detail" class="mt-1 text-xs leading-relaxed text-slate-500"></p>' +
            "    </div>" +
            "  </div>" +
            '  <p id="report-export-busy-tip" class="mb-3 text-xs font-medium text-indigo-700"></p>' +
            '  <div class="mb-2 h-2.5 w-full overflow-hidden rounded-full bg-slate-200">' +
            '    <div class="report-export-busy-thumb h-full rounded-full bg-gradient-to-r from-indigo-400 via-indigo-600 to-indigo-400"></div>' +
            "  </div>" +
            '  <div class="flex items-center justify-between gap-3 text-[11px] text-slate-400">' +
            '    <span>进度不确定：表示任务仍在进行，并非卡死</span>' +
            '    <span id="report-export-busy-elapsed" class="shrink-0 tabular-nums font-medium text-slate-600">已等待 0 秒</span>' +
            "  </div>" +
            "</div>";
        document.body.appendChild(el);
        return el;
    }

    function clearTimers() {
        if (timerId) {
            clearInterval(timerId);
            timerId = null;
        }
        if (tipId) {
            clearInterval(tipId);
            tipId = null;
        }
    }

    function hide() {
        active = false;
        clearTimers();
        var el = document.getElementById(OVERLAY_ID);
        if (!el) return;
        el.classList.add("hidden");
        el.setAttribute("aria-busy", "false");
        document.documentElement.classList.remove("overflow-hidden");
    }

    function show(kind) {
        var copy = KIND_COPY[kind] || KIND_COPY.export;
        var el = ensureOverlay();
        var title = document.getElementById("report-export-busy-title");
        var detail = document.getElementById("report-export-busy-detail");
        var tip = document.getElementById("report-export-busy-tip");
        var elapsed = document.getElementById("report-export-busy-elapsed");
        if (title) title.textContent = copy.title;
        if (detail) detail.textContent = copy.detail;
        if (tip) tip.textContent = copy.tips[0] || "";
        if (elapsed) elapsed.textContent = "已等待 0 秒";

        clearTimers();
        startedAt = Date.now();
        active = true;
        el.classList.remove("hidden");
        el.setAttribute("aria-busy", "true");
        document.documentElement.classList.add("overflow-hidden");

        timerId = setInterval(function () {
            if (!elapsed) return;
            var sec = Math.floor((Date.now() - startedAt) / 1000);
            elapsed.textContent = "已等待 " + sec + " 秒";
        }, 1000);

        var tipIdx = 0;
        tipId = setInterval(function () {
            if (!tip || !copy.tips.length) return;
            tipIdx = (tipIdx + 1) % copy.tips.length;
            tip.textContent = copy.tips[tipIdx];
        }, 3500);
    }

    function actionFromForm(form, submitter) {
        if (submitter && submitter.getAttribute) {
            var sn = submitter.getAttribute("name");
            var sv = submitter.getAttribute("value");
            if (sn === "action" && sv) return sv;
        }
        var hidden = form.querySelector('input[name="action"]');
        return hidden && hidden.value ? String(hidden.value) : "";
    }

    function kindFromAction(action) {
        if (!action) return "";
        if (
            action === "hub_export_report" ||
            action === "manual_export_report_from_site_record" ||
            action === "export_site_folder_report" ||
            action === "export_report_folder_merged"
        ) {
            return "export";
        }
        if (action === "hub_merge_reports" || action === "merge_reports") {
            return "merge";
        }
        return "";
    }

    function kindFromForm(form, submitter) {
        var explicit = (form.getAttribute("data-report-busy") || "").trim().toLowerCase();
        if (explicit === "off" || explicit === "0" || explicit === "false") return "";
        if (explicit === "export" || explicit === "merge") return explicit;
        return kindFromAction(actionFromForm(form, submitter));
    }

    function armBusy(form, kind) {
        if (!form || !kind) return;
        if (form.getAttribute("data-report-busy-armed") === "1") return;
        form.setAttribute("data-report-busy-armed", "1");
        show(kind);
        Array.prototype.forEach.call(form.querySelectorAll("button, input[type=submit]"), function (btn) {
            try {
                btn.disabled = true;
            } catch (e) {
                /* ignore */
            }
        });
        // 合并按钮在 form 外通过 form= 关联
        var formId = form.id;
        if (formId) {
            document.querySelectorAll('button[form="' + formId + '"]').forEach(function (btn) {
                try {
                    btn.disabled = true;
                } catch (e2) {
                    /* ignore */
                }
            });
        }
    }

    function onSubmit(event) {
        var form = event.target;
        if (!form || form.tagName !== "FORM") return;
        // 冒泡阶段：等 onclick/onsubmit 的 confirm 有机会 preventDefault
        if (event.defaultPrevented) return;
        var kind = kindFromForm(form, event.submitter || null);
        if (!kind) return;
        armBusy(form, kind);
    }

    // 不用 capture，避免先于 confirm 弹出遮罩
    document.addEventListener("submit", onSubmit, false);

    // 浏览器后退可能从 bfcache 恢复，遮罩应关掉
    window.addEventListener("pageshow", function (ev) {
        if (ev.persisted || active) hide();
    });

    window.ReportExportBusy = {
        show: show,
        hide: hide,
        kindFromAction: kindFromAction,
    };
})();
