(() => {
    "use strict";

    const supportsPopover = Object.hasOwn(HTMLElement.prototype, "popover");

    const state = {
        token: localStorage.getItem("ev_hub_token") || "",
        user: null,
        rolesMap: {},
        activePanel: "panel-driver",
        operatorsMode: "full",
        sessionInvoices: [],
        pages: {
            stations: 1,
            guns: 1,
            orders: 1,
            faults: 1,
            logs: 1,
            realtime: 1,
            bills: 1,
            diffs: 1,
        },
        pageSize: {
            stations: 9,
            guns: 12,
            orders: 10,
            faults: 12,
            logs: 12,
            realtime: 12,
            bills: 10,
            diffs: 10,
        },
        stationCache: new Map(),
        operatorCache: new Map(),
    };

    const TAB_ACCESS = {
        "panel-driver": {
            roles: ["driver", "fleet", "ops", "admin"],
            hint: "找站与开单视图面向车主（driver）、车队（fleet）、运维与管理员；扫码开单限车主或车队角色。",
            suggest: ["driver", "fleet"],
        },
        "panel-orders": {
            roles: ["driver", "fleet", "ops", "settlement", "admin"],
            hint: "全部角色均可查看订单列表（车主/车队自动按本人隔离，其余角色看全量）。",
            suggest: ["driver"],
        },
        "panel-ops": {
            roles: ["ops", "admin"],
            hint: "运维排障、心跳巡检与设备状态日志仅限场站运维（ops）与平台管理员（admin）访问。",
            suggest: ["ops", "admin"],
        },
        "panel-admin": {
            roles: ["admin", "settlement", "ops"],
            hint: "实时资产看板限管理员与结算专员；桩/枪钻取限运维与管理员；资产建档与编辑限管理员。",
            suggest: ["admin", "settlement"],
        },
        "panel-settle": {
            roles: ["settlement", "admin"],
            hint: "月度结算单、对账差异核销与发票开具/红冲仅限结算专员（settlement）与管理员（admin）。",
            suggest: ["settlement", "admin"],
        },
        "panel-cross": {
            roles: ["driver", "fleet", "ops", "settlement", "admin"],
            hint: "支持使用任意角色 Token 实测越权拦截与数据库约束。",
            suggest: [],
        },
    };

    const ROLE_LABELS = {
        driver: "普通车主",
        fleet: "企业车队",
        ops: "场站运维",
        settlement: "结算专员",
        admin: "平台管理员",
    };

    const $ = (id) => document.getElementById(id);

    function escapeHtml(raw) {
        if (raw === null || raw === undefined) return "";
        return String(raw)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function formatTime(val) {
        if (!val) return "-";
        return String(val).replace("T", " ").slice(0, 19);
    }

    function sumDecimalStrings(values, scale = 2) {
        const factor = 10n ** BigInt(scale);
        let totalScaled = 0n;
        for (const raw of values) {
            if (raw === null || raw === undefined || raw === "") continue;
            const s = String(raw).trim();
            const neg = s.startsWith("-");
            const clean = neg ? s.slice(1) : s;
            const [intPart = "0", fracPart = ""] = clean.split(".");
            const intVal = BigInt(intPart.replace(/\D/g, "") || "0");
            const paddedFrac = (fracPart.replace(/\D/g, "") + "0".repeat(scale)).slice(0, scale);
            const fracVal = BigInt(paddedFrac || "0");
            const combined = intVal * factor + fracVal;
            totalScaled += neg ? -combined : combined;
        }
        const negTotal = totalScaled < 0n;
        const abs = negTotal ? -totalScaled : totalScaled;
        const intOut = abs / factor;
        const fracOut = (abs % factor).toString().padStart(scale, "0");
        return `${negTotal ? "-" : ""}${intOut}.${fracOut}`;
    }

    function pctRatio(part, total) {
        const p = Number(part || 0);
        const t = Number(total || 0);
        if (!t || t <= 0) return 0;
        return Math.max(0, Math.min(100, Math.round((p / t) * 100)));
    }

    function genTxnNo(prefix = "TXN") {
        const ts = Date.now().toString(36).toUpperCase();
        const rand = Math.random().toString(36).slice(2, 6).toUpperCase();
        return `${prefix}${ts}${rand}`;
    }

    // ------------------------------------------------------------------------
    // 无障碍播报与 Top-Layer 可堆叠 Toast 系统
    // ------------------------------------------------------------------------
    const activeToasts = [];

    function announceStatus(message) {
        const live = $("liveAnnouncer");
        if (live) {
            live.textContent = "";
            requestAnimationFrame(() => {
                live.textContent = message;
            });
        }
        if (typeof document.body.ariaNotify === "function") {
            try {
                document.body.ariaNotify(message);
            } catch {
                // 忽略部分实验性浏览器的调用差异
            }
        }
    }

    function repositionToasts() {
        let offset = 0;
        for (let i = activeToasts.length - 1; i >= 0; i -= 1) {
            const item = activeToasts[i];
            item.el.style.setProperty("--toast-offset", `${offset}px`);
            const h = item.el.offsetHeight || 48;
            offset += h + 10;
        }
    }

    function dismissToast(toastEl) {
        const idx = activeToasts.findIndex((t) => t.el === toastEl);
        if (idx === -1) return;
        const hadFocus = toastEl.contains(document.activeElement);
        const [entry] = activeToasts.splice(idx, 1);
        if (entry.timer) clearTimeout(entry.timer);
        if (supportsPopover && toastEl.matches(":popover-open")) {
            try {
                toastEl.hidePopover();
            } catch {
                // 忽略已移除状态
            }
        } else {
            toastEl.classList.remove(":popover-open");
        }
        toastEl.remove();
        repositionToasts();
        if (hadFocus) {
            const nextToast = activeToasts[idx] || activeToasts[activeToasts.length - 1];
            const nextFocus = nextToast?.el.querySelector(".toast-close") || entry.returnFocusTo;
            if (nextFocus && document.contains(nextFocus)) {
                nextFocus.focus();
            }
        }
    }

    function showToast(message, isError = false) {
        // 打开模态对话框时，不向背景推送遮挡或无障碍不可达的 Toast
        if (document.querySelector("dialog[open]")) {
            announceStatus(message);
            return;
        }

        const returnFocusTo = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        const stack = $("toastStack");
        const toast = document.createElement("div");
        toast.className = `toast-item ${isError ? "toast-err" : "toast-ok"}`;
        if (supportsPopover) {
            toast.setAttribute("popover", "manual");
        }

        const msgSpan = document.createElement("span");
        msgSpan.textContent = message;

        const closeBtn = document.createElement("button");
        closeBtn.type = "button";
        closeBtn.className = "toast-close";
        closeBtn.textContent = "关闭";
        closeBtn.setAttribute("aria-label", "关闭这条通知");

        toast.append(msgSpan, closeBtn);
        stack.appendChild(toast);

        if (supportsPopover) {
            try {
                toast.showPopover();
            } catch {
                toast.classList.add(":popover-open");
            }
        } else {
            toast.classList.add(":popover-open");
        }

        const entry = { el: toast, timer: null, paused: false, returnFocusTo };
        const duration = isError ? 6000 : 3800;

        const scheduleHide = () => {
            if (entry.timer) clearTimeout(entry.timer);
            entry.timer = setTimeout(() => {
                if (!entry.paused) dismissToast(toast);
            }, duration);
        };

        toast.addEventListener("pointerenter", () => {
            entry.paused = true;
            if (entry.timer) clearTimeout(entry.timer);
        });
        toast.addEventListener("pointerleave", () => {
            entry.paused = false;
            scheduleHide();
        });
        toast.addEventListener("focusin", () => {
            entry.paused = true;
            if (entry.timer) clearTimeout(entry.timer);
        });
        toast.addEventListener("focusout", () => {
            entry.paused = false;
            scheduleHide();
        });
        closeBtn.addEventListener("click", () => {
            dismissToast(toast);
        });

        activeToasts.push(entry);
        if (activeToasts.length > 4) {
            dismissToast(activeToasts[0].el);
        }
        repositionToasts();
        scheduleHide();
        announceStatus(message);
    }

    // ------------------------------------------------------------------------
    // 原生 Dialog 辅助与降级兼容
    // ------------------------------------------------------------------------
    function setupDialogs() {
        const supportsClosedBy = "closedBy" in HTMLDialogElement.prototype;

        document.querySelectorAll("dialog").forEach((dlg) => {
            dlg.addEventListener("close", () => {
                dlg.returnValue = "";
            });

            if (!supportsClosedBy && dlg.getAttribute("closedby") === "any") {
                const isOutside = (e) => {
                    const r = dlg.getBoundingClientRect();
                    return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
                };
                let pressedOutside = false;
                dlg.addEventListener("pointerdown", (e) => {
                    pressedOutside = isOutside(e);
                });
                dlg.addEventListener("pointerup", (e) => {
                    if (pressedOutside && isOutside(e)) dlg.close();
                });
            }
        });

        document.addEventListener("click", (e) => {
            const closeBtn = e.target.closest("[data-close-dialog]");
            if (closeBtn) {
                const dlg = closeBtn.closest("dialog");
                if (dlg && dlg.open) dlg.close("cancel");
            }
        });

        // Popover 降级兼容（当浏览器不支持 popover 属性时）
        if (!supportsPopover) {
            const pop = $("identityPopover");
            const trigger = $("identityPopoverBtn");
            trigger?.addEventListener("click", () => {
                pop.classList.toggle(":popover-open");
            });
            document.addEventListener("click", (e) => {
                if (
                    pop.classList.contains(":popover-open") &&
                    !pop.contains(e.target) &&
                    !trigger.contains(e.target)
                ) {
                    pop.classList.remove(":popover-open");
                }
            });
        }
    }

    function setInlineAlert(el, message = "") {
        if (!el) return;
        if (message) {
            el.textContent = message;
            el.hidden = false;
        } else {
            el.textContent = "";
            el.hidden = true;
        }
    }

    // ------------------------------------------------------------------------
    // 网络请求与标准状态渲染
    // ------------------------------------------------------------------------
    async function apiFetch(url, options = {}) {
        const headers = new Headers(options.headers || {});
        if (state.token) {
            headers.set("Authorization", `Bearer ${state.token}`);
        }
        if (options.body && !headers.has("Content-Type")) {
            headers.set("Content-Type", "application/json");
        }
        const started = performance.now();
        const resp = await fetch(url, { ...options, headers });
        const elapsedMs = Math.round(performance.now() - started);
        const text = await resp.text();
        let data = text;
        try {
            data = text ? JSON.parse(text) : {};
        } catch {
            // 保留原始文本
        }
        if (!resp.ok) {
            const detail =
                typeof data === "object" && data !== null && data.detail
                    ? data.detail
                    : `请求失败 (${resp.status})`;
            const err = new Error(detail);
            err.status = resp.status;
            err.data = data;
            err.elapsedMs = elapsedMs;
            throw err;
        }
        return { status: resp.status, data, elapsedMs };
    }

    function statusBadge(st, customText = null) {
        const map = {
            IDLE: ["IDLE 空闲", "badge-ok"],
            OPEN: ["OPEN 营业中", "badge-ok"],
            ACTIVE: ["ACTIVE 正常", "badge-ok"],
            PASSED: ["PASSED 已验收", "badge-ok"],
            DONE: ["DONE 已办结", "badge-ok"],
            PAID: ["PAID 已支付", "badge-ok"],
            ISSUED: ["ISSUED 已开具", "badge-ok"],
            CONFIRMED: ["CONFIRMED 已确认", "badge-ok"],
            INVOICED: ["INVOICED 已开票", "badge-info"],
            OCCUPIED: ["OCCUPIED 占用", "badge-info"],
            CHARGING: ["CHARGING 充电中", "badge-info"],
            FINISHED: ["FINISHED 待支付", "badge-warn"],
            PENDING: ["PENDING 待处理", "badge-warn"],
            DRAFT: ["DRAFT 草稿", "badge-warn"],
            MAINTENANCE: ["MAINTENANCE 维护", "badge-warn"],
            SUSPENDED: ["SUSPENDED 暂停", "badge-warn"],
            FAULT: ["FAULT 故障", "badge-danger"],
            REJECTED: ["REJECTED 未通过", "badge-danger"],
            RED_FLUSHED: ["RED_FLUSHED 已红冲", "badge-danger"],
            REFUNDED: ["REFUNDED 已退款", "badge-warn"],
            OFFLINE: ["OFFLINE 离线", "badge-muted"],
            CLOSED: ["CLOSED 已关闭", "badge-muted"],
            CANCELLED: ["CANCELLED 已取消", "badge-muted"],
            NONE: ["NONE", "badge-muted"],
        };
        const [label, cls] = map[st] || [String(st || "-"), "badge-muted"];
        return `<span class="badge ${cls}">${escapeHtml(customText || label)}</span>`;
    }

    function renderPagination(container, pageResult, onPageChange) {
        if (!container) return;
        const total = Number(pageResult?.total || 0);
        const page = Number(pageResult?.page || 1);
        const pageSize = Number(pageResult?.page_size || 10);
        if (total === 0) {
            container.innerHTML = "";
            return;
        }
        const totalPages = Math.max(1, Math.ceil(total / pageSize));
        container.innerHTML = `
            <span>共 ${escapeHtml(total)} 条 · 第 ${escapeHtml(page)} / ${escapeHtml(totalPages)} 页</span>
            <button class="btn btn-outline btn-xs" data-page="${page - 1}" ${page <= 1 ? "disabled" : ""} type="button">上一页</button>
            <button class="btn btn-outline btn-xs" data-page="${page + 1}" ${page >= totalPages ? "disabled" : ""} type="button">下一页</button>
        `;
        container.querySelectorAll("button[data-page]").forEach((btn) => {
            btn.addEventListener("click", () => {
                const target = Number(btn.dataset.page);
                if (target >= 1 && target <= totalPages) onPageChange(target);
            });
        });
    }

    // ------------------------------------------------------------------------
    // 顶部指挥栏：探活、角色切换与身份上下文
    // ------------------------------------------------------------------------
    async function checkDbHealth() {
        const dot = $("dbHealthDot");
        const txt = $("dbHealthText");
        try {
            const { data } = await apiFetch("/health/db");
            dot.className = "status-dot ok";
            txt.textContent = `MySQL ${data.mysql_version} · ${data.database}`;
        } catch (err) {
            dot.className = "status-dot err";
            txt.textContent = `数据库不可用：${err.message}`;
        }
    }

    async function loadRolesMeta() {
        try {
            const { data } = await apiFetch("/api/auth/roles");
            for (const item of data) {
                state.rolesMap[item.role] = item;
            }
        } catch {
            // 忽略字典拉取失败
        }
    }

    function renderRoleNotice() {
        const banner = $("roleNoticeBanner");
        const textEl = $("roleNoticeText");
        const actionsEl = $("roleNoticeActions");
        const cfg = TAB_ACCESS[state.activePanel];
        const currentRole = state.user?.role;

        if (!cfg || !currentRole || cfg.roles.includes(currentRole)) {
            banner.hidden = true;
            actionsEl.innerHTML = "";
            return;
        }

        textEl.textContent = `当前身份为「${ROLE_LABELS[currentRole] || currentRole}」，${cfg.hint}`;
        actionsEl.innerHTML = cfg.suggest
            .map(
                (r) =>
                    `<button class="btn btn-outline btn-xs quick-role-btn" data-role="${escapeHtml(r)}" type="button">切换为 ${escapeHtml(ROLE_LABELS[r] || r)} (${escapeHtml(r)})</button>`
            )
            .join("");
        banner.hidden = false;
    }

    function renderUserContext() {
        const chip = $("identityChipText");
        const label = $("currentUserLabel");
        const desc = $("currentRoleDesc");

        document.querySelectorAll("#roleButtons .role-pill").forEach((btn) => {
            const active = Boolean(state.user && btn.dataset.role === state.user.role);
            btn.setAttribute("aria-pressed", active ? "true" : "false");
        });

        if (!state.user) {
            chip.textContent = "未认证";
            label.textContent = "未认证";
            desc.textContent = "请点击顶部任意角色按钮签发开发测试 Token。";
            renderRoleNotice();
            return;
        }

        const roleMeta = state.rolesMap[state.user.role];
        const fleetNote = state.user.fleet_id ? ` · fleet=${state.user.fleet_id}` : "";
        chip.textContent = `${state.user.role_label} · uid=${state.user.user_id}${fleetNote}`;
        label.textContent = `${state.user.username}（${state.user.role_label} / ${state.user.role} · uid=${state.user.user_id}${fleetNote}）`;
        desc.textContent = roleMeta ? roleMeta.description : "";

        // 同步联调参数输入框
        $("devUserId").value = state.user.user_id;
        if (state.user.fleet_id) {
            $("devFleetId").value = state.user.fleet_id;
        }
        renderRoleNotice();
    }

    async function switchDevRole(role) {
        const userId = Math.max(1, Number($("devUserId").value || 9001));
        const fleetId = Math.max(1, Number($("devFleetId").value || 1));
        try {
            const { data } = await apiFetch("/api/auth/dev-token", {
                method: "POST",
                body: JSON.stringify({
                    user_id: userId,
                    username: `demo_${role}`,
                    role,
                    fleet_id: role === "fleet" ? fleetId : null,
                }),
            });
            state.token = data.access_token;
            state.user = data.user;
            localStorage.setItem("ev_hub_token", state.token);
            renderUserContext();
            showToast(`已切换为 ${data.user.role_label}（uid=${data.user.user_id}）`);
            refreshActivePanelData();
        } catch (err) {
            showToast(err.message, true);
        }
    }

    async function restoreSessionOrDefault() {
        if (state.token) {
            try {
                const { data } = await apiFetch("/api/auth/me");
                state.user = data;
                renderUserContext();
                return;
            } catch {
                state.token = "";
                localStorage.removeItem("ev_hub_token");
            }
        }
        await switchDevRole("driver");
    }

    // ------------------------------------------------------------------------
    // Tab 1: 找站与开单
    // ------------------------------------------------------------------------
    async function loadDriverStations(page = state.pages.stations) {
        state.pages.stations = page;
        const grid = $("driverStationGrid");
        const pager = $("driverStationPager");
        const qs = new URLSearchParams({
            page: String(page),
            page_size: String(state.pageSize.stations),
        });
        const kw = $("stationKeyword").value.trim();
        if (kw) qs.set("keyword", kw);
        if ($("onlyIdleFast").checked) qs.set("only_idle_fast", "true");

        try {
            const { data } = await apiFetch(`/api/stations/overview?${qs.toString()}`);
            if (!data.items.length) {
                grid.innerHTML = '<div class="empty-hint">未找到符合条件的营业场站。</div>';
                pager.innerHTML = "";
                return;
            }
            grid.innerHTML = data.items
                .map((s) => {
                    const spacePct = pctRatio(s.available_spaces, s.total_spaces);
                    const gunPct = pctRatio(s.idle_gun_count, s.total_gun_count);
                    return `
                        <article class="station-card">
                            <div class="station-card-head">
                                <div>
                                    <h3>${escapeHtml(s.station_name)}</h3>
                                    <p class="meta">${escapeHtml(s.station_code)} · ${escapeHtml(s.operator_name)}</p>
                                </div>
                                <span class="badge badge-ok">营业中</span>
                            </div>
                            <div class="station-card-body">
                                <p class="meta">${escapeHtml(s.address)}</p>
                                <p class="meta">营业：${escapeHtml(s.business_hours)} ｜ 停车费：¥${escapeHtml(s.parking_fee_rate)}/h</p>
                                <div class="station-meters">
                                    <div class="meter-row">
                                        <div class="meter-label">
                                            <span>空闲车位</span>
                                            <strong>${escapeHtml(s.available_spaces)} / ${escapeHtml(s.total_spaces)}</strong>
                                        </div>
                                        <div class="meter-track"><div class="meter-fill meter-blue" style="--pct:${spacePct}%"></div></div>
                                    </div>
                                    <div class="meter-row">
                                        <div class="meter-label">
                                            <span>空闲充电枪（快 ${escapeHtml(s.idle_fast_gun_count)} / 慢 ${escapeHtml(s.idle_slow_gun_count)}）</span>
                                            <strong>${escapeHtml(s.idle_gun_count)} / ${escapeHtml(s.total_gun_count)}</strong>
                                        </div>
                                        <div class="meter-track"><div class="meter-fill" style="--pct:${gunPct}%"></div></div>
                                    </div>
                                </div>
                            </div>
                            <div class="station-card-foot">
                                <span class="meta">活跃桩 ${escapeHtml(s.active_pile_count)} 台</span>
                                <button class="btn btn-outline btn-xs view-station-guns" data-station-id="${escapeHtml(s.station_id)}" type="button">查看可用枪</button>
                            </div>
                        </article>`;
                })
                .join("");

            renderPagination(pager, data, (nextPage) => loadDriverStations(nextPage));
        } catch (err) {
            grid.innerHTML = `<div class="empty-hint">无法加载场站概览 (${escapeHtml(err.status || "-")})：${escapeHtml(err.message)}</div>`;
            pager.innerHTML = "";
        }
    }

    async function loadDriverGuns(page = state.pages.guns) {
        state.pages.guns = page;
        const tbody = $("driverGunsBody");
        const pager = $("driverGunsPager");
        const qs = new URLSearchParams({
            page: String(page),
            page_size: String(state.pageSize.guns),
        });
        const stId = $("gunStationFilter").value.trim();
        const gType = $("gunTypeFilter").value;
        const gStatus = $("gunStatusFilter").value;
        if (stId) qs.set("station_id", stId);
        if (gType) qs.set("gun_type", gType);
        if (gStatus) qs.set("gun_status", gStatus);

        try {
            const { data } = await apiFetch(`/api/stations/guns/availability?${qs.toString()}`);
            if (!data.items.length) {
                tbody.innerHTML = '<tr><td colspan="9"><div class="empty-hint">暂无符合条件的可用充电枪</div></td></tr>';
                pager.innerHTML = "";
                return;
            }
            const canOrder = state.user && (state.user.role === "driver" || state.user.role === "fleet");
            tbody.innerHTML = data.items
                .map((g) => {
                    const isIdle = g.gun_status === "IDLE";
                    const orderBtnLabel = isIdle ? "扫码开单" : "测并发抢单";
                    const orderBtnCls = isIdle ? "btn-primary" : "btn-outline";
                    return `
                        <tr>
                            <td data-label="枪 ID">${escapeHtml(g.gun_id)}</td>
                            <td data-label="枪编号"><code>${escapeHtml(g.gun_code)}</code></td>
                            <td data-label="所属场站">#${escapeHtml(g.station_id)} ${escapeHtml(g.station_name)}</td>
                            <td data-label="所属桩 / 额定功率"><code>${escapeHtml(g.pile_code)}</code> (${escapeHtml(g.rated_power)}kW)</td>
                            <td data-label="枪型">${escapeHtml(g.gun_type)}</td>
                            <td data-label="接口标准">${escapeHtml(g.connector_standard)}</td>
                            <td data-label="最大功率 (kW)" class="num">${escapeHtml(g.max_power)}</td>
                            <td data-label="当前状态">${statusBadge(g.gun_status)}</td>
                            <td data-label="操作" class="actions-cell">
                                <button
                                    class="btn ${orderBtnCls} btn-xs start-gun-order-btn"
                                    data-station-id="${escapeHtml(g.station_id)}"
                                    data-station-name="${escapeHtml(g.station_name)}"
                                    data-gun-id="${escapeHtml(g.gun_id)}"
                                    data-gun-code="${escapeHtml(g.gun_code)}"
                                    type="button"
                                    ${canOrder ? "" : 'title="仅 driver / fleet 角色可执行扫码开单"'}
                                >${orderBtnLabel}</button>
                            </td>
                        </tr>`;
                })
                .join("");

            renderPagination(pager, data, (nextPage) => loadDriverGuns(nextPage));
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="9"><div class="empty-hint">查询失败 (${escapeHtml(err.status || "-")})：${escapeHtml(err.message)}</div></td></tr>`;
            pager.innerHTML = "";
        }
    }

    function openStartOrderModal({ stationId = 1, gunId = 1, stationName = "", gunCode = "" } = {}) {
        const dlg = $("startOrderDialog");
        setInlineAlert($("startOrderError"), "");
        $("soStationId").value = stationId;
        $("soGunId").value = gunId;
        $("soVehicleId").value = "";
        $("soStartSoc").value = "25";
        const targetSummary = gunCode
            ? `场站 #${stationId} ${stationName} · 充电枪 #${gunId}（${gunCode}）`
            : `手动指定场站 #${stationId} · 充电枪 #${gunId}（同枪若已有 CHARGING 订单将触发 1062 -> HTTP 409）`;
        $("startOrderTargetInfo").textContent = targetSummary;
        dlg.showModal();
    }

    // ------------------------------------------------------------------------
    // Tab 2: 订单与分时计费
    // ------------------------------------------------------------------------
    async function loadOrders(page = state.pages.orders) {
        state.pages.orders = page;
        const tbody = $("ordersBody");
        const pager = $("ordersPager");
        const scopeDesc = $("orderScopeDesc");
        if (state.user) {
            const isPersonal = state.user.role === "driver" || state.user.role === "fleet";
            scopeDesc.innerHTML = isPersonal
                ? `数据源：视图 <code>v_order_owner</code> · 当前仅显示 <code>uid=${escapeHtml(state.user.user_id)}</code> 的本人订单`
                : `数据源：视图 <code>v_order_owner</code> · 当前角色可查看全平台全部订单`;
        }

        const qs = new URLSearchParams({
            page: String(page),
            page_size: String(state.pageSize.orders),
        });

        try {
            const { data } = await apiFetch(`/api/orders?${qs.toString()}`);
            if (!data.items.length) {
                tbody.innerHTML = '<tr><td colspan="10"><div class="empty-hint">当前视角下暂无充电订单（可在“找站与开单”扫码开单，或将联调 user_id 设为 9001 查看种子订单）。</div></td></tr>';
                pager.innerHTML = "";
                return;
            }
            const role = state.user?.role;
            const canFinishRole = role === "driver" || role === "ops";
            const canPayRole = role === "driver" || role === "fleet";

            tbody.innerHTML = data.items
                .map((o) => {
                    const isCharging = o.status === "CHARGING";
                    const canPay = o.status === "FINISHED" || o.status === "PAID";
                    return `
                        <tr>
                            <td data-label="订单 ID">${escapeHtml(o.order_id)}</td>
                            <td data-label="订单编号"><code>${escapeHtml(o.order_no)}</code></td>
                            <td data-label="车主 UID">${escapeHtml(o.user_id)}</td>
                            <td data-label="场站 / 枪">站#${escapeHtml(o.station_id)} / 枪#${escapeHtml(o.gun_id)}</td>
                            <td data-label="起止时间">${escapeHtml(formatTime(o.start_time))}<br><span class="meta">${escapeHtml(formatTime(o.end_time))}</span></td>
                            <td data-label="电量 (kWh)" class="num">${escapeHtml(o.kwh)}</td>
                            <td data-label="应付 (¥)" class="num"><strong>¥${escapeHtml(o.total_amount)}</strong></td>
                            <td data-label="已付 / 待付 (¥)" class="num">¥${escapeHtml(o.paid_amount)} / <span class="${Number(o.unpaid_amount) > 0 ? "badge badge-warn" : "meta"}">¥${escapeHtml(o.unpaid_amount)}</span></td>
                            <td data-label="状态">${statusBadge(o.status, o.status_text || o.status)}</td>
                            <td data-label="操作" class="actions-cell">
                                <div class="row-actions">
                                    <button class="btn btn-outline btn-xs view-order-detail-btn" data-order-id="${escapeHtml(o.order_id)}" type="button">分时明细</button>
                                    ${
                                        isCharging && canFinishRole
                                            ? `<button class="btn btn-primary btn-xs finish-order-btn" data-order-id="${escapeHtml(o.order_id)}" data-order-no="${escapeHtml(o.order_no)}" type="button">结束计费</button>`
                                            : ""
                                    }
                                    ${
                                        canPay && canPayRole
                                            ? `<button class="btn btn-outline btn-xs pay-order-btn" data-order-id="${escapeHtml(o.order_id)}" data-order-no="${escapeHtml(o.order_no)}" data-unpaid="${escapeHtml(o.unpaid_amount)}" data-total="${escapeHtml(o.total_amount)}" type="button">支付/退款</button>`
                                            : ""
                                    }
                                </div>
                            </td>
                        </tr>`;
                })
                .join("");

            renderPagination(pager, data, (nextPage) => loadOrders(nextPage));
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="10"><div class="empty-hint">加载订单失败 (${escapeHtml(err.status || "-")})：${escapeHtml(err.message)}</div></td></tr>`;
            pager.innerHTML = "";
        }
    }

    async function showOrderDetailModal(orderId) {
        const dlg = $("detailDialog");
        $("detailDialogTitle").textContent = `订单 #${orderId} 分时计费明细`;
        $("detailDialogBody").innerHTML = '<div class="empty-hint">正在加载订单详情...</div>';
        dlg.showModal();

        try {
            const { data } = await apiFetch(`/api/orders/${orderId}`);
            const detailsHtml = data.details?.length
                ? `
                    <div class="table-wrap" tabindex="0" role="region" aria-label="分时计费段明细">
                        <table>
                            <caption class="visually-hidden">订单分时费用明细表</caption>
                            <thead>
                                <tr>
                                    <th scope="col">时段</th>
                                    <th scope="col">起始时间</th>
                                    <th scope="col">结束时间</th>
                                    <th scope="col" class="num">电量 (kWh)</th>
                                    <th scope="col" class="num">电价</th>
                                    <th scope="col" class="num">服务费单价</th>
                                    <th scope="col" class="num">电费 (¥)</th>
                                    <th scope="col" class="num">服务费 (¥)</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${data.details
                                    .map(
                                        (d) => `
                                        <tr>
                                            <td data-label="时段">${statusBadge(d.period, d.period)}</td>
                                            <td data-label="起始时间">${escapeHtml(formatTime(d.begin_time))}</td>
                                            <td data-label="结束时间">${escapeHtml(formatTime(d.end_time))}</td>
                                            <td data-label="电量 (kWh)" class="num">${escapeHtml(d.kwh)}</td>
                                            <td data-label="电价" class="num">${escapeHtml(d.energy_price)}</td>
                                            <td data-label="服务费单价" class="num">${escapeHtml(d.service_price)}</td>
                                            <td data-label="电费 (¥)" class="num">¥${escapeHtml(d.energy_fee)}</td>
                                            <td data-label="服务费 (¥)" class="num">¥${escapeHtml(d.service_fee)}</td>
                                        </tr>`
                                    )
                                    .join("")}
                            </tbody>
                        </table>
                    </div>`
                : '<div class="empty-hint">该订单暂无分时费用明细（充电结束后由存储过程 sp_order_calc 生成）。</div>';

            $("detailDialogBody").innerHTML = `
                <div class="detail-kv-grid">
                    <div class="detail-kv"><span>订单编号</span><strong><code>${escapeHtml(data.order_no)}</code></strong></div>
                    <div class="detail-kv"><span>订单状态</span><strong>${statusBadge(data.status)}</strong></div>
                    <div class="detail-kv"><span>总电量</span><strong>${escapeHtml(data.kwh)} kWh</strong></div>
                    <div class="detail-kv"><span>电费 / 服务费</span><strong>¥${escapeHtml(data.energy_fee)} / ¥${escapeHtml(data.service_fee)}</strong></div>
                    <div class="detail-kv"><span>优惠减免</span><strong>¥${escapeHtml(data.discount_amount)}</strong></div>
                    <div class="detail-kv"><span>应付 / 已付</span><strong>¥${escapeHtml(data.total_amount)} / ¥${escapeHtml(data.paid_amount)}</strong></div>
                </div>
                ${detailsHtml}
            `;
        } catch (err) {
            $("detailDialogBody").innerHTML = `<div class="inline-alert">加载失败 (${escapeHtml(err.status || "-")})：${escapeHtml(err.message)}</div>`;
        }
    }

    async function loadPeriodStats() {
        const box = $("periodStatBox");
        const startDate = $("statStartDate").value;
        const endDate = $("statEndDate").value;
        if (!startDate || !endDate) return;

        try {
            const qs = new URLSearchParams({ start_date: startDate, end_date: endDate });
            const { data } = await apiFetch(`/api/orders/stats/period?${qs.toString()}`);
            if (!data.length) {
                box.innerHTML = '<div class="empty-hint">所选日期区间内暂无分时计费数据。</div>';
                return;
            }
            box.innerHTML = `
                <table>
                    <caption class="visually-hidden">峰平谷分时电价与电量统计表</caption>
                    <thead>
                        <tr>
                            <th scope="col">统计日期</th>
                            <th scope="col">时段</th>
                            <th scope="col" class="num">订单数</th>
                            <th scope="col" class="num">总电量 (kWh)</th>
                            <th scope="col" class="num">电费 (¥)</th>
                            <th scope="col" class="num">服务费 (¥)</th>
                            <th scope="col" class="num">综合均价 (¥/kWh)</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${data
                            .map(
                                (r) => `
                                <tr>
                                    <td data-label="统计日期">${escapeHtml(r.stat_date)}</td>
                                    <td data-label="时段">${escapeHtml(r.period)}</td>
                                    <td data-label="订单数" class="num">${escapeHtml(r.order_cnt)}</td>
                                    <td data-label="总电量 (kWh)" class="num">${escapeHtml(r.total_kwh)}</td>
                                    <td data-label="电费 (¥)" class="num">¥${escapeHtml(r.energy_fee)}</td>
                                    <td data-label="服务费 (¥)" class="num">¥${escapeHtml(r.service_fee)}</td>
                                    <td data-label="综合均价" class="num">¥${escapeHtml(r.avg_price)}</td>
                                </tr>`
                            )
                            .join("")}
                    </tbody>
                </table>
            `;
        } catch (err) {
            if (err.status === 500) {
                box.innerHTML = `<div class="empty-hint">后端 <code>GET /api/orders/stats/period</code> 返回 HTTP 500（已知线 2 <code>PeriodStatOut.stat_date</code> 类型定义与数据库 <code>DATE</code> 列待对齐），已安全隔离不影响其他功能。</div>`;
            } else {
                box.innerHTML = `<div class="empty-hint">查询失败 (${escapeHtml(err.status || "-")})：${escapeHtml(err.message)}</div>`;
            }
        }
    }

    // ------------------------------------------------------------------------
    // Tab 3: 运维排障与巡检
    // ------------------------------------------------------------------------
    async function loadFaults(page = state.pages.faults) {
        state.pages.faults = page;
        const tbody = $("faultsBody");
        const pager = $("faultsPager");
        const qs = new URLSearchParams({
            page: String(page),
            page_size: String(state.pageSize.faults),
        });
        const stId = $("faultStationFilter").value.trim();
        const ps = $("faultPileStatusFilter").value;
        if (stId) qs.set("station_id", stId);
        if (ps) qs.set("pile_status", ps);

        try {
            const { data } = await apiFetch(`/api/stations/faults?${qs.toString()}`);
            if (!data.items.length) {
                tbody.innerHTML = '<tr><td colspan="8"><div class="empty-hint">当前筛选条件下无故障或离线桩枪记录。</div></td></tr>';
                pager.innerHTML = "";
                return;
            }
            tbody.innerHTML = data.items
                .map(
                    (r) => `
                    <tr>
                        <td data-label="场站">#${escapeHtml(r.station_id)} ${escapeHtml(r.station_name)}</td>
                        <td data-label="桩编号 / 型号">#${escapeHtml(r.pile_id)} <code>${escapeHtml(r.pile_code)}</code><br><span class="meta">${escapeHtml(r.device_model)}</span></td>
                        <td data-label="桩状态">${statusBadge(r.pile_status)}</td>
                        <td data-label="枪编号">${r.gun_code ? `#${escapeHtml(r.gun_id)} <code>${escapeHtml(r.gun_code)}</code>` : "-"}</td>
                        <td data-label="枪状态">${r.gun_status ? statusBadge(r.gun_status) : "-"}</td>
                        <td data-label="心跳滞后 (s)" class="num">${r.heartbeat_lag_sec ?? "-"}</td>
                        <td data-label="脱敏热线">${escapeHtml(r.service_phone_masked || "-")}</td>
                        <td data-label="排障处置" class="actions-cell">
                            <div class="row-actions">
                                <button class="btn btn-outline btn-xs recover-pile-btn" data-pile-id="${escapeHtml(r.pile_id)}" type="button">桩置空闲</button>
                                ${
                                    r.gun_id
                                        ? `<button class="btn btn-outline btn-xs recover-gun-btn" data-gun-id="${escapeHtml(r.gun_id)}" type="button">枪置空闲</button>`
                                        : ""
                                }
                                <button class="btn btn-ghost btn-xs custom-pile-status-btn" data-pile-id="${escapeHtml(r.pile_id)}" data-cur="${escapeHtml(r.pile_status)}" type="button">改状态</button>
                            </div>
                        </td>
                    </tr>`
                )
                .join("");

            renderPagination(pager, data, (nextPage) => loadFaults(nextPage));
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="8"><div class="empty-hint">无权限或加载失败 (${escapeHtml(err.status || "-")})：${escapeHtml(err.message)}</div></td></tr>`;
            pager.innerHTML = "";
        }
    }

    async function loadStatusLogs(page = state.pages.logs) {
        state.pages.logs = page;
        const tbody = $("statusLogsBody");
        const pager = $("logsPager");
        const qs = new URLSearchParams({
            page: String(page),
            page_size: String(state.pageSize.logs),
        });
        const stId = $("logStationFilter").value.trim();
        const dt = $("logDeviceTypeFilter").value;
        const ts = $("logTodoStatusFilter").value;
        if (stId) qs.set("station_id", stId);
        if (dt) qs.set("device_type", dt);
        if (ts) qs.set("todo_status", ts);

        try {
            const { data } = await apiFetch(`/api/stations/status-logs?${qs.toString()}`);
            if (!data.items.length) {
                tbody.innerHTML = '<tr><td colspan="9"><div class="empty-hint">暂无符合条件的状态变更日志。</div></td></tr>';
                pager.innerHTML = "";
                return;
            }
            tbody.innerHTML = data.items
                .map(
                    (l) => `
                    <tr>
                        <td data-label="日志 ID">${escapeHtml(l.log_id)}</td>
                        <td data-label="场站">#${escapeHtml(l.station_id)} ${escapeHtml(l.station_name)}</td>
                        <td data-label="设备">${escapeHtml(l.device_type)} · <code>${escapeHtml(l.gun_code || l.pile_code)}</code></td>
                        <td data-label="状态变迁">${escapeHtml(l.prev_status || "NULL")} → ${statusBadge(l.new_status)}</td>
                        <td data-label="来源">${escapeHtml(l.change_source)}</td>
                        <td data-label="待办">${statusBadge(l.todo_status)}</td>
                        <td data-label="备注说明">${escapeHtml(l.remark || "-")}</td>
                        <td data-label="变更时间">${escapeHtml(formatTime(l.changed_at))}</td>
                        <td data-label="操作" class="actions-cell">
                            ${
                                l.todo_status === "PENDING"
                                    ? `<button class="btn btn-primary btn-xs resolve-todo-btn" data-log-id="${escapeHtml(l.log_id)}" data-device="${escapeHtml(l.gun_code || l.pile_code)}" type="button">办结待办</button>`
                                    : "-"
                            }
                        </td>
                    </tr>`
                )
                .join("");

            renderPagination(pager, data, (nextPage) => loadStatusLogs(nextPage));
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="9"><div class="empty-hint">无权限或加载失败 (${escapeHtml(err.status || "-")})：${escapeHtml(err.message)}</div></td></tr>`;
            pager.innerHTML = "";
        }
    }

    async function runHeartbeatCheck() {
        try {
            const { data } = await apiFetch("/api/stations/piles/heartbeat-check", {
                method: "POST",
                body: JSON.stringify({ check_time: null }),
            });
            showToast(`${data.message}：标记离线桩 ${data.affected_piles} 台、枪 ${data.affected_guns} 把`);
            loadFaults(1);
            loadStatusLogs(1);
        } catch (err) {
            showToast(`巡检失败 (${err.status || "-"})：${err.message}`, true);
        }
    }

    async function reportSingleHeartbeat() {
        const pileId = Number($("opsPileId").value || 0);
        if (!pileId) return;
        try {
            const { data } = await apiFetch(`/api/stations/piles/${pileId}/heartbeat`, { method: "POST" });
            showToast(`桩 #${data.pile_id} (${data.pile_code}) 心跳已刷新`);
            loadFaults();
        } catch (err) {
            showToast(`上报失败 (${err.status || "-"})：${err.message}`, true);
        }
    }

    // ------------------------------------------------------------------------
    // Tab 4: 资产看板与三级建档
    // ------------------------------------------------------------------------
    async function loadRealtimeBoard(page = state.pages.realtime) {
        state.pages.realtime = page;
        const kpis = $("realtimeKpis");
        const tbody = $("realtimeBody");
        const pager = $("realtimePager");

        const qs = new URLSearchParams({
            page: String(page),
            page_size: String(state.pageSize.realtime),
        });
        const opId = $("realtimeOperatorFilter").value.trim();
        const stStatus = $("realtimeStatusFilter").value;
        if (opId) qs.set("operator_id", opId);
        if (stStatus) qs.set("station_status", stStatus);

        try {
            const { data } = await apiFetch(`/api/stations/realtime-status?${qs.toString()}`);
            let totalPiles = 0;
            let pendingPiles = 0;
            let abnormalPiles = 0;
            const powerValues = [];
            state.stationCache.clear();

            for (const row of data.items) {
                state.stationCache.set(String(row.station_id), row);
                totalPiles += Number(row.total_piles || 0);
                pendingPiles += Number(row.pending_acceptance_piles || 0);
                abnormalPiles += Number(row.fault_piles || 0) + Number(row.offline_piles || 0);
                powerValues.push(row.total_rated_power);
            }

            const pagePower = sumDecimalStrings(powerValues, 2);
            kpis.innerHTML = `
                <div class="kpi-box"><div class="kpi-label">符合条件场站数</div><div class="kpi-value">${escapeHtml(data.total)}</div></div>
                <div class="kpi-box"><div class="kpi-label">当前页装机桩</div><div class="kpi-value">${escapeHtml(totalPiles)}</div></div>
                <div class="kpi-box"><div class="kpi-label">当前页待验收桩</div><div class="kpi-value">${escapeHtml(pendingPiles)}</div></div>
                <div class="kpi-box"><div class="kpi-label">当前页故障 / 离线桩</div><div class="kpi-value ${abnormalPiles > 0 ? "kpi-danger" : ""}">${escapeHtml(abnormalPiles)}</div></div>
                <div class="kpi-box"><div class="kpi-label">当前页装机功率 (kW)</div><div class="kpi-value kpi-teal">${escapeHtml(pagePower)}</div></div>
            `;

            if (!data.items.length) {
                tbody.innerHTML = '<tr><td colspan="8"><div class="empty-hint">暂无符合条件的场站实时数据。</div></td></tr>';
                pager.innerHTML = "";
                return;
            }

            const isAdmin = state.user?.role === "admin";
            tbody.innerHTML = data.items
                .map((s) => {
                    const pileAbnormal = Number(s.fault_piles) + Number(s.offline_piles);
                    const gunAbnormal = Number(s.fault_guns) + Number(s.offline_guns);
                    return `
                        <tr>
                            <td data-label="场站 ID">${escapeHtml(s.station_id)}</td>
                            <td data-label="场站名称"><strong>${escapeHtml(s.station_name)}</strong><br><span class="meta">${escapeHtml(s.station_code)}</span></td>
                            <td data-label="运营商">${escapeHtml(s.operator_name)}</td>
                            <td data-label="营业状态">${statusBadge(s.station_status)}</td>
                            <td data-label="充电桩分布">
                                <div class="mini-pills">
                                    <span class="mini-pill">总 ${s.total_piles}</span>
                                    ${s.pending_acceptance_piles > 0 ? `<span class="mini-pill pill-warn">验 ${s.pending_acceptance_piles}</span>` : ""}
                                    <span class="mini-pill pill-ok">闲 ${s.idle_piles}</span>
                                    <span class="mini-pill pill-blue">忙 ${s.occupied_piles}</span>
                                    ${pileAbnormal > 0 ? `<span class="mini-pill pill-danger">异 ${pileAbnormal}</span>` : '<span class="mini-pill">异 0</span>'}
                                </div>
                            </td>
                            <td data-label="装机功率 (kW)" class="num"><strong>${escapeHtml(s.total_rated_power)}</strong></td>
                            <td data-label="充电枪分布">
                                <div class="mini-pills">
                                    <span class="mini-pill">总 ${s.total_guns}</span>
                                    <span class="mini-pill pill-ok">闲 ${s.idle_guns}</span>
                                    <span class="mini-pill pill-blue">忙 ${s.occupied_guns}</span>
                                    ${gunAbnormal > 0 ? `<span class="mini-pill pill-danger">异 ${gunAbnormal}</span>` : '<span class="mini-pill">异 0</span>'}
                                </div>
                            </td>
                            <td data-label="操作" class="actions-cell">
                                <div class="row-actions">
                                    <button class="btn btn-outline btn-xs drill-station-btn" data-station-id="${escapeHtml(s.station_id)}" type="button">钻取桩</button>
                                    ${
                                        isAdmin
                                            ? `<button class="btn btn-outline btn-xs edit-station-btn" data-station-id="${escapeHtml(s.station_id)}" type="button">编辑</button>`
                                            : ""
                                    }
                                </div>
                            </td>
                        </tr>`;
                })
                .join("");

            renderPagination(pager, data, (nextPage) => loadRealtimeBoard(nextPage));
        } catch (err) {
            kpis.innerHTML = "";
            tbody.innerHTML = `<tr><td colspan="8"><div class="empty-hint">无权限或加载失败 (${escapeHtml(err.status || "-")})：${escapeHtml(err.message)}</div></td></tr>`;
            pager.innerHTML = "";
        }
    }

    async function loadOperators(summaryOnly = state.operatorsMode === "summary") {
        state.operatorsMode = summaryOnly ? "summary" : "full";
        const tbody = $("operatorsBody");
        const endpoint = summaryOnly ? "/api/stations/operators/summary" : "/api/stations/operators";
        try {
            const { data } = await apiFetch(endpoint);
            state.operatorCache.clear();
            const isAdmin = state.user?.role === "admin";
            tbody.innerHTML = data
                .map((op) => {
                    state.operatorCache.set(String(op.operator_id), op);
                    return `
                        <tr>
                            <td data-label="ID">${escapeHtml(op.operator_id)}</td>
                            <td data-label="编码"><code>${escapeHtml(op.operator_code)}</code></td>
                            <td data-label="名称">${escapeHtml(op.operator_name)}</td>
                            <td data-label="状态">${statusBadge(op.status)}</td>
                            <td data-label="客服热线">${escapeHtml(op.service_phone_masked || op.service_phone || "-")}</td>
                            <td data-label="信用代码"><code>${escapeHtml(op.credit_code || "已脱敏隐藏")}</code></td>
                            <td data-label="操作" class="actions-cell">
                                ${
                                    isAdmin && !summaryOnly
                                        ? `<button class="btn btn-outline btn-xs edit-operator-btn" data-operator-id="${escapeHtml(op.operator_id)}" type="button">编辑</button>`
                                        : "-"
                                }
                            </td>
                        </tr>`;
                })
                .join("");
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="7"><div class="empty-hint">无权限或加载失败 (${escapeHtml(err.status || "-")})：${escapeHtml(err.message)}</div></td></tr>`;
        }
    }

    async function drillPiles(stationId) {
        const box = $("drillResultBox");
        $("drillStationId").value = stationId;
        try {
            const { data } = await apiFetch(`/api/stations/${stationId}/piles`);
            if (!data.length) {
                box.className = "empty-hint";
                box.textContent = `场站 #${stationId} 暂无充电桩。`;
                return;
            }
            box.className = "table-wrap table-scroll-sm";
            box.innerHTML = `
                <table>
                    <caption class="visually-hidden">场站下属充电桩列表</caption>
                    <thead>
                        <tr>
                            <th scope="col">桩 ID</th>
                            <th scope="col">编码</th>
                            <th scope="col">型号</th>
                            <th scope="col" class="num">额定功率</th>
                            <th scope="col">验收</th>
                            <th scope="col">状态</th>
                            <th scope="col">操作</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${data
                            .map(
                                (p) => `
                                <tr>
                                    <td data-label="桩 ID">${escapeHtml(p.pile_id)}</td>
                                    <td data-label="编码"><code>${escapeHtml(p.pile_code)}</code></td>
                                    <td data-label="型号">${escapeHtml(p.device_model)}</td>
                                    <td data-label="额定功率" class="num">${escapeHtml(p.rated_power)}kW</td>
                                    <td data-label="验收">${statusBadge(p.acceptance_status)}</td>
                                    <td data-label="状态">${statusBadge(p.current_status)}</td>
                                    <td data-label="操作" class="actions-cell">
                                        <div class="row-actions">
                                            <button class="btn btn-outline btn-xs drill-pile-btn" data-pile-id="${escapeHtml(p.pile_id)}" type="button">查枪</button>
                                            <button class="btn btn-ghost btn-xs drill-pile-status-btn" data-pile-id="${escapeHtml(p.pile_id)}" data-cur="${escapeHtml(p.current_status)}" type="button">改状态</button>
                                            ${
                                                p.acceptance_status !== "PASSED"
                                                    ? `<button class="btn btn-primary btn-xs drill-commission-btn" data-pile-id="${escapeHtml(p.pile_id)}" type="button">验收</button>`
                                                    : ""
                                            }
                                        </div>
                                    </td>
                                </tr>`
                            )
                            .join("")}
                    </tbody>
                </table>
            `;
        } catch (err) {
            box.className = "empty-hint";
            box.textContent = `查桩失败 (${err.status || "-"})：${err.message}`;
        }
    }

    async function drillGuns(pileId) {
        const box = $("drillResultBox");
        $("drillPileId").value = pileId;
        try {
            const { data } = await apiFetch(`/api/stations/piles/${pileId}/guns`);
            if (!data.length) {
                box.className = "empty-hint";
                box.textContent = `充电桩 #${pileId} 暂未配置充电枪。`;
                return;
            }
            box.className = "table-wrap table-scroll-sm";
            box.innerHTML = `
                <table>
                    <caption class="visually-hidden">充电桩下属充电枪列表</caption>
                    <thead>
                        <tr>
                            <th scope="col">枪 ID</th>
                            <th scope="col">编码</th>
                            <th scope="col">序号</th>
                            <th scope="col">枪型</th>
                            <th scope="col">标准</th>
                            <th scope="col" class="num">最大功率</th>
                            <th scope="col">状态</th>
                            <th scope="col">操作</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${data
                            .map(
                                (g) => `
                                <tr>
                                    <td data-label="枪 ID">${escapeHtml(g.gun_id)}</td>
                                    <td data-label="编码"><code>${escapeHtml(g.gun_code)}</code></td>
                                    <td data-label="序号">${escapeHtml(g.gun_seq)}</td>
                                    <td data-label="枪型">${escapeHtml(g.gun_type)}</td>
                                    <td data-label="标准">${escapeHtml(g.connector_standard)}</td>
                                    <td data-label="最大功率" class="num">${escapeHtml(g.max_power)}kW</td>
                                    <td data-label="状态">${statusBadge(g.current_status)}</td>
                                    <td data-label="操作" class="actions-cell">
                                        <button class="btn btn-ghost btn-xs drill-gun-status-btn" data-gun-id="${escapeHtml(g.gun_id)}" data-cur="${escapeHtml(g.current_status)}" type="button">改状态</button>
                                    </td>
                                </tr>`
                            )
                            .join("")}
                    </tbody>
                </table>
            `;
        } catch (err) {
            box.className = "empty-hint";
            box.textContent = `查枪失败 (${err.status || "-"})：${err.message}`;
        }
    }

    // ------------------------------------------------------------------------
    // Tab 5: 结算对账与开票
    // ------------------------------------------------------------------------
    async function loadBills(page = state.pages.bills) {
        state.pages.bills = page;
        const tbody = $("billsBody");
        const pager = $("billsPager");
        const qs = new URLSearchParams({
            page: String(page),
            page_size: String(state.pageSize.bills),
        });
        const opId = $("billOperatorFilter").value.trim();
        if (opId) qs.set("operator_id", opId);

        try {
            const { data } = await apiFetch(`/api/settlements?${qs.toString()}`);
            if (!data.items.length) {
                tbody.innerHTML = '<tr><td colspan="12"><div class="empty-hint">暂无结算单数据。</div></td></tr>';
                pager.innerHTML = "";
                return;
            }
            tbody.innerHTML = data.items
                .map((b) => {
                    const invPct = pctRatio(b.invoiced_amount, b.payable_amount);
                    return `
                        <tr>
                            <td data-label="账单 ID">${escapeHtml(b.bill_id)}</td>
                            <td data-label="运营商 / 场站">运营商#${escapeHtml(b.operator_id)} / ${b.station_id ? `站#${escapeHtml(b.station_id)}` : "汇总"}</td>
                            <td data-label="账期"><code>${escapeHtml(b.bill_month)}</code></td>
                            <td data-label="订单数" class="num">${escapeHtml(b.order_count)}</td>
                            <td data-label="总电量 (kWh)" class="num">${escapeHtml(b.total_kwh)}</td>
                            <td data-label="订单总额 (¥)" class="num">¥${escapeHtml(b.total_amount)}</td>
                            <td data-label="平台抽成 (¥)" class="num">¥${escapeHtml(b.platform_fee)}</td>
                            <td data-label="应付运营商 (¥)" class="num"><strong>¥${escapeHtml(b.payable_amount)}</strong></td>
                            <td data-label="开票进度">
                                <div class="meter-row">
                                    <div class="meter-label"><span>已开 ¥${escapeHtml(b.invoiced_amount)}</span><span>余 ¥${escapeHtml(b.uninvoiced_amount)}</span></div>
                                    <div class="meter-track"><div class="meter-fill" style="--pct:${invPct}%"></div></div>
                                </div>
                            </td>
                            <td data-label="未处理差异" class="num">${Number(b.open_diff_cnt) > 0 ? `<span class="badge badge-danger">${escapeHtml(b.open_diff_cnt)} 笔</span>` : '<span class="meta">0</span>'}</td>
                            <td data-label="状态">${statusBadge(b.status)}</td>
                            <td data-label="操作" class="actions-cell">
                                <div class="row-actions">
                                    <button class="btn btn-outline btn-xs view-bill-detail-btn" data-bill-id="${escapeHtml(b.bill_id)}" type="button">详情</button>
                                    <button class="btn btn-primary btn-xs issue-invoice-btn" data-bill-id="${escapeHtml(b.bill_id)}" data-uninvoiced="${escapeHtml(b.uninvoiced_amount)}" data-payable="${escapeHtml(b.payable_amount)}" type="button">开票</button>
                                </div>
                            </td>
                        </tr>`;
                })
                .join("");

            renderPagination(pager, data, (nextPage) => loadBills(nextPage));
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="12"><div class="empty-hint">无权限或加载失败 (${escapeHtml(err.status || "-")})：${escapeHtml(err.message)}</div></td></tr>`;
            pager.innerHTML = "";
        }
    }

    async function loadDiffs(page = state.pages.diffs) {
        state.pages.diffs = page;
        const tbody = $("diffsBody");
        const pager = $("diffsPager");
        const qs = new URLSearchParams({
            page: String(page),
            page_size: String(state.pageSize.diffs),
        });

        try {
            const { data } = await apiFetch(`/api/settlements/diffs?${qs.toString()}`);
            if (!data.items.length) {
                tbody.innerHTML = '<tr><td colspan="7"><div class="empty-hint">当前无待处理对账差异。</div></td></tr>';
                pager.innerHTML = "";
                return;
            }
            tbody.innerHTML = data.items
                .map(
                    (d) => `
                    <tr>
                        <td data-label="差异 ID">${escapeHtml(d.diff_id)}</td>
                        <td data-label="账单 / 账期">账单#${escapeHtml(d.bill_id)} (${escapeHtml(d.bill_month)})</td>
                        <td data-label="关联订单">${d.order_id ? `订单#${escapeHtml(d.order_id)}` : "-"}</td>
                        <td data-label="类型"><code>${escapeHtml(d.diff_type)}</code></td>
                        <td data-label="差额 (¥)" class="num"><strong>¥${escapeHtml(d.diff_amount)}</strong><br><span class="meta">应${escapeHtml(d.expect_amount)} / 实${escapeHtml(d.actual_amount)}</span></td>
                        <td data-label="说明">${escapeHtml(d.remark || "-")}</td>
                        <td data-label="操作" class="actions-cell">
                            <button class="btn btn-primary btn-xs resolve-diff-btn" data-diff-id="${escapeHtml(d.diff_id)}" type="button">核销</button>
                        </td>
                    </tr>`
                )
                .join("");

            renderPagination(pager, data, (nextPage) => loadDiffs(nextPage));
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="7"><div class="empty-hint">无权限或加载失败 (${escapeHtml(err.status || "-")})：${escapeHtml(err.message)}</div></td></tr>`;
            pager.innerHTML = "";
        }
    }

    async function showBillDetailModal(billId) {
        const dlg = $("detailDialog");
        $("detailDialogTitle").textContent = `结算单 #${billId} 汇总详情`;
        $("detailDialogBody").innerHTML = '<div class="empty-hint">正在加载账单详情...</div>';
        dlg.showModal();

        try {
            const { data } = await apiFetch(`/api/settlements/${billId}`);
            $("detailDialogBody").innerHTML = `
                <div class="detail-kv-grid">
                    <div class="detail-kv"><span>账单 ID / 账期</span><strong>#${escapeHtml(data.bill_id)} · ${escapeHtml(data.bill_month)}</strong></div>
                    <div class="detail-kv"><span>运营商 / 场站</span><strong>OP#${escapeHtml(data.operator_id)} / ${data.station_id ? `ST#${escapeHtml(data.station_id)}` : "全部"}</strong></div>
                    <div class="detail-kv"><span>状态</span><strong>${statusBadge(data.status)}</strong></div>
                    <div class="detail-kv"><span>订单数 / 总电量</span><strong>${escapeHtml(data.order_count)} 单 · ${escapeHtml(data.total_kwh)} kWh</strong></div>
                    <div class="detail-kv"><span>订单总额</span><strong>¥${escapeHtml(data.total_amount)}</strong></div>
                    <div class="detail-kv"><span>平台抽成</span><strong>¥${escapeHtml(data.platform_fee)}</strong></div>
                    <div class="detail-kv"><span>应付运营商</span><strong>¥${escapeHtml(data.payable_amount)}</strong></div>
                    <div class="detail-kv"><span>已开票 / 未开票</span><strong>¥${escapeHtml(data.invoiced_amount)} / ¥${escapeHtml(data.uninvoiced_amount)}</strong></div>
                    <div class="detail-kv"><span>未处理差异笔数</span><strong>${escapeHtml(data.open_diff_cnt)}</strong></div>
                </div>
            `;
        } catch (err) {
            $("detailDialogBody").innerHTML = `<div class="inline-alert">加载失败 (${escapeHtml(err.status || "-")})：${escapeHtml(err.message)}</div>`;
        }
    }

    function renderSessionInvoices() {
        const list = $("sessionInvoicesList");
        if (!state.sessionInvoices.length) {
            list.innerHTML = '<li class="meta">暂无本会话开票记录（种子发票 ID 为 1 ~ 20）</li>';
            return;
        }
        list.innerHTML = state.sessionInvoices
            .map(
                (inv) =>
                    `<li><button class="btn btn-outline btn-xs pick-invoice-btn" data-invoice-id="${escapeHtml(inv.invoice_id)}" data-amount="${escapeHtml(inv.amount)}" type="button">#${escapeHtml(inv.invoice_id)} · ${escapeHtml(inv.invoice_no)} (¥${escapeHtml(inv.amount)})</button></li>`
            )
            .join("");
    }

    // ------------------------------------------------------------------------
    // Tab 6: RBAC 与约束实验室
    // ------------------------------------------------------------------------
    function logConsole(method, url, status, data, elapsedMs = null) {
        const box = $("apiConsole");
        const codeEl = box.querySelector("code") || box;
        const stamp = new Date().toLocaleTimeString();
        const roleTag = state.user ? `[${state.user.role}]` : "[anon]";
        const timeTag = elapsedMs !== null ? ` (${elapsedMs}ms)` : "";
        const header = `[${stamp}] ${roleTag} ${method} ${url} -> HTTP ${status}${timeTag}`;
        const body = typeof data === "string" ? data : JSON.stringify(data, null, 2);
        const prev = codeEl.textContent === "等待发起探测请求..." ? "" : `\n\n${codeEl.textContent}`;
        codeEl.textContent = `${header}\n${body}${prev}`;
    }

    async function probeEndpoint(method, url, body = null) {
        try {
            const res = await apiFetch(url, {
                method,
                body: body ? JSON.stringify(body) : undefined,
            });
            logConsole(method, url, res.status, res.data, res.elapsedMs);
            showToast(`${method} ${url} -> HTTP ${res.status}`);
        } catch (err) {
            logConsole(method, url, err.status || "ERR", err.data || err.message, err.elapsedMs ?? null);
            showToast(`${method} ${url} -> HTTP ${err.status || "-"}：${err.message}`, true);
        }
    }

    // ------------------------------------------------------------------------
    // 通用参数弹窗引擎（用于验收、改状态、办结待办、编辑场站、新增/编辑运营商、开票、核销差异）
    // ------------------------------------------------------------------------
    let currentActionHandler = null;

    function openActionDialog({ title, desc = "", submitText = "确认提交", fields = [], onSubmit }) {
        const dlg = $("actionDialog");
        $("actionDialogTitle").textContent = title;
        const descEl = $("actionDialogDesc");
        if (desc) {
            descEl.textContent = desc;
            descEl.hidden = false;
        } else {
            descEl.textContent = "";
            descEl.hidden = true;
        }
        setInlineAlert($("actionDialogError"), "");
        $("actionDialogSubmitBtn").textContent = submitText;

        const container = $("actionDialogFields");
        container.innerHTML = fields
            .map((f, idx) => {
                const inputId = `act-field-${f.name}`;
                const autoAttr = idx === 0 ? " autofocus" : "";
                if (f.type === "select") {
                    const opts = f.options
                        .map(
                            (opt) =>
                                `<option value="${escapeHtml(opt.value)}" ${String(opt.value) === String(f.value ?? "") ? "selected" : ""}>${escapeHtml(opt.label)}</option>`
                        )
                        .join("");
                    return `<div class="field"><label for="${inputId}">${escapeHtml(f.label)}</label><select id="${inputId}" name="${escapeHtml(f.name)}"${autoAttr}>${opts}</select></div>`;
                }
                const attrs = [
                    `id="${inputId}"`,
                    `name="${escapeHtml(f.name)}"`,
                    `type="${escapeHtml(f.type || "text")}"`,
                    f.value !== undefined && f.value !== null ? `value="${escapeHtml(f.value)}"` : "",
                    f.placeholder ? `placeholder="${escapeHtml(f.placeholder)}"` : "",
                    f.min !== undefined ? `min="${escapeHtml(f.min)}"` : "",
                    f.max !== undefined ? `max="${escapeHtml(f.max)}"` : "",
                    f.maxlength !== undefined ? `maxlength="${escapeHtml(f.maxlength)}"` : "",
                    f.minlength !== undefined ? `minlength="${escapeHtml(f.minlength)}"` : "",
                    f.inputmode ? `inputmode="${escapeHtml(f.inputmode)}"` : "",
                    f.required ? "required" : "",
                    autoAttr,
                ]
                    .filter(Boolean)
                    .join(" ");
                return `<div class="field"><label for="${inputId}">${escapeHtml(f.label)}</label><input ${attrs}></div>`;
            })
            .join("");

        currentActionHandler = onSubmit;
        dlg.showModal();
    }

    // ------------------------------------------------------------------------
    // Tab 切换与面板数据调度
    // ------------------------------------------------------------------------
    function activatePanel(panelId, shouldFocusTab = false) {
        state.activePanel = panelId;
        document.querySelectorAll('#mainTabs [role="tab"]').forEach((tab) => {
            const selected = tab.dataset.panel === panelId;
            tab.setAttribute("aria-selected", selected ? "true" : "false");
            tab.tabIndex = selected ? 0 : -1;
            if (selected) {
                tab.scrollIntoView({ block: "nearest", inline: "nearest", container: "nearest" });
                if (shouldFocusTab) tab.focus();
            }
        });

        document.querySelectorAll('[role="tabpanel"]').forEach((panel) => {
            const isTarget = panel.id === panelId;
            if (isTarget) {
                panel.removeAttribute("hidden");
                panel.tabIndex = 0;
            } else {
                panel.setAttribute("hidden", "until-found");
                panel.removeAttribute("tabindex");
            }
        });

        renderRoleNotice();
        refreshActivePanelData();
    }

    function refreshActivePanelData() {
        const role = state.user?.role;
        const p = state.activePanel;
        if (p === "panel-driver") {
            if (!role || TAB_ACCESS[p].roles.includes(role)) {
                loadDriverStations();
                loadDriverGuns();
            }
        } else if (p === "panel-orders") {
            loadOrders();
        } else if (p === "panel-ops") {
            if (!role || TAB_ACCESS[p].roles.includes(role)) {
                loadFaults();
                loadStatusLogs();
            }
        } else if (p === "panel-admin") {
            if (role === "admin" || role === "settlement") {
                loadRealtimeBoard();
                loadOperators(role === "settlement");
            } else if (role === "ops") {
                drillPiles(Number($("drillStationId").value || 1));
            }
        } else if (p === "panel-settle") {
            if (!role || TAB_ACCESS[p].roles.includes(role)) {
                loadBills();
                loadDiffs();
            }
        }
    }

    // ------------------------------------------------------------------------
    // 事件绑定
    // ------------------------------------------------------------------------
    function bindEvents() {
        setupDialogs();

        // Scroll-state 不支持时的顶部指挥栏滚动收起降级
        if (!CSS.supports("container-type", "scroll-state")) {
            const bar = document.querySelector(".topbar");
            let lastY = window.scrollY;
            window.addEventListener(
                "scroll",
                () => {
                    const y = Math.max(0, Math.min(window.scrollY, document.documentElement.scrollHeight - window.innerHeight));
                    if (y === lastY) return;
                    bar?.classList.toggle("is-hidden", y > lastY && y > bar.offsetHeight);
                    lastY = y;
                },
                { passive: true }
            );
        }

        // 角色切换（顶部 + 引导条）
        $("roleButtons").addEventListener("click", (e) => {
            const btn = e.target.closest(".role-pill");
            if (btn) switchDevRole(btn.dataset.role);
        });
        $("roleNoticeActions").addEventListener("click", (e) => {
            const btn = e.target.closest(".quick-role-btn");
            if (btn) switchDevRole(btn.dataset.role);
        });

        // 联调参数 Popover
        document.querySelectorAll(".uid-preset-btn").forEach((btn) => {
            btn.addEventListener("click", () => {
                $("devUserId").value = btn.dataset.uid;
            });
        });
        $("devContextForm").addEventListener("submit", async (e) => {
            e.preventDefault();
            const currentRole = state.user?.role || "driver";
            await switchDevRole(currentRole);
            if (supportsPopover) {
                $("identityPopover").hidePopover();
            } else {
                $("identityPopover").classList.remove(":popover-open");
            }
        });

        // 登录 / 注册 Modal
        $("openAuthDialogBtn").addEventListener("click", () => {
            setInlineAlert($("authDialogError"), "");
            $("authDialog").showModal();
        });
        const switchAuthMode = (mode) => {
            const isLogin = mode === "login";
            $("loginForm").hidden = !isLogin;
            $("registerForm").hidden = isLogin;
            $("modeLoginBtn").className = `btn btn-xs ${isLogin ? "btn-primary" : "btn-outline"}`;
            $("modeRegisterBtn").className = `btn btn-xs ${isLogin ? "btn-outline" : "btn-primary"}`;
            $("modeLoginBtn").setAttribute("aria-pressed", isLogin ? "true" : "false");
            $("modeRegisterBtn").setAttribute("aria-pressed", isLogin ? "false" : "true");
            setInlineAlert($("authDialogError"), "");
        };
        $("modeLoginBtn").addEventListener("click", () => switchAuthMode("login"));
        $("modeRegisterBtn").addEventListener("click", () => switchAuthMode("register"));

        const setupPwdToggle = (btnId, inputId, labelShow, labelHide) => {
            $(btnId).addEventListener("click", () => {
                const input = $(inputId);
                const btn = $(btnId);
                const showing = input.type === "text";
                input.type = showing ? "password" : "text";
                btn.textContent = showing ? "显示" : "隐藏";
                btn.setAttribute("aria-pressed", showing ? "false" : "true");
                btn.setAttribute("aria-label", showing ? labelShow : labelHide);
            });
        };
        setupPwdToggle("toggleLoginPwdBtn", "loginPassword", "显示登录密码", "隐藏登录密码");
        setupPwdToggle("toggleRegPwdBtn", "regPassword", "显示注册密码", "隐藏注册密码");

        $("regRole").addEventListener("change", () => {
            const isFleet = $("regRole").value === "fleet";
            $("regFleetWrap").hidden = !isFleet;
            $("regFleetId").required = isFleet;
            if (!isFleet) $("regFleetId").value = "";
        });

        $("loginForm").addEventListener("submit", async (e) => {
            e.preventDefault();
            const submitBtn = $("loginSubmitBtn");
            submitBtn.disabled = true;
            setInlineAlert($("authDialogError"), "");
            try {
                const { data } = await apiFetch("/api/auth/login", {
                    method: "POST",
                    body: JSON.stringify({
                        username: $("loginUsername").value.trim(),
                        password: $("loginPassword").value,
                    }),
                });
                state.token = data.access_token;
                state.user = data.user;
                localStorage.setItem("ev_hub_token", state.token);
                renderUserContext();
                $("authDialog").close();
                showToast(`登录成功：${data.user.username}`);
                refreshActivePanelData();
            } catch (err) {
                const msg =
                    err.status === 500
                        ? `登录失败 (HTTP 500)：当前数据库尚未合并业务线 4 的 user_account 表，请先关闭弹窗并使用顶栏快捷签发角色 Token。`
                        : `登录失败 (${err.status || "-"})：${err.message}`;
                setInlineAlert($("authDialogError"), msg);
            } finally {
                submitBtn.disabled = false;
            }
        });

        $("registerForm").addEventListener("submit", async (e) => {
            e.preventDefault();
            const submitBtn = $("registerSubmitBtn");
            submitBtn.disabled = true;
            setInlineAlert($("authDialogError"), "");
            try {
                const role = $("regRole").value;
                const payload = {
                    username: $("regUsername").value.trim(),
                    phone: $("regPhone").value.trim(),
                    password: $("regPassword").value,
                    role,
                    fleet_id: role === "fleet" ? Number($("regFleetId").value) : null,
                };
                const { data } = await apiFetch("/api/auth/register", {
                    method: "POST",
                    body: JSON.stringify(payload),
                });
                switchAuthMode("login");
                $("loginUsername").value = data.username;
                setInlineAlert($("authDialogError"), "");
                $("authDialog").close();
                showToast(`账号 ${data.username} 注册成功，请登录`);
            } catch (err) {
                const msg =
                    err.status === 500
                        ? `注册失败 (HTTP 500)：需业务线 4（user_account）合入建表后可用。`
                        : `注册失败 (${err.status || "-"})：${err.message}`;
                setInlineAlert($("authDialogError"), msg);
            } finally {
                submitBtn.disabled = false;
            }
        });

        // Tabs 鼠标与键盘导航（selection by activation）
        const tabs = Array.from(document.querySelectorAll('#mainTabs [role="tab"]'));
        $("mainTabs").addEventListener("click", (e) => {
            const btn = e.target.closest('[role="tab"]');
            if (btn) activatePanel(btn.dataset.panel);
        });
        $("mainTabs").addEventListener("keydown", (e) => {
            const currentIdx = tabs.indexOf(document.activeElement);
            if (currentIdx === -1) return;
            let nextIdx = null;
            if (e.key === "ArrowRight") nextIdx = (currentIdx + 1) % tabs.length;
            else if (e.key === "ArrowLeft") nextIdx = (currentIdx - 1 + tabs.length) % tabs.length;
            else if (e.key === "Home") nextIdx = 0;
            else if (e.key === "End") nextIdx = tabs.length - 1;
            if (nextIdx !== null) {
                e.preventDefault();
                tabs[nextIdx].focus();
            }
        });
        document.querySelectorAll('[role="tabpanel"]').forEach((panel) => {
            panel.addEventListener("beforematch", () => {
                activatePanel(panel.id);
            });
        });

        // Tab 1 找站与开单
        $("stationSearchForm").addEventListener("submit", (e) => {
            e.preventDefault();
            loadDriverStations(1);
        });
        $("resetStationsBtn").addEventListener("click", () => {
            $("stationKeyword").value = "";
            $("onlyIdleFast").checked = false;
            loadDriverStations(1);
        });
        $("gunSearchForm").addEventListener("submit", (e) => {
            e.preventDefault();
            loadDriverGuns(1);
        });
        $("resetGunsBtn").addEventListener("click", () => {
            $("gunStationFilter").value = "";
            $("gunTypeFilter").value = "";
            $("gunStatusFilter").value = "";
            loadDriverGuns(1);
        });
        $("driverStationGrid").addEventListener("click", (e) => {
            const btn = e.target.closest(".view-station-guns");
            if (!btn) return;
            $("gunStationFilter").value = btn.dataset.stationId;
            loadDriverGuns(1);
            $("driverGunsCard").scrollIntoView({ behavior: "smooth", block: "start" });
        });
        $("driverGunsBody").addEventListener("click", (e) => {
            const btn = e.target.closest(".start-gun-order-btn");
            if (!btn) return;
            openStartOrderModal({
                stationId: btn.dataset.stationId,
                gunId: btn.dataset.gunId,
                stationName: btn.dataset.stationName,
                gunCode: btn.dataset.gunCode,
            });
        });

        // 扫码开单提交
        $("startOrderForm").addEventListener("submit", async (e) => {
            e.preventDefault();
            const submitBtn = $("startOrderSubmitBtn");
            submitBtn.disabled = true;
            setInlineAlert($("startOrderError"), "");
            try {
                const vehicleRaw = $("soVehicleId").value.trim();
                const socRaw = $("soStartSoc").value.trim();
                const payload = {
                    station_id: Number($("soStationId").value),
                    gun_id: Number($("soGunId").value),
                    vehicle_id: vehicleRaw ? Number(vehicleRaw) : null,
                    start_soc: socRaw || null,
                };
                const { data } = await apiFetch("/api/orders/start", {
                    method: "POST",
                    body: JSON.stringify(payload),
                });
                $("startOrderDialog").close();
                showToast(`开单成功：${data.order_no}（订单 #${data.order_id}）`);
                if (state.activePanel === "panel-driver") loadDriverGuns();
                if (state.activePanel === "panel-orders") loadOrders(1);
            } catch (err) {
                setInlineAlert($("startOrderError"), `开单被拦截 (${err.status || "-"})：${err.message}`);
            } finally {
                submitBtn.disabled = false;
            }
        });

        // Tab 2 订单管理与分时明细
        $("openManualOrderBtn").addEventListener("click", () => {
            openStartOrderModal({ stationId: 1, gunId: 1 });
        });
        $("refreshOrdersBtn").addEventListener("click", () => loadOrders());
        $("periodStatForm").addEventListener("submit", (e) => {
            e.preventDefault();
            loadPeriodStats();
        });

        $("ordersBody").addEventListener("click", (e) => {
            const detailBtn = e.target.closest(".view-order-detail-btn");
            const finishBtn = e.target.closest(".finish-order-btn");
            const payBtn = e.target.closest(".pay-order-btn");

            if (detailBtn) {
                showOrderDetailModal(detailBtn.dataset.orderId);
            } else if (finishBtn) {
                setInlineAlert($("finishOrderError"), "");
                $("foOrderId").value = finishBtn.dataset.orderId;
                $("finishOrderInfo").textContent = `正在结束订单 #${finishBtn.dataset.orderId}（${finishBtn.dataset.orderNo}），提交后调用 sp_order_calc 按峰平谷拆账。`;
                $("finishOrderDialog").showModal();
            } else if (payBtn) {
                setInlineAlert($("payOrderError"), "");
                $("poOrderId").value = payBtn.dataset.orderId;
                const unpaid = payBtn.dataset.unpaid;
                $("poAmount").value = Number(unpaid) > 0 ? unpaid : "1.00";
                $("poTxnNo").value = genTxnNo("PAY");
                $("payOrderInfo").textContent = `订单 #${payBtn.dataset.orderId}（${payBtn.dataset.orderNo}）· 总额 ¥${payBtn.dataset.total} · 当前待付 ¥${unpaid}`;
                $("payOrderDialog").showModal();
            }
        });

        $("finishOrderForm").addEventListener("submit", async (e) => {
            e.preventDefault();
            const submitBtn = $("finishOrderSubmitBtn");
            submitBtn.disabled = true;
            setInlineAlert($("finishOrderError"), "");
            try {
                const orderId = $("foOrderId").value;
                const endSoc = $("foEndSoc").value.trim();
                const { data } = await apiFetch(`/api/orders/${orderId}/finish`, {
                    method: "POST",
                    body: JSON.stringify({
                        kwh: $("foKwh").value.trim(),
                        end_soc: endSoc || null,
                    }),
                });
                $("finishOrderDialog").close();
                showToast(`订单 #${data.order_id} 计费完成：应付 ¥${data.total_amount}`);
                loadOrders();
            } catch (err) {
                setInlineAlert($("finishOrderError"), `结束计费失败 (${err.status || "-"})：${err.message}`);
            } finally {
                submitBtn.disabled = false;
            }
        });

        $("genTxnBtn").addEventListener("click", () => {
            $("poTxnNo").value = genTxnNo("PAY");
        });
        $("presetRefundBtn").addEventListener("click", () => {
            $("poAmount").value = "-1.00";
            $("poTxnNo").value = genTxnNo("REF");
        });
        $("presetOverpayBtn").addEventListener("click", () => {
            $("poAmount").value = "9999.00";
            $("poTxnNo").value = genTxnNo("OVR");
        });

        $("payOrderForm").addEventListener("submit", async (e) => {
            e.preventDefault();
            const submitBtn = $("payOrderSubmitBtn");
            submitBtn.disabled = true;
            setInlineAlert($("payOrderError"), "");
            try {
                const orderId = $("poOrderId").value;
                const { data } = await apiFetch(`/api/orders/${orderId}/pay`, {
                    method: "POST",
                    body: JSON.stringify({
                        amount: $("poAmount").value.trim(),
                        method: $("poMethod").value,
                        txn_no: $("poTxnNo").value.trim(),
                    }),
                });
                $("payOrderDialog").close();
                showToast(`流水已记账：${data.txn_no}（¥${data.amount} · ${data.status}）`);
                loadOrders();
            } catch (err) {
                setInlineAlert($("payOrderError"), `支付/退款被拦截 (${err.status || "-"})：${err.message}`);
            } finally {
                submitBtn.disabled = false;
            }
        });

        // Tab 3 运维排障与巡检
        $("heartbeatCheckBtn").addEventListener("click", runHeartbeatCheck);
        $("reportHeartbeatBtn").addEventListener("click", reportSingleHeartbeat);
        $("commissionPileBtn").addEventListener("click", () => {
            const pileId = Number($("opsPileId").value || 0);
            if (!pileId) return;
            openCommissionDialog(pileId);
        });
        $("openPileStatusBtn").addEventListener("click", () => {
            const pileId = Number($("opsPileId").value || 0);
            if (!pileId) return;
            openDeviceStatusDialog("PILE", pileId, "OFFLINE");
        });
        $("faultSearchForm").addEventListener("submit", (e) => {
            e.preventDefault();
            loadFaults(1);
        });
        $("logSearchForm").addEventListener("submit", (e) => {
            e.preventDefault();
            loadStatusLogs(1);
        });

        $("faultsBody").addEventListener("click", async (e) => {
            const pileBtn = e.target.closest(".recover-pile-btn");
            const gunBtn = e.target.closest(".recover-gun-btn");
            const customBtn = e.target.closest(".custom-pile-status-btn");
            try {
                if (pileBtn) {
                    const pid = pileBtn.dataset.pileId;
                    await apiFetch(`/api/stations/piles/${pid}/status`, {
                        method: "PATCH",
                        body: JSON.stringify({
                            new_status: "IDLE",
                            change_source: "MANUAL",
                            todo_status: "DONE",
                            remark: "运维调度台快速恢复充电桩为空闲",
                        }),
                    });
                    showToast(`充电桩 #${pid} 已恢复 IDLE`);
                    loadFaults();
                    loadStatusLogs(1);
                } else if (gunBtn) {
                    const gid = gunBtn.dataset.gunId;
                    await apiFetch(`/api/stations/guns/${gid}/status`, {
                        method: "PATCH",
                        body: JSON.stringify({
                            new_status: "IDLE",
                            change_source: "MANUAL",
                            todo_status: "DONE",
                            remark: "运维调度台快速恢复充电枪为空闲",
                        }),
                    });
                    showToast(`充电枪 #${gid} 已恢复 IDLE`);
                    loadFaults();
                    loadStatusLogs(1);
                } else if (customBtn) {
                    openDeviceStatusDialog("PILE", customBtn.dataset.pileId, customBtn.dataset.cur);
                }
            } catch (err) {
                showToast(`状态更新失败 (${err.status || "-"})：${err.message}`, true);
            }
        });

        $("statusLogsBody").addEventListener("click", (e) => {
            const btn = e.target.closest(".resolve-todo-btn");
            if (!btn) return;
            const logId = btn.dataset.logId;
            openActionDialog({
                title: `办结运维待办 #${logId}`,
                desc: `目标设备：${btn.dataset.device}。提交后会将待办置为 DONE 并追加处置备注。`,
                submitText: "确认办结",
                fields: [
                    { name: "remark", label: "现场处置备注", type: "text", value: "运维工程师已现场排查并确认恢复", required: true, maxlength: 255 },
                ],
                onSubmit: async (vals) => {
                    const { data } = await apiFetch(`/api/stations/status-logs/${logId}/resolve-todo`, {
                        method: "POST",
                        body: JSON.stringify({ remark: vals.remark.trim() }),
                    });
                    showToast(`${data.message} (#${data.id})`);
                    loadStatusLogs();
                },
            });
        });

        // Tab 4 资产看板、运营商管理与三级建档
        $("realtimeSearchForm").addEventListener("submit", (e) => {
            e.preventDefault();
            loadRealtimeBoard(1);
        });
        $("loadOperatorsFullBtn").addEventListener("click", () => loadOperators(false));
        $("loadOperatorsSummaryBtn").addEventListener("click", () => loadOperators(true));
        $("openCreateOperatorBtn").addEventListener("click", () => {
            openActionDialog({
                title: "新增合作运营商（仅 admin）",
                submitText: "创建运营商",
                fields: [
                    { name: "operator_code", label: "运营商编码（3~32位）", value: "OP-DEMO-101", minlength: 3, maxlength: 32, required: true },
                    { name: "operator_name", label: "运营商全称", value: "演示智慧能源科技有限公司", minlength: 2, maxlength: 100, required: true },
                    { name: "credit_code", label: "统一社会信用代码（18位）", value: "91110108MA0DEMO101", minlength: 18, maxlength: 18, required: true },
                    { name: "contact_name", label: "业务联系人", value: "张示范", required: true },
                    { name: "service_phone", label: "客服热线", value: "400-888-1001", minlength: 5, maxlength: 20, required: true },
                    {
                        name: "status",
                        label: "合作状态",
                        type: "select",
                        value: "ACTIVE",
                        options: [
                            { value: "ACTIVE", label: "ACTIVE 正常合作" },
                            { value: "SUSPENDED", label: "SUSPENDED 暂停合作" },
                        ],
                    },
                ],
                onSubmit: async (vals) => {
                    const { data } = await apiFetch("/api/stations/operators", {
                        method: "POST",
                        body: JSON.stringify(vals),
                    });
                    showToast(`运营商创建成功：#${data.operator_id} ${data.operator_name}`);
                    loadOperators(false);
                },
            });
        });

        $("operatorsBody").addEventListener("click", (e) => {
            const btn = e.target.closest(".edit-operator-btn");
            if (!btn) return;
            const op = state.operatorCache.get(btn.dataset.operatorId);
            if (!op) return;
            openActionDialog({
                title: `编辑运营商 #${op.operator_id}（${op.operator_code}）`,
                submitText: "保存修改",
                fields: [
                    { name: "operator_name", label: "运营商全称", value: op.operator_name, minlength: 2, maxlength: 100, required: true },
                    { name: "contact_name", label: "业务联系人", value: op.contact_name, required: true },
                    { name: "service_phone", label: "客服热线", value: op.service_phone, minlength: 5, maxlength: 20, required: true },
                    {
                        name: "status",
                        label: "合作状态",
                        type: "select",
                        value: op.status,
                        options: [
                            { value: "ACTIVE", label: "ACTIVE 正常合作" },
                            { value: "SUSPENDED", label: "SUSPENDED 暂停合作" },
                        ],
                    },
                ],
                onSubmit: async (vals) => {
                    const { data } = await apiFetch(`/api/stations/operators/${op.operator_id}`, {
                        method: "PATCH",
                        body: JSON.stringify(vals),
                    });
                    showToast(`运营商 #${data.operator_id} 已更新`);
                    loadOperators(false);
                },
            });
        });

        $("realtimeBody").addEventListener("click", (e) => {
            const drillBtn = e.target.closest(".drill-station-btn");
            const editBtn = e.target.closest(".edit-station-btn");
            if (drillBtn) {
                drillPiles(Number(drillBtn.dataset.stationId));
            } else if (editBtn) {
                const st = state.stationCache.get(editBtn.dataset.stationId);
                if (!st) return;
                openActionDialog({
                    title: `编辑场站 #${st.station_id}（${st.station_code}）`,
                    desc: "提示：若将空闲车位改到大于总车位数，会触发数据库 CHECK 约束 3819 拦截。",
                    submitText: "保存场站变更",
                    fields: [
                        { name: "station_name", label: "场站名称", value: st.station_name, minlength: 2, maxlength: 120, required: true },
                        { name: "address", label: "详细地址", value: st.address, minlength: 2, maxlength: 255, required: true },
                        { name: "total_spaces", label: "总车位", type: "number", min: 1, value: st.total_spaces, required: true },
                        { name: "available_spaces", label: "空闲车位", type: "number", min: 0, value: st.available_spaces, required: true },
                        { name: "business_hours", label: "营业时间", value: st.business_hours, required: true },
                        { name: "parking_fee_rate", label: "停车费 (元/h)", value: st.parking_fee_rate, inputmode: "decimal", required: true },
                        {
                            name: "station_status",
                            label: "营业状态",
                            type: "select",
                            value: st.station_status,
                            options: [
                                { value: "OPEN", label: "OPEN 营业中" },
                                { value: "MAINTENANCE", label: "MAINTENANCE 维护中" },
                                { value: "CLOSED", label: "CLOSED 已关闭" },
                            ],
                        },
                    ],
                    onSubmit: async (vals) => {
                        const { data } = await apiFetch(`/api/stations/${st.station_id}`, {
                            method: "PATCH",
                            body: JSON.stringify({
                                ...vals,
                                total_spaces: Number(vals.total_spaces),
                                available_spaces: Number(vals.available_spaces),
                            }),
                        });
                        showToast(`场站 #${data.station_id} 更新成功`);
                        loadRealtimeBoard();
                    },
                });
            }
        });

        $("drillPilesBtn").addEventListener("click", () => drillPiles(Number($("drillStationId").value || 1)));
        $("drillGunsBtn").addEventListener("click", () => drillGuns(Number($("drillPileId").value || 1)));
        $("drillResultBox").addEventListener("click", (e) => {
            const gunBtn = e.target.closest(".drill-pile-btn");
            const pileStatusBtn = e.target.closest(".drill-pile-status-btn");
            const commBtn = e.target.closest(".drill-commission-btn");
            const gunStatusBtn = e.target.closest(".drill-gun-status-btn");
            if (gunBtn) {
                drillGuns(Number(gunBtn.dataset.pileId));
            } else if (pileStatusBtn) {
                openDeviceStatusDialog("PILE", pileStatusBtn.dataset.pileId, pileStatusBtn.dataset.cur, () =>
                    drillPiles(Number($("drillStationId").value || 1))
                );
            } else if (commBtn) {
                openCommissionDialog(Number(commBtn.dataset.pileId), () =>
                    drillPiles(Number($("drillStationId").value || 1))
                );
            } else if (gunStatusBtn) {
                openDeviceStatusDialog("GUN", gunStatusBtn.dataset.gunId, gunStatusBtn.dataset.cur, () =>
                    drillGuns(Number($("drillPileId").value || 1))
                );
            }
        });

        $("createStationForm").addEventListener("submit", async (e) => {
            e.preventDefault();
            const fd = new FormData(e.target);
            const payload = Object.fromEntries(fd.entries());
            payload.operator_id = Number(payload.operator_id);
            payload.total_spaces = Number(payload.total_spaces);
            payload.available_spaces = Number(payload.available_spaces);
            try {
                const { data } = await apiFetch("/api/stations", {
                    method: "POST",
                    body: JSON.stringify(payload),
                });
                showToast(`场站创建成功：#${data.station_id} ${data.station_name}`);
                loadRealtimeBoard(1);
            } catch (err) {
                showToast(`创建场站被拦截 (${err.status || "-"})：${err.message}`, true);
            }
        });

        $("createPileForm").addEventListener("submit", async (e) => {
            e.preventDefault();
            const fd = new FormData(e.target);
            const payload = Object.fromEntries(fd.entries());
            payload.station_id = Number(payload.station_id);
            payload.gun_count = Number(payload.gun_count);
            payload.offline_threshold_sec = Number(payload.offline_threshold_sec);
            try {
                const { data } = await apiFetch("/api/stations/piles", {
                    method: "POST",
                    body: JSON.stringify(payload),
                });
                showToast(`充电桩建档成功：#${data.pile_id} ${data.pile_code}`);
                drillPiles(payload.station_id);
            } catch (err) {
                showToast(`建档被拦截 (${err.status || "-"})：${err.message}`, true);
            }
        });

        $("createGunForm").addEventListener("submit", async (e) => {
            e.preventDefault();
            const fd = new FormData(e.target);
            const payload = Object.fromEntries(fd.entries());
            payload.pile_id = Number(payload.pile_id);
            payload.gun_seq = Number(payload.gun_seq);
            try {
                const { data } = await apiFetch("/api/stations/guns", {
                    method: "POST",
                    body: JSON.stringify(payload),
                });
                showToast(`充电枪建档成功：#${data.gun_id} ${data.gun_code}`);
                drillGuns(payload.pile_id);
            } catch (err) {
                showToast(`建枪被拦截 (${err.status || "-"})：${err.message}`, true);
            }
        });

        // Tab 5 结算对账与开票红冲
        $("generateBillForm").addEventListener("submit", async (e) => {
            e.preventDefault();
            try {
                const { data } = await apiFetch("/api/settlements/generate", {
                    method: "POST",
                    body: JSON.stringify({
                        operator_id: Number($("genOperatorId").value),
                        bill_month: $("genBillMonth").value.trim(),
                        platform_rate: $("genPlatformRate").value.trim(),
                    }),
                });
                showToast(data.message);
                loadBills(1);
                loadDiffs(1);
            } catch (err) {
                showToast(`生成结算单失败 (${err.status || "-"})：${err.message}`, true);
            }
        });

        $("billSearchForm").addEventListener("submit", (e) => {
            e.preventDefault();
            loadBills(1);
        });
        $("refreshDiffsBtn").addEventListener("click", () => loadDiffs(1));

        $("billsBody").addEventListener("click", (e) => {
            const detailBtn = e.target.closest(".view-bill-detail-btn");
            const invBtn = e.target.closest(".issue-invoice-btn");
            if (detailBtn) {
                showBillDetailModal(detailBtn.dataset.billId);
            } else if (invBtn) {
                const billId = invBtn.dataset.billId;
                const uninvoiced = invBtn.dataset.uninvoiced;
                openActionDialog({
                    title: `为结算单 #${billId} 开具发票`,
                    desc: `账单应付 ¥${invBtn.dataset.payable}，当前剩余未开票 ¥${uninvoiced}。超额开票将触发触发器 1644 拦截。`,
                    submitText: "确认开票",
                    fields: [
                        { name: "invoice_no", label: "发票号码", value: genTxnNo("INV").slice(0, 18), minlength: 4, maxlength: 32, required: true },
                        { name: "amount", label: "开票金额 (¥)", value: Number(uninvoiced) > 0 ? uninvoiced : "100.00", inputmode: "decimal", required: true },
                        {
                            name: "invoice_type",
                            label: "发票类型",
                            type: "select",
                            value: "NORMAL",
                            options: [
                                { value: "NORMAL", label: "NORMAL 普通发票" },
                                { value: "SPECIAL", label: "SPECIAL 专用发票" },
                            ],
                        },
                    ],
                    onSubmit: async (vals) => {
                        const { data } = await apiFetch(`/api/settlements/${billId}/invoices`, {
                            method: "POST",
                            body: JSON.stringify({
                                invoice_no: vals.invoice_no.trim(),
                                amount: vals.amount.trim(),
                                invoice_type: vals.invoice_type,
                            }),
                        });
                        state.sessionInvoices.unshift(data);
                        renderSessionInvoices();
                        $("rfInvoiceId").value = data.invoice_id;
                        showToast(`开票成功：#${data.invoice_id} ${data.invoice_no}（¥${data.amount}）`);
                        loadBills();
                    },
                });
            }
        });

        $("diffsBody").addEventListener("click", (e) => {
            const btn = e.target.closest(".resolve-diff-btn");
            if (!btn) return;
            const diffId = btn.dataset.diffId;
            openActionDialog({
                title: `核销对账差异 #${diffId}`,
                submitText: "标记已处理",
                fields: [
                    { name: "remark", label: "核销备注说明", value: "已核对第三方支付流水并确认平账", maxlength: 200, required: true },
                ],
                onSubmit: async (vals) => {
                    const { data } = await apiFetch(`/api/settlements/diffs/${diffId}/resolve`, {
                        method: "POST",
                        body: JSON.stringify({ remark: vals.remark.trim() }),
                    });
                    showToast(`差异 #${data.diff_id} 已标记处理`);
                    loadDiffs();
                    loadBills();
                },
            });
        });

        $("redFlushForm").addEventListener("submit", async (e) => {
            e.preventDefault();
            const invoiceId = Number($("rfInvoiceId").value);
            const invoiceNo = $("rfInvoiceNo").value.trim();
            const amtRaw = $("rfAmount").value.trim();
            try {
                const { data } = await apiFetch(`/api/settlements/invoices/${invoiceId}/red-flush`, {
                    method: "POST",
                    body: JSON.stringify({
                        invoice_no: invoiceNo,
                        amount: amtRaw || null,
                    }),
                });
                $("rfInvoiceNo").value = "";
                showToast(`红冲完成：生成负数票 #${data.invoice_id} ${data.invoice_no}（¥${data.amount}）`);
                loadBills();
            } catch (err) {
                showToast(`红冲被拦截 (${err.status || "-"})：${err.message}`, true);
            }
        });

        $("sessionInvoicesList").addEventListener("click", (e) => {
            const btn = e.target.closest(".pick-invoice-btn");
            if (!btn) return;
            $("rfInvoiceId").value = btn.dataset.invoiceId;
            $("rfInvoiceNo").value = genTxnNo("RED").slice(0, 18);
            $("rfAmount").value = `-${btn.dataset.amount}`;
        });

        // 通用弹窗提交处理器
        $("actionDialogForm").addEventListener("submit", async (e) => {
            e.preventDefault();
            if (!currentActionHandler) return;
            const submitBtn = $("actionDialogSubmitBtn");
            submitBtn.disabled = true;
            setInlineAlert($("actionDialogError"), "");
            try {
                const fd = new FormData(e.target);
                const vals = Object.fromEntries(fd.entries());
                await currentActionHandler(vals);
                $("actionDialog").close();
            } catch (err) {
                setInlineAlert($("actionDialogError"), `操作被拦截 (${err.status || "-"})：${err.message}`);
            } finally {
                submitBtn.disabled = false;
            }
        });

        // Tab 6 实验控制台探针
        $("probeFaultsBtn").addEventListener("click", () => probeEndpoint("GET", "/api/stations/faults?page=1&page_size=5"));
        $("probeRealtimeBtn").addEventListener("click", () => probeEndpoint("GET", "/api/stations/realtime-status?page=1&page_size=5"));
        $("probeOperatorsBtn").addEventListener("click", () => probeEndpoint("GET", "/api/stations/operators"));
        $("probeOrdersBtn").addEventListener("click", () => probeEndpoint("GET", "/api/orders?page=1&page_size=5"));
        $("probeBillsBtn").addEventListener("click", () => probeEndpoint("GET", "/api/settlements?page=1&page_size=5"));
        $("probeCommission1Btn").addEventListener("click", () =>
            probeEndpoint("POST", "/api/stations/piles/1/commission", { remark: "触发重复投运1644" })
        );
        $("probeOverpayBtn").addEventListener("click", async () => {
            let targetOrderId = 6;
            if (state.user && (state.user.role === "driver" || state.user.role === "fleet")) {
                try {
                    const { data } = await apiFetch("/api/orders?page=1&page_size=20");
                    const paidOrFinished = data.items?.find((o) => o.status === "PAID" || o.status === "FINISHED");
                    const anyOwn = paidOrFinished || data.items?.[0];
                    if (anyOwn) targetOrderId = anyOwn.order_id;
                } catch {
                    // 回退到默认订单 6
                }
            }
            probeEndpoint("POST", `/api/orders/${targetOrderId}/pay`, {
                amount: "99999.00",
                method: "WECHAT",
                txn_no: genTxnNo("LABOVR"),
            });
        });
        $("probeOverInvoiceBtn").addEventListener("click", () =>
            probeEndpoint("POST", "/api/settlements/1/invoices", {
                amount: "999999.00",
                invoice_no: genTxnNo("LABINV").slice(0, 20),
                invoice_type: "NORMAL",
            })
        );
        $("clearConsoleBtn").addEventListener("click", () => {
            const codeEl = $("apiConsole").querySelector("code");
            if (codeEl) codeEl.textContent = "等待发起探测请求...";
        });
    }

    function openCommissionDialog(pileId, onSuccess = null) {
        const today = new Date().toISOString().slice(0, 10);
        openActionDialog({
            title: `充电桩 #${pileId} 协议联调验收投运`,
            desc: "调用存储过程 proc_commission_charging_pile；未配枪或已验收桩将触发 1644 拦截。",
            submitText: "确认投运",
            fields: [
                { name: "commissioned_date", label: "投运日期", type: "date", value: today },
                { name: "remark", label: "现场铭牌及联调备注", value: "现场铭牌核对与协议联调通过", maxlength: 255, required: true },
            ],
            onSubmit: async (vals) => {
                const { data } = await apiFetch(`/api/stations/piles/${pileId}/commission`, {
                    method: "POST",
                    body: JSON.stringify({
                        commissioned_date: vals.commissioned_date || null,
                        remark: vals.remark.trim(),
                    }),
                });
                showToast(`桩 #${data.pile_id} (${data.pile_code}) 已验收投运：${data.acceptance_status} / ${data.current_status}`);
                if (onSuccess) onSuccess();
                else loadStatusLogs(1);
            },
        });
    }

    function openDeviceStatusDialog(deviceType, targetId, currentStatus = "IDLE", onSuccess = null) {
        const isPile = deviceType === "PILE";
        const endpoint = isPile ? `/api/stations/piles/${targetId}/status` : `/api/stations/guns/${targetId}/status`;
        openActionDialog({
            title: `变更${isPile ? "充电桩" : "充电枪"} #${targetId} 运行状态`,
            desc: "通过会话变量透传触发来源至 MySQL 审计触发器，状态变迁将自动写入 device_status_log。",
            submitText: "提交状态变更",
            fields: [
                {
                    name: "new_status",
                    label: "目标运行状态",
                    type: "select",
                    value: currentStatus,
                    options: [
                        { value: "IDLE", label: "IDLE 空闲" },
                        { value: "OCCUPIED", label: "OCCUPIED 占用" },
                        { value: "FAULT", label: "FAULT 故障" },
                        { value: "OFFLINE", label: "OFFLINE 离线" },
                    ],
                },
                {
                    name: "change_source",
                    label: "变更来源",
                    type: "select",
                    value: "MANUAL",
                    options: [
                        { value: "MANUAL", label: "MANUAL 人工维护" },
                        { value: "ALARM", label: "ALARM 告警触发" },
                        { value: "HEARTBEAT", label: "HEARTBEAT 心跳检测" },
                    ],
                },
                {
                    name: "todo_status",
                    label: "运维待办标记",
                    type: "select",
                    value: "",
                    options: [
                        { value: "", label: "自动判定（故障/离线自动 PENDING）" },
                        { value: "PENDING", label: "PENDING 待处理" },
                        { value: "DONE", label: "DONE 已办结" },
                        { value: "NONE", label: "NONE 无需处理" },
                    ],
                },
                { name: "remark", label: "变更说明", value: "运维调度台手动变更设备状态", maxlength: 255, required: true },
            ],
            onSubmit: async (vals) => {
                await apiFetch(endpoint, {
                    method: "PATCH",
                    body: JSON.stringify({
                        new_status: vals.new_status,
                        change_source: vals.change_source,
                        todo_status: vals.todo_status || null,
                        remark: vals.remark.trim(),
                    }),
                });
                showToast(`${isPile ? "充电桩" : "充电枪"} #${targetId} 状态已变更为 ${vals.new_status}`);
                if (onSuccess) {
                    onSuccess();
                } else {
                    loadFaults();
                    loadStatusLogs(1);
                }
            },
        });
    }

    async function init() {
        bindEvents();
        await Promise.all([checkDbHealth(), loadRolesMeta()]);
        await restoreSessionOrDefault();
        refreshActivePanelData();
    }

    init();
})();
