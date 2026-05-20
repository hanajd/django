/**
 * 文件库 / 项目工作台：文件夹拖放（左侧树 + 中间文件夹网格）。
 * 在名称/标题上按住拖动；单击仍用于打开文件夹。
 */
(function () {
    var dragState = null;
    var didDrag = false;

    function csrfToken() {
        var m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return m ? decodeURIComponent(m[1]) : '';
    }

    function readDragFromEl(el) {
        if (!el) return null;
        var type = el.getAttribute('data-drag-type') || '';
        var pk = el.getAttribute('data-drag-id') || el.getAttribute('data-entity-pk') || '';
        if (!type || !pk) return null;
        return {
            type: type,
            pk: pk,
            orgLevel: el.getAttribute('data-org-level') || '',
        };
    }

    function dropAccepts(dropEl) {
        return (dropEl.getAttribute('data-drop-accepts') || '')
            .split(',')
            .map(function (s) {
                return s.trim();
            })
            .filter(Boolean);
    }

    function dropTargetPk(dropEl) {
        return dropEl.getAttribute('data-drop-id') || dropEl.getAttribute('data-entity-pk') || '';
    }

    function canDrop(drag, dropEl) {
        if (!drag || !drag.type || !drag.pk) return false;
        var accepts = dropAccepts(dropEl);
        if (!accepts.length) return false;
        var targetPk = dropTargetPk(dropEl);
        if (targetPk === '' || targetPk === null || targetPk === undefined) return false;
        if (drag.type !== 'report' && targetPk === drag.pk) return false;
        var targetLevel = dropEl.getAttribute('data-drop-org-level') || dropEl.getAttribute('data-org-level') || '';
        if (drag.type === 'project') {
            return accepts.indexOf('project') >= 0;
        }
        if (drag.type === 'file') {
            return accepts.indexOf('file') >= 0 && drag.pk !== '0';
        }
        if (drag.type === 'org') {
            if (drag.orgLevel === 'hospital') return false;
            if (accepts.indexOf('org') < 0) return false;
            if (drag.orgLevel === 'campus') return targetLevel === 'hospital';
            if (drag.orgLevel === 'department') {
                return targetLevel === 'hospital' || targetLevel === 'campus';
            }
        }
        if (drag.type === 'report') {
            return accepts.indexOf('report') >= 0;
        }
        return false;
    }

    function findDropTarget(el) {
        if (!el || !el.closest) return null;
        return el.closest('[data-drop-accepts]');
    }

    function postMove(scope, fd) {
        var postUrl = scope.getAttribute('data-post-url') || '';
        if (!postUrl) return;
        fetch(postUrl, {
            method: 'POST',
            body: fd,
            headers: {
                'X-Requested-With': 'XMLHttpRequest',
                Accept: 'application/json',
            },
            credentials: 'same-origin',
        })
            .then(function (r) {
                return r.json().then(function (data) {
                    return { ok: r.ok, data: data };
                });
            })
            .then(function (res) {
                var data = res.data || {};
                if (data.ok) {
                    window.location.href = data.redirect || window.location.href;
                } else {
                    alert(data.message || '操作失败');
                }
            })
            .catch(function () {
                alert('网络错误，请重试');
            });
    }

    function clearDropHighlight() {
        document.querySelectorAll('[data-drop-accepts].dnd-drop-active').forEach(function (n) {
            n.classList.remove('dnd-drop-active', 'ring-2', 'ring-indigo-400', 'bg-indigo-50/50');
        });
    }

    function onDragStart(e) {
        var source = e.target.closest('[data-drag-type]');
        if (!source) return;
        dragState = readDragFromEl(source);
        if (!dragState) return;
        didDrag = true;
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', JSON.stringify(dragState));
        source.classList.add('opacity-60', 'ring-2', 'ring-indigo-300');
    }

    function onDragEnd(e) {
        var source = e.target.closest('[data-drag-type]');
        if (source) source.classList.remove('opacity-60', 'ring-2', 'ring-indigo-300');
        dragState = null;
        clearDropHighlight();
        window.setTimeout(function () {
            didDrag = false;
        }, 0);
    }

    function onSourceClick(e) {
        if (didDrag) {
            e.preventDefault();
            e.stopPropagation();
        }
    }

    function bindScope(scope) {
        if (!scope || scope.getAttribute('data-dnd-enabled') !== 'true') return;
        var mode = scope.getAttribute('data-dnd-mode') || 'workbench';
        var allowOrgMove = scope.getAttribute('data-dnd-allow-org-move') !== '0';

        scope.querySelectorAll('[data-drag-type]').forEach(function (el) {
            if (el.getAttribute('data-drag-type') === 'org' && !allowOrgMove) return;
            if (!el.hasAttribute('draggable')) {
                el.setAttribute('draggable', 'true');
            }
            el.addEventListener('dragstart', onDragStart);
            el.addEventListener('dragend', onDragEnd);
            el.addEventListener('click', onSourceClick, true);
        });

        scope.querySelectorAll('tr[data-file-drag-id]').forEach(function (tr) {
            tr.setAttribute('draggable', 'true');
            tr.classList.add('cursor-grab');
            tr.addEventListener('dragstart', function (e) {
                dragState = { type: 'file', pk: tr.getAttribute('data-file-drag-id') || '', orgLevel: '' };
                didDrag = true;
                e.dataTransfer.effectAllowed = 'copy';
                e.dataTransfer.setData('text/plain', JSON.stringify(dragState));
                tr.classList.add('bg-indigo-50/80');
            });
            tr.addEventListener('dragend', function () {
                tr.classList.remove('bg-indigo-50/80');
                dragState = null;
                clearDropHighlight();
                window.setTimeout(function () {
                    didDrag = false;
                }, 0);
            });
        });

        scope.addEventListener(
            'dragover',
            function (e) {
                var drag = dragState;
                if (!drag) return;
                var dropEl = findDropTarget(e.target);
                if (!dropEl || !scope.contains(dropEl)) return;
                if (!canDrop(drag, dropEl)) return;
                e.preventDefault();
                e.dataTransfer.dropEffect = mode === 'file_library' ? 'copy' : 'move';
                clearDropHighlight();
                dropEl.classList.add('dnd-drop-active', 'ring-2', 'ring-indigo-400', 'bg-indigo-50/50');
            },
            false
        );

        scope.addEventListener(
            'dragleave',
            function (e) {
                var dropEl = findDropTarget(e.target);
                if (!dropEl || !scope.contains(dropEl)) return;
                if (dropEl.contains(e.relatedTarget)) return;
                dropEl.classList.remove('dnd-drop-active', 'ring-2', 'ring-indigo-400', 'bg-indigo-50/50');
            },
            false
        );

        scope.addEventListener(
            'drop',
            function (e) {
                var dropEl = findDropTarget(e.target);
                if (!dropEl || !scope.contains(dropEl)) return;
                e.preventDefault();
                e.stopPropagation();
                dropEl.classList.remove('dnd-drop-active', 'ring-2', 'ring-indigo-400', 'bg-indigo-50/50');
                var drag = dragState;
                if (!drag) {
                    try {
                        drag = JSON.parse(e.dataTransfer.getData('text/plain'));
                    } catch (err) {
                        return;
                    }
                }
                if (!canDrop(drag, dropEl)) return;
                var targetPk = dropTargetPk(dropEl);

                var fd = new FormData();
                fd.append('csrfmiddlewaretoken', csrfToken());
                fd.append('fl_path', scope.getAttribute('data-fl-path') || '');

                if (mode === 'workbench') {
                    if (drag.type === 'project') {
                        fd.append('action', 'move_project_to_commission_org');
                        fd.append('project_id', drag.pk);
                        fd.append('target_org_id', targetPk);
                    } else if (drag.type === 'org') {
                        fd.append('action', 'move_commission_org_parent');
                        fd.append('org_id', drag.pk);
                        fd.append('target_org_id', targetPk);
                    } else {
                        return;
                    }
                } else if (mode === 'file_library' && drag.type === 'file') {
                    fd.append('action', 'dnd_attach_files_to_project');
                    fd.append('project_id', targetPk);
                    fd.append('file_ids', drag.pk);
                    fd.append('tab', scope.getAttribute('data-tab') || '');
                    fd.append('date_from', scope.getAttribute('data-date-from') || '');
                    fd.append('date_to', scope.getAttribute('data-date-to') || '');
                    fd.append('uploader', scope.getAttribute('data-uploader') || '');
                } else if (mode === 'task_library' && drag.type === 'report') {
                    fd.append('action', 'move_report_to_task_folder');
                    fd.append('report_task_id', drag.pk);
                    fd.append('target_folder_id', targetPk);
                } else {
                    return;
                }
                postMove(scope, fd);
            },
            false
        );
    }

    function init() {
        var scope = document.querySelector('[data-folder-explorer-dnd]');
        if (scope) bindScope(scope);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
