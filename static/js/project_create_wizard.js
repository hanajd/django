/**
 * 项目工作台 · 新建项目三步向导
 */
(function (global) {
    'use strict';

    function qs(id) {
        return document.getElementById(id);
    }

    function escapeHtml(s) {
        var d = document.createElement('div');
        d.textContent = s || '';
        return d.innerHTML;
    }

    function readJsonScript(id) {
        var el = qs(id);
        if (!el || !el.textContent) return null;
        try {
            return JSON.parse(el.textContent);
        } catch (e) {
            return null;
        }
    }

    function initProjectCreateWizard() {
        var modal = qs('project-create-modal');
        var form = qs('project-create-modal-form');
        var treeEl = qs('commission-org-picker-data');
        if (!modal || !form || !treeEl) return;

        var config = readJsonScript('project-create-wizard-config-data');
        if (!config) return;

        var hospitals = JSON.parse(treeEl.textContent);
        var orgById = {};
        function walk(nodes) {
            (nodes || []).forEach(function (n) {
                orgById[n.id] = n;
                walk(n.children);
            });
        }
        walk(hospitals);

        var initial = config.initial || {};
        var pathIds = [];
        var selectedOrgId = null;
        var currentStep = 1;
        var equipmentLoadedForOrg = null;
        var equipmentFolders = [];
        var eqFolderKey = null;
        /** @type {Object.<string, {row: object, inspection_type: string}>} */
        var selectedEquipment = {};

        var grid = qs('create-picker-folder-grid');
        var crumbs = qs('create-picker-breadcrumbs');
        var orgBackBtn = qs('create-picker-back-btn');
        var labelEl = qs('create-picker-selected-label');
        var hiddenOrg = qs('create-picker-commission-org-id');
        var selectCurrentBtn = qs('create-picker-select-current-btn');
        var hintEl = qs('create-picker-hint');
        var step1 = qs('project-create-step-1');
        var step2 = qs('project-create-step-2');
        var step3 = qs('project-create-step-3');
        var btnPrev = qs('project-create-btn-prev');
        var btnNext = qs('project-create-btn-next');
        var btnSubmit = qs('project-create-btn-submit');
        var eqGrid = qs('project-create-equipment-grid');
        var eqBackBtn = qs('project-create-eq-back-btn');
        var eqFolderLabel = qs('project-create-eq-folder-label');
        var eqFormFields = qs('project-create-equipment-form-fields');
        var eqSummary = qs('project-create-eq-selected-summary');
        var eqEmpty = qs('project-create-equipment-empty');
        var eqLoading = qs('project-create-equipment-loading');
        var eqScope = qs('project-create-eq-scope');
        var assigneeSelect = qs('project-create-primary-assignee');
        var stepIndicators = document.querySelectorAll('#project-create-step-indicator [data-step-ind]');

        function levelIcon(level) {
            if (level === 'hospital') return 'ri-hospital-line text-rose-500';
            if (level === 'campus') return 'ri-building-2-line text-amber-600';
            if (level === 'department') return 'ri-stethoscope-line text-teal-600';
            return 'ri-folder-fill text-amber-500';
        }

        function currentOrg() {
            if (!pathIds.length) return null;
            return orgById[pathIds[pathIds.length - 1]] || null;
        }

        function currentChildren() {
            if (!pathIds.length) return hospitals;
            var cur = currentOrg();
            return cur && cur.children ? cur.children : [];
        }

        function updateOrgBackButton() {
            if (!orgBackBtn) return;
            if (pathIds.length) orgBackBtn.classList.remove('hidden');
            else orgBackBtn.classList.add('hidden');
        }

        function updateSelectionUI() {
            if (!labelEl || !hiddenOrg) return;
            if (selectedOrgId && orgById[selectedOrgId]) {
                labelEl.textContent = orgById[selectedOrgId].full_display_name;
                hiddenOrg.value = String(selectedOrgId);
            } else {
                labelEl.innerHTML = '<span class="text-slate-400">请进入并选择医院 / 院区 / 科室</span>';
                hiddenOrg.value = '';
            }
            if (selectCurrentBtn) selectCurrentBtn.disabled = !currentOrg();
        }

        function renderCrumbs() {
            if (!crumbs) return;
            crumbs.innerHTML = '';
            var rootBtn = document.createElement('button');
            rootBtn.type = 'button';
            rootBtn.className = 'hover:text-indigo-600 font-medium';
            rootBtn.textContent = '全部医院';
            rootBtn.addEventListener('click', function () {
                pathIds = [];
                renderOrgGrid();
            });
            crumbs.appendChild(rootBtn);
            pathIds.forEach(function (oid, idx) {
                var sep = document.createElement('span');
                sep.className = 'text-slate-300';
                sep.textContent = '/';
                crumbs.appendChild(sep);
                var o = orgById[oid];
                if (!o) return;
                var btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'hover:text-indigo-600 truncate max-w-[8rem]';
                btn.textContent = o.name;
                btn.addEventListener('click', function () {
                    pathIds = pathIds.slice(0, idx + 1);
                    renderOrgGrid();
                });
                crumbs.appendChild(btn);
            });
        }

        function selectOrg(id) {
            selectedOrgId = id;
            updateSelectionUI();
            if (hintEl) {
                hintEl.classList.remove('hidden');
                hintEl.textContent = '已选择：' + (orgById[id] ? orgById[id].full_display_name : '');
            }
        }

        function renderOrgGrid() {
            if (!grid) return;
            renderCrumbs();
            updateOrgBackButton();
            grid.innerHTML = '';
            var children = currentChildren();
            if (!children.length) {
                var empty = document.createElement('p');
                empty.className = 'col-span-3 text-center text-sm text-slate-400 py-8';
                empty.textContent = pathIds.length
                    ? '此层级下暂无下级，可点「选当前层级」'
                    : '暂无医院，请先在「医院信息管理」中创建';
                grid.appendChild(empty);
            } else {
                children.forEach(function (node) {
                    var card = document.createElement('div');
                    card.className =
                        'flex flex-col rounded-xl border border-slate-200 bg-white p-2.5 hover:border-indigo-300 transition-all';
                    var top = document.createElement('button');
                    top.type = 'button';
                    top.className = 'flex flex-col items-center gap-1 text-center w-full';
                    top.innerHTML =
                        '<span class="flex h-9 w-9 items-center justify-center rounded-lg bg-amber-50"><i class="' +
                        levelIcon(node.level) +
                        ' text-lg"></i></span><span class="text-xs font-medium text-slate-900 line-clamp-2">' +
                        escapeHtml(node.name) +
                        '</span>';
                    top.addEventListener('click', function () {
                        if (node.children && node.children.length) {
                            pathIds.push(node.id);
                            renderOrgGrid();
                        } else {
                            selectOrg(node.id);
                        }
                    });
                    card.appendChild(top);
                    var pick = document.createElement('button');
                    pick.type = 'button';
                    pick.className =
                        'mt-1.5 w-full py-0.5 text-[10px] font-medium text-indigo-700 rounded border border-indigo-100 bg-indigo-50/80 hover:bg-indigo-100';
                    pick.textContent = '选此层级';
                    pick.addEventListener('click', function (e) {
                        e.stopPropagation();
                        selectOrg(node.id);
                    });
                    card.appendChild(pick);
                    grid.appendChild(card);
                });
            }
            updateSelectionUI();
            var cur = currentOrg();
            if (hintEl) {
                if (cur) {
                    hintEl.classList.remove('hidden');
                    hintEl.textContent =
                        '当前：' + cur.full_display_name + ' — 可进入下级、点「返回上一级」或「选当前层级」';
                } else {
                    hintEl.classList.add('hidden');
                }
            }
        }

        function syncEquipmentFormFields() {
            if (!eqFormFields) return;
            eqFormFields.innerHTML = '';
            Object.keys(selectedEquipment).forEach(function (eid) {
                var sel = selectedEquipment[eid];
                var cb = document.createElement('input');
                cb.type = 'hidden';
                cb.name = 'equipment_ids';
                cb.value = eid;
                eqFormFields.appendChild(cb);
                var it = document.createElement('input');
                it.type = 'hidden';
                it.name = 'inspection_type_' + eid;
                it.value = sel.inspection_type || '';
                eqFormFields.appendChild(it);
            });
            if (eqSummary) {
                var n = Object.keys(selectedEquipment).length;
                if (n) {
                    eqSummary.classList.remove('hidden');
                    eqSummary.textContent = '已选 ' + n + ' 台设备（提交时将按各自检测类型绑定报告模板）';
                } else {
                    eqSummary.classList.add('hidden');
                }
            }
        }

        function toggleEquipmentSelection(row) {
            var eid = String(row.equipment_id);
            if (selectedEquipment[eid]) {
                delete selectedEquipment[eid];
            } else {
                selectedEquipment[eid] = {
                    row: row,
                    inspection_type: row.default_inspection_type || (row.type_options[0] && row.type_options[0].inspection_type) || '',
                };
            }
            syncEquipmentFormFields();
            renderEquipmentGrid();
        }

        function renderEquipmentGrid() {
            if (!eqGrid) return;
            eqGrid.innerHTML = '';
            if (eqEmpty) eqEmpty.classList.add('hidden');

            if (!eqFolderKey) {
                if (eqBackBtn) eqBackBtn.classList.add('hidden');
                if (eqFolderLabel) eqFolderLabel.textContent = '请选择科室文件夹';
                if (!equipmentFolders.length) {
                    if (eqEmpty) eqEmpty.classList.remove('hidden');
                    return;
                }
                equipmentFolders.forEach(function (folder) {
                    var card = document.createElement('button');
                    card.type = 'button';
                    card.className =
                        'flex flex-col items-center gap-1.5 rounded-xl border border-slate-200 bg-white p-3 hover:border-indigo-300 hover:shadow-sm text-center';
                    card.innerHTML =
                        '<span class="flex h-10 w-10 items-center justify-center rounded-lg bg-amber-50"><i class="ri-folder-fill text-amber-500 text-xl"></i></span>' +
                        '<span class="text-xs font-semibold text-slate-900 line-clamp-2">' +
                        escapeHtml(folder.name) +
                        '</span><span class="text-[10px] text-slate-500">' +
                        folder.equipment_count +
                        ' 台设备</span>';
                    card.addEventListener('click', function () {
                        eqFolderKey = folder.folder_key;
                        renderEquipmentGrid();
                    });
                    eqGrid.appendChild(card);
                });
                return;
            }

            var folder = equipmentFolders.find(function (f) {
                return f.folder_key === eqFolderKey;
            });
            if (!folder) {
                eqFolderKey = null;
                renderEquipmentGrid();
                return;
            }
            if (eqBackBtn) eqBackBtn.classList.remove('hidden');
            if (eqFolderLabel) eqFolderLabel.textContent = '当前：' + folder.name;

            (folder.equipments || []).forEach(function (row) {
                var eid = String(row.equipment_id);
                var picked = !!selectedEquipment[eid];
                var card = document.createElement('div');
                card.className =
                    'flex flex-col rounded-xl border p-2.5 transition-all cursor-pointer ' +
                    (picked
                        ? 'border-indigo-400 bg-indigo-50/80 ring-2 ring-indigo-300'
                        : 'border-slate-200 bg-white hover:border-indigo-300');

                var head = document.createElement('button');
                head.type = 'button';
                head.className = 'flex flex-col items-center gap-1 text-center w-full';
                head.innerHTML =
                    '<span class="flex h-9 w-9 items-center justify-center rounded-lg ' +
                    (picked ? 'bg-indigo-100' : 'bg-slate-100') +
                    '"><i class="ri-cpu-line text-lg ' +
                    (picked ? 'text-indigo-600' : 'text-slate-500') +
                    '"></i></span><span class="text-xs font-semibold text-slate-900 line-clamp-2">' +
                    escapeHtml(row.name) +
                    '</span>';
                if (row.model || row.serial_no) {
                    head.innerHTML +=
                        '<span class="text-[10px] text-slate-500">' +
                        escapeHtml([row.model, row.serial_no].filter(Boolean).join(' / ')) +
                        '</span>';
                }
                head.addEventListener('click', function () {
                    toggleEquipmentSelection(row);
                });
                card.appendChild(head);

                if (picked && (row.type_options || []).length) {
                    var selWrap = document.createElement('label');
                    selWrap.className = 'mt-2 block w-full';
                    selWrap.innerHTML = '<span class="text-[10px] font-medium text-indigo-800">本次检测类型 *</span>';
                    var sel = document.createElement('select');
                    sel.className =
                        'mt-0.5 w-full text-[11px] rounded-lg border-0 ring-1 ring-indigo-200 px-2 py-1.5 bg-white';
                    (row.type_options || []).forEach(function (o) {
                        var opt = document.createElement('option');
                        opt.value = o.inspection_type || '';
                        opt.textContent = (o.inspection_type || '') + ' · ' + (o.task_label || '');
                        if (o.inspection_type === selectedEquipment[eid].inspection_type) opt.selected = true;
                        sel.appendChild(opt);
                    });
                    sel.addEventListener('click', function (e) {
                        e.stopPropagation();
                    });
                    sel.addEventListener('change', function (e) {
                        e.stopPropagation();
                        selectedEquipment[eid].inspection_type = sel.value;
                        syncEquipmentFormFields();
                    });
                    selWrap.appendChild(sel);
                    card.appendChild(selWrap);
                }

                var badge = document.createElement('span');
                badge.className =
                    'mt-1.5 text-[10px] font-medium ' + (picked ? 'text-indigo-700' : 'text-slate-400');
                badge.textContent = picked ? '已选 · 再点取消' : '点击选择';
                card.appendChild(badge);

                eqGrid.appendChild(card);
            });
        }

        function resetPickerFromInitial() {
            pathIds = Array.isArray(initial.path) ? initial.path.slice() : [];
            selectedOrgId = initial.orgId || null;
            equipmentLoadedForOrg = null;
            equipmentFolders = [];
            eqFolderKey = null;
            selectedEquipment = {};
            syncEquipmentFormFields();
            renderOrgGrid();
        }

        function setStep(step) {
            currentStep = step;
            if (step1) step1.classList.toggle('hidden', step !== 1);
            if (step2) step2.classList.toggle('hidden', step !== 2);
            if (step3) step3.classList.toggle('hidden', step !== 3);
            if (selectCurrentBtn) selectCurrentBtn.classList.toggle('hidden', step !== 1);
            if (btnPrev) btnPrev.classList.toggle('hidden', step <= 1);
            if (btnNext) btnNext.classList.toggle('hidden', step >= 3);
            if (btnSubmit) btnSubmit.classList.toggle('hidden', step !== 3);
            stepIndicators.forEach(function (li) {
                var n = parseInt(li.getAttribute('data-step-ind'), 10);
                if (n === step) {
                    li.className = 'px-2.5 py-1 rounded-full bg-indigo-600 text-white';
                } else if (n < step) {
                    li.className = 'px-2.5 py-1 rounded-full bg-indigo-100 text-indigo-800';
                } else {
                    li.className = 'px-2.5 py-1 rounded-full bg-slate-100 text-slate-500';
                }
            });
        }

        function loadEquipmentForOrg(orgId) {
            if (!config.optionsUrl) return Promise.resolve();
            if (equipmentLoadedForOrg === orgId) {
                renderEquipmentGrid();
                return Promise.resolve();
            }
            if (eqLoading) eqLoading.classList.remove('hidden');
            if (eqEmpty) eqEmpty.classList.add('hidden');
            if (eqGrid) eqGrid.innerHTML = '';
            eqFolderKey = null;
            return fetch(config.optionsUrl + '?org_id=' + encodeURIComponent(orgId), {
                credentials: 'same-origin',
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
            })
                .then(function (r) {
                    return r.json();
                })
                .then(function (data) {
                    if (eqLoading) eqLoading.classList.add('hidden');
                    if (!data || !data.ok) {
                        equipmentFolders = [];
                        if (eqScope) eqScope.textContent = data && data.message ? data.message : '加载失败';
                        renderEquipmentGrid();
                        return;
                    }
                    equipmentLoadedForOrg = orgId;
                    equipmentFolders = data.equipment_folders || [];
                    if (eqScope && data.org) {
                        eqScope.textContent =
                            '范围：' + (data.org.scope_label || data.org.full_display_name || '');
                    }
                    renderEquipmentGrid();
                })
                .catch(function () {
                    if (eqLoading) eqLoading.classList.add('hidden');
                    equipmentFolders = [];
                    if (eqScope) eqScope.textContent = '加载设备失败，请重试';
                    renderEquipmentGrid();
                });
        }

        function validateStep1() {
            var name = (qs('create-picker-project-name') && qs('create-picker-project-name').value || '').trim();
            if (!name) {
                alert('请填写项目名称');
                return false;
            }
            if (!hiddenOrg || !hiddenOrg.value) {
                var cur = currentOrg();
                if (cur) hiddenOrg.value = String(cur.id);
            }
            if (!hiddenOrg || !hiddenOrg.value) {
                alert('请选择委托单位：逐级进入医院/院区/科室，并点「选当前层级」或「选此层级」');
                return false;
            }
            return true;
        }

        function validateStep2() {
            var keys = Object.keys(selectedEquipment);
            for (var i = 0; i < keys.length; i++) {
                var eid = keys[i];
                if (!(selectedEquipment[eid].inspection_type || '').trim()) {
                    alert('已选设备须选择本次检测类型');
                    return false;
                }
            }
            return true;
        }

        function populateAssignees() {
            if (!assigneeSelect) return;
            while (assigneeSelect.options.length > 1) assigneeSelect.remove(1);
            (config.assignUsers || []).forEach(function (u) {
                var opt = document.createElement('option');
                opt.value = String(u.id);
                opt.textContent = u.label;
                assigneeSelect.appendChild(opt);
            });
        }

        function openModal() {
            resetPickerFromInitial();
            setStep(1);
            modal.classList.remove('hidden');
            modal.setAttribute('aria-hidden', 'false');
            document.body.classList.add('overflow-hidden');
        }

        function closeModal() {
            modal.classList.add('hidden');
            modal.setAttribute('aria-hidden', 'true');
            document.body.classList.remove('overflow-hidden');
        }

        if (orgBackBtn) {
            orgBackBtn.addEventListener('click', function () {
                if (!pathIds.length) return;
                pathIds.pop();
                renderOrgGrid();
            });
        }

        if (eqBackBtn) {
            eqBackBtn.addEventListener('click', function () {
                eqFolderKey = null;
                renderEquipmentGrid();
            });
        }

        if (selectCurrentBtn) {
            selectCurrentBtn.addEventListener('click', function () {
                var cur = currentOrg();
                if (cur) selectOrg(cur.id);
            });
        }

        if (btnNext) {
            btnNext.addEventListener('click', function () {
                if (currentStep === 1) {
                    if (!validateStep1()) return;
                    setStep(2);
                    loadEquipmentForOrg(parseInt(hiddenOrg.value, 10));
                } else if (currentStep === 2) {
                    if (!validateStep2()) return;
                    setStep(3);
                }
            });
        }

        if (btnPrev) {
            btnPrev.addEventListener('click', function () {
                if (currentStep > 1) setStep(currentStep - 1);
            });
        }

        form.addEventListener('submit', function (e) {
            if (!validateStep1()) {
                e.preventDefault();
                setStep(1);
                return;
            }
            if (!validateStep2()) {
                e.preventDefault();
                setStep(2);
            }
        });

        modal.querySelectorAll('[data-project-create-dismiss]').forEach(function (el) {
            el.addEventListener('click', closeModal);
        });

        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && !modal.classList.contains('hidden')) closeModal();
        });

        function bindOpenButton(el) {
            if (!el) return;
            el.addEventListener('click', function (ev) {
                ev.preventDefault();
                openModal();
            });
        }

        bindOpenButton(qs('guide-project-create'));

        global.openProjectCreateModal = openModal;
        populateAssignees();
        resetPickerFromInitial();
        setStep(1);
    }

    function boot() {
        initProjectCreateWizard();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }

    global.initProjectCreateWizard = initProjectCreateWizard;
})(window);
