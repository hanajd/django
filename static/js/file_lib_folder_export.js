/**
 * 文件库文件夹「导出合并报告」：全局只加载一次，避免 PJAX 重复绑定 document 点击导致多次 confirm。
 */
(function () {
    "use strict";

    function submitFolderExport(btn) {
        if (!btn) return false;
        if (btn.dataset.folderExportBusy === "1") return false;
        var form = document.getElementById("file-lib-folder-export-form");
        if (!form) return false;
        var msg = btn.getAttribute("data-confirm") || "确定导出报告？";
        if (!window.confirm(msg)) return false;
        btn.dataset.folderExportBusy = "1";
        var actionEl = document.getElementById("file-lib-folder-export-action");
        var projectEl = document.getElementById("file-lib-folder-export-project");
        var reportEl = document.getElementById("file-lib-folder-export-report");
        var siteEl = document.getElementById("file-lib-folder-export-site");
        if (actionEl) actionEl.value = btn.getAttribute("data-action") || "";
        if (projectEl) projectEl.value = btn.getAttribute("data-project-key") || "";
        if (reportEl) reportEl.value = btn.getAttribute("data-report-key") || "";
        if (siteEl) siteEl.value = btn.getAttribute("data-site-key") || "";
        form.submit();
        return false;
    }

    window.fileLibSubmitFolderExport = submitFolderExport;
})();
