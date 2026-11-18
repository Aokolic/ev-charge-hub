(() => {
    "use strict";

    const state = {
        token: localStorage.getItem("ev_hub_token") || "",
        user: null,
        rolesMap: {},
        stationPage: 1,
        stationPageSize: 9,
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

    let toastTimer = null;
    function showToast(message, isError = false) {
        const el = $("toast");
        el.textContent = message;
        el.className = `toast show ${isError ? "err" : "ok"}`;
        if (toastTimer) clearTimeout(toastTimer);
        toastTimer = setTimeout(() => {
            el.className = "toast";
        }, 3600);
    }

    function logConsole(method, url, status, data) {
        const box = $("apiConsole");
        const stamp = new Date().toLocaleTimeString();
        const header = `[${stamp}] ${method} ${url} -> HTTP ${status}`;
        const body = typeof data === "string" ? data : JSON.stringify(data, null, 2);
        box.textContent = `${header}\n${body}\n\n` + box.textContent;
    }

    async function apiFetch(url, options = {}) {
        const headers = new Headers(options.headers || {});
        if (state.token) {
            headers.set("Authorization", `Bearer ${state.token}`);
        }
        if (options.body && !headers.has("Content-Type")) {
            headers.set("Content-Type", "application/json");
        }
        const resp = await fetch(url, { ...options, headers });
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
            throw err;
        }
        return { status: resp.status, data };
    }

    function statusBadge(st) {
        const map = {
            IDLE: ["IDLE 空闲", "badge-ok"],
            OPEN: ["OPEN 营业中", "badge-ok"],
            ACTIVE: ["ACTIVE 正常", "badge-ok"],
            PASSED: ["PASSED 已验收", "badge-ok"],
            DONE: ["DONE 已办结", "badge-ok"],
            OCCUPIED: ["OCCUPIED 充电中", "badge-info"],
            CHARGING: ["CHARGING 充电中", "badge-info"],
            PENDING: ["PENDING 待处理", "badge-warn"],
            MAINTENANCE: ["MAINTENANCE 维护", "badge-warn"],
            SUSPENDED: ["SUSPENDED 暂停", "badge-warn"],
            FAULT: ["FAULT 故障", "badge-danger"],
            REJECTED: ["REJECTED 未通过", "badge-danger"],
            OFFLINE: ["OFFLINE 离线", "badge-muted"],
            CLOSED: ["CLOSED 关闭", "badge-muted"],
            NONE: ["NONE", "badge-muted"],
        };
        const [label, cls] = map[st] || [String(st || "-"), "badge-muted"];
        return `<span class="badge ${cls}">${escapeHtml(label)}</span>`;
    }

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

    function renderUserContext() {
        const label = $("currentUserLabel");
        const desc = $("currentRoleDesc");
        document.querySelectorAll("#roleButtons .role-btn").forEach((btn) => {
            btn.classList.toggle("active", Boolean(state.user && btn.dataset.role === state.user.role));
        });

        if (!state.user) {
            label.textContent = "未认证";
            desc.textContent = "请点击左侧任意角色按钮签发测试 Token。";
            return;
        }
        const roleMeta = state.rolesMap[state.user.role];
        const fleetNote = state.user.fleet_id ? ` · fleet_id=${state.user.fleet_id}` : "";
        label.textContent = `${state.user.username}（${state.user.role_label} / ${state.user.role} · uid=${state.user.user_id}${fleetNote}）`;
        desc.textContent = roleMeta ? roleMeta.description : "";
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
            showToast(`已切换为 ${data.user.role_label}`);
            refreshActiveTabData();
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

    async function realLogin() {
        const username = $("loginUsername").value.trim();
        const password = $("loginPassword").value;
        if (!username || !password) {
            showToast("请输入用户名和密码", true);
            return;
        }
        try {
            const { data } = await apiFetch("/api/auth/login", {
                method: "POST",
                body: JSON.stringify({ username, password }),
            });
            state.token = data.access_token;
            state.user = data.user;
            localStorage.setItem("ev_hub_token", state.token);
            renderUserContext();
            $("loginPanel").style.display = "none";
            showToast(`登录成功：${data.user.username}`);
            refreshActiveTabData();
        } catch (err) {
            showToast(`登录失败 (${err.status || "-"})：${err.message}`, true);
        }
    }

    // ------------------------------------------------------------------------
    // Tab 1: 车主 / 车队找站查枪
    // ------------------------------------------------------------------------
    async function loadDriverStations(page = state.stationPage) {
        state.stationPage = page;
        const grid = $("driverStationGrid");
        const pager = $("driverStationPager");
        const qs = new URLSearchParams({
            page: String(page),
            page_size: String(state.stationPageSize),
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
                .map(
                    (s) => `
                    <article class="station-card">
                        <div>
                            <div style="display:flex;justify-content:space-between;align-items:center;gap:6px;">
                                <h4>${escapeHtml(s.station_name)}</h4>
                                <span class="badge badge-ok">营业中</span>
                            </div>
                            <div class="meta">${escapeHtml(s.station_code)} · ${escapeHtml(s.operator_name)}</div>
                            <div class="meta" style="margin-top:4px;">${escapeHtml(s.address)}</div>
                            <div class="meta" style="margin-top:4px;">
                                营业：${escapeHtml(s.business_hours)} ｜ 停车费：¥${escapeHtml(s.parking_fee_rate)}/h
                            </div>
                        </div>
                        <div>
                            <div class="meta" style="margin-bottom:8px;">
                                车位 <strong>${escapeHtml(s.available_spaces)}/${escapeHtml(s.total_spaces)}</strong> ｜
                                活跃桩 <strong>${escapeHtml(s.active_pile_count)}</strong> ｜
                                空闲枪 <strong style="color:var(--primary)">${escapeHtml(s.idle_gun_count)}/${escapeHtml(s.total_gun_count)}</strong>
                                （快 ${escapeHtml(s.idle_fast_gun_count)} / 慢 ${escapeHtml(s.idle_slow_gun_count)}）
                            </div>
                            <button class="btn btn-outline btn-sm view-station-guns" data-station-id="${escapeHtml(s.station_id)}" type="button">
                                查看该站可用枪
                            </button>
                        </div>
                    </article>`
                )
                .join("");

            const totalPages = Math.max(1, Math.ceil(data.total / data.page_size));
            pager.innerHTML = `
                <span>共 ${data.total} 座营业场站 · 第 ${data.page}/${totalPages} 页</span>
                <button class="btn btn-outline btn-sm" id="prevStationsPage" ${data.page <= 1 ? "disabled" : ""} type="button">上一页</button>
                <button class="btn btn-outline btn-sm" id="nextStationsPage" ${data.page >= totalPages ? "disabled" : ""} type="button">下一页</button>
            `;
            $("prevStationsPage")?.addEventListener("click", () => loadDriverStations(data.page - 1));
            $("nextStationsPage")?.addEventListener("click", () => loadDriverStations(data.page + 1));
        } catch (err) {
            grid.innerHTML = `<div class="empty-hint">无法加载场站概览 (${escapeHtml(err.status || "-")})：${escapeHtml(err.message)}</div>`;
            pager.innerHTML = "";
        }
    }

    async function loadDriverGuns() {
        const tbody = $("driverGunsBody");
        const qs = new URLSearchParams({ page: "1", page_size: "30" });
        const stId = $("gunStationFilter").value.trim();
        const gType = $("gunTypeFilter").value;
        const gStatus = $("gunStatusFilter").value;
        if (stId) qs.set("station_id", stId);
        if (gType) qs.set("gun_type", gType);
        if (gStatus) qs.set("gun_status", gStatus);

        try {
            const { data } = await apiFetch(`/api/stations/guns/availability?${qs.toString()}`);
            if (!data.items.length) {
                tbody.innerHTML = '<tr><td colspan="8">暂无符合条件的可用充电枪</td></tr>';
                return;
            }
            tbody.innerHTML = data.items
                .map(
                    (g) => `
                    <tr>
                        <td>${escapeHtml(g.gun_id)}</td>
                        <td><code>${escapeHtml(g.gun_code)}</code></td>
                        <td>#${escapeHtml(g.station_id)} ${escapeHtml(g.station_name)}</td>
                        <td><code>${escapeHtml(g.pile_code)}</code> (${escapeHtml(g.rated_power)}kW)</td>
                        <td>${escapeHtml(g.gun_type)}</td>
                        <td>${escapeHtml(g.connector_standard)}</td>
                        <td>${escapeHtml(g.max_power)}</td>
                        <td>${statusBadge(g.gun_status)}</td>
                    </tr>`
                )
                .join("");
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="8">查询失败：${escapeHtml(err.message)}</td></tr>`;
        }
    }

    // ------------------------------------------------------------------------
    // Tab 2: 运维排障、待办闭环与存储过程
    // ------------------------------------------------------------------------
    async function loadFaults() {
        const tbody = $("faultsBody");
        const qs = new URLSearchParams({ page: "1", page_size: "30" });
        const ps = $("faultPileStatusFilter").value;
        if (ps) qs.set("pile_status", ps);

        try {
            const { data } = await apiFetch(`/api/stations/faults?${qs.toString()}`);
            if (!data.items.length) {
                tbody.innerHTML = '<tr><td colspan="8">当前无故障或离线桩枪记录</td></tr>';
                return;
            }
            tbody.innerHTML = data.items
                .map(
                    (r) => `
                    <tr>
                        <td>#${escapeHtml(r.station_id)} ${escapeHtml(r.station_name)}</td>
                        <td>#${escapeHtml(r.pile_id)} <code>${escapeHtml(r.pile_code)}</code></td>
                        <td>${statusBadge(r.pile_status)}</td>
                        <td>${r.gun_code ? `#${escapeHtml(r.gun_id)} <code>${escapeHtml(r.gun_code)}</code>` : "-"}</td>
                        <td>${r.gun_status ? statusBadge(r.gun_status) : "-"}</td>
                        <td>${r.heartbeat_lag_sec ?? "-"}</td>
                        <td>${escapeHtml(r.service_phone_masked || "-")}</td>
                        <td>
                            <button class="btn btn-outline btn-sm recover-pile-btn" data-pile-id="${escapeHtml(r.pile_id)}" type="button">桩置空闲</button>
                            ${r.gun_id ? `<button class="btn btn-outline btn-sm recover-gun-btn" data-gun-id="${escapeHtml(r.gun_id)}" type="button">枪置空闲</button>` : ""}
                        </td>
                    </tr>`
                )
                .join("");
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="8">无权限或加载失败 (${escapeHtml(err.status || "-")})：${escapeHtml(err.message)}</td></tr>`;
        }
    }

    async function loadStatusLogs() {
        const tbody = $("statusLogsBody");
        const qs = new URLSearchParams({ page: "1", page_size: "30" });
        const dt = $("logDeviceTypeFilter").value;
        const ts = $("logTodoStatusFilter").value;
        if (dt) qs.set("device_type", dt);
        if (ts) qs.set("todo_status", ts);

        try {
            const { data } = await apiFetch(`/api/stations/status-logs?${qs.toString()}`);
            if (!data.items.length) {
                tbody.innerHTML = '<tr><td colspan="9">暂无状态变更日志</td></tr>';
                return;
            }
            tbody.innerHTML = data.items
                .map(
                    (l) => `
                    <tr>
                        <td>${escapeHtml(l.log_id)}</td>
                        <td>${escapeHtml(l.station_name)}</td>
                        <td>${escapeHtml(l.device_type)} · <code>${escapeHtml(l.gun_code || l.pile_code)}</code></td>
                        <td>${escapeHtml(l.prev_status || "NULL")} → ${statusBadge(l.new_status)}</td>
                        <td>${escapeHtml(l.change_source)}</td>
                        <td>${statusBadge(l.todo_status)}</td>
                        <td title="${escapeHtml(l.remark || "")}">${escapeHtml((l.remark || "-").slice(0, 26))}</td>
                        <td>${escapeHtml(String(l.changed_at || "").replace("T", " ").slice(0, 19))}</td>
                        <td>
                            ${
                                l.todo_status === "PENDING"
                                    ? `<button class="btn btn-primary btn-sm resolve-todo-btn" data-log-id="${escapeHtml(l.log_id)}" type="button">办结待办</button>`
                                    : "-"
                            }
                        </td>
                    </tr>`
                )
                .join("");
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="9">无权限或加载失败 (${escapeHtml(err.status || "-")})：${escapeHtml(err.message)}</td></tr>`;
        }
    }

    async function runHeartbeatCheck() {
        try {
            const { data } = await apiFetch("/api/stations/piles/heartbeat-check", {
                method: "POST",
                body: JSON.stringify({ check_time: null }),
            });
            showToast(`${data.message}：标记离线桩 ${data.affected_piles} 台、枪 ${data.affected_guns} 把`);
            loadFaults();
            loadStatusLogs();
        } catch (err) {
            showToast(`巡检失败 (${err.status || "-"})：${err.message}`, true);
        }
    }

    async function commissionSinglePile() {
        const pileId = Number($("opsPileId").value || 0);
        if (!pileId) return;
        try {
            const { data } = await apiFetch(`/api/stations/piles/${pileId}/commission`, {
                method: "POST",
                body: JSON.stringify({ remark: "前端联调工作台现场验收投运" }),
            });
            showToast(`桩 #${data.pile_id} (${data.pile_code}) 已验收投运：${data.acceptance_status} / ${data.current_status}`);
            loadStatusLogs();
        } catch (err) {
            showToast(`验收失败 (${err.status || "-"})：${err.message}`, true);
        }
    }

    async function reportSingleHeartbeat() {
        const pileId = Number($("opsPileId").value || 0);
        if (!pileId) return;
        try {
            const { data } = await apiFetch(`/api/stations/piles/${pileId}/heartbeat`, { method: "POST" });
            showToast(`桩 #${data.pile_id} 心跳已刷新：${data.last_heartbeat_at}`);
        } catch (err) {
            showToast(`上报失败 (${err.status || "-"})：${err.message}`, true);
        }
    }

    // ------------------------------------------------------------------------
    // Tab 3: 全局实时看板与三级资产建档
    // ------------------------------------------------------------------------
    async function loadRealtimeBoard() {
        const kpis = $("realtimeKpis");
        const tbody = $("realtimeBody");
        try {
            const { data } = await apiFetch("/api/stations/realtime-status?page=1&page_size=50");
            let totalPiles = 0;
            let pendingPiles = 0;
            let abnormalPiles = 0;
            let totalPower = 0;
            for (const row of data.items) {
                totalPiles += Number(row.total_piles || 0);
                pendingPiles += Number(row.pending_acceptance_piles || 0);
                abnormalPiles += Number(row.fault_piles || 0) + Number(row.offline_piles || 0);
                totalPower += Number(row.total_rated_power || 0);
            }
            kpis.innerHTML = `
                <div class="kpi-box"><div class="kpi-label">总场站数</div><div class="kpi-value">${escapeHtml(data.total)}</div></div>
                <div class="kpi-box"><div class="kpi-label">装机充电桩</div><div class="kpi-value">${escapeHtml(totalPiles)}</div></div>
                <div class="kpi-box"><div class="kpi-label">待验收桩</div><div class="kpi-value">${escapeHtml(pendingPiles)}</div></div>
                <div class="kpi-box"><div class="kpi-label">故障/离线桩</div><div class="kpi-value" style="color:var(--danger)">${escapeHtml(abnormalPiles)}</div></div>
                <div class="kpi-box"><div class="kpi-label">装机总功率(kW)</div><div class="kpi-value" style="color:var(--primary)">${escapeHtml(totalPower.toFixed(2))}</div></div>
            `;
            tbody.innerHTML = data.items
                .map(
                    (s) => `
                    <tr>
                        <td>${escapeHtml(s.station_id)}</td>
                        <td>${escapeHtml(s.station_name)}</td>
                        <td>${escapeHtml(s.operator_name)}</td>
                        <td>${statusBadge(s.station_status)}</td>
                        <td>${s.total_piles} / ${s.pending_acceptance_piles} / ${s.idle_piles} / ${s.occupied_piles} / ${s.fault_piles} / ${s.offline_piles}</td>
                        <td><strong>${escapeHtml(s.total_rated_power)}</strong></td>
                        <td>${s.total_guns} / ${s.idle_guns} / ${s.occupied_guns} / ${s.fault_guns} / ${s.offline_guns}</td>
                        <td><button class="btn btn-outline btn-sm drill-station-btn" data-station-id="${escapeHtml(s.station_id)}" type="button">查下属桩</button></td>
                    </tr>`
                )
                .join("");
        } catch (err) {
            kpis.innerHTML = "";
            tbody.innerHTML = `<tr><td colspan="8">无权限或加载失败 (${escapeHtml(err.status || "-")})：${escapeHtml(err.message)}</td></tr>`;
        }
    }

    async function loadOperators(summaryOnly = false) {
        const tbody = $("operatorsBody");
        const endpoint = summaryOnly ? "/api/stations/operators/summary" : "/api/stations/operators";
        try {
            const { data } = await apiFetch(endpoint);
            tbody.innerHTML = data
                .map(
                    (op) => `
                    <tr>
                        <td>${escapeHtml(op.operator_id)}</td>
                        <td><code>${escapeHtml(op.operator_code)}</code></td>
                        <td>${escapeHtml(op.operator_name)}</td>
                        <td>${statusBadge(op.status)}</td>
                        <td>${escapeHtml(op.service_phone_masked || op.service_phone || "-")}</td>
                        <td><code>${escapeHtml(op.credit_code || "已脱敏隐藏")}</code></td>
                    </tr>`
                )
                .join("");
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="6">无权限或加载失败 (${escapeHtml(err.status || "-")})：${escapeHtml(err.message)}</td></tr>`;
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
            box.className = "table-wrap";
            box.innerHTML = `
                <table>
                    <thead><tr><th>桩ID</th><th>编码</th><th>型号</th><th>额定功率</th><th>验收</th><th>状态</th><th>枪</th></tr></thead>
                    <tbody>
                        ${data
                            .map(
                                (p) => `
                            <tr>
                                <td>${escapeHtml(p.pile_id)}</td>
                                <td><code>${escapeHtml(p.pile_code)}</code></td>
                                <td>${escapeHtml(p.device_model)}</td>
                                <td>${escapeHtml(p.rated_power)}kW</td>
                                <td>${statusBadge(p.acceptance_status)}</td>
                                <td>${statusBadge(p.current_status)}</td>
                                <td><button class="btn btn-outline btn-sm drill-pile-btn" data-pile-id="${escapeHtml(p.pile_id)}" type="button">查枪</button></td>
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
            box.className = "table-wrap";
            box.innerHTML = `
                <table>
                    <thead><tr><th>枪ID</th><th>编码</th><th>序号</th><th>枪型</th><th>标准</th><th>最大功率</th><th>状态</th></tr></thead>
                    <tbody>
                        ${data
                            .map(
                                (g) => `
                            <tr>
                                <td>${escapeHtml(g.gun_id)}</td>
                                <td><code>${escapeHtml(g.gun_code)}</code></td>
                                <td>${escapeHtml(g.gun_seq)}</td>
                                <td>${escapeHtml(g.gun_type)}</td>
                                <td>${escapeHtml(g.connector_standard)}</td>
                                <td>${escapeHtml(g.max_power)}kW</td>
                                <td>${statusBadge(g.current_status)}</td>
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
    // Tab 4: 跨线联调与 RBAC/约束试探
    // ------------------------------------------------------------------------
    async function probeEndpoint(method, url, body = null) {
        try {
            const res = await apiFetch(url, {
                method,
                body: body ? JSON.stringify(body) : undefined,
            });
            logConsole(method, url, res.status, res.data);
            showToast(`${method} ${url} -> HTTP ${res.status}`);
        } catch (err) {
            logConsole(method, url, err.status || "ERR", err.data || err.message);
            showToast(`${method} ${url} -> HTTP ${err.status || "-"}：${err.message}`, true);
        }
    }

    async function loadCrossPreview(url, title) {
        const box = $("crossPreviewBox");
        try {
            const { data } = await apiFetch(url);
            box.className = "console-box";
            box.textContent = `// ${title}\n` + JSON.stringify(data, null, 2);
        } catch (err) {
            box.className = "empty-hint";
            box.textContent = `${title} 返回 HTTP ${err.status || "-"}：${err.message}`;
        }
    }

    function refreshActiveTabData() {
        const activeTab = document.querySelector(".tab-btn.active")?.dataset.tab;
        if (activeTab === "tab-driver") {
            loadDriverStations(1);
            loadDriverGuns();
        } else if (activeTab === "tab-ops") {
            loadFaults();
            loadStatusLogs();
        } else if (activeTab === "tab-admin") {
            loadRealtimeBoard();
            loadOperators(state.user?.role === "settlement");
        }
    }

    function bindEvents() {
        $("roleButtons").addEventListener("click", (e) => {
            const btn = e.target.closest(".role-btn");
            if (btn) switchDevRole(btn.dataset.role);
        });

        $("toggleLoginBtn").addEventListener("click", () => {
            const panel = $("loginPanel");
            panel.style.display = panel.style.display === "none" ? "block" : "none";
        });
        $("realLoginSubmitBtn").addEventListener("click", realLogin);

        $("mainTabs").addEventListener("click", (e) => {
            const btn = e.target.closest(".tab-btn");
            if (!btn) return;
            document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
            document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
            btn.classList.add("active");
            $(btn.dataset.tab).classList.add("active");
            refreshActiveTabData();
        });

        $("searchStationsBtn").addEventListener("click", () => loadDriverStations(1));
        $("searchGunsBtn").addEventListener("click", loadDriverGuns);
        $("driverStationGrid").addEventListener("click", (e) => {
            const btn = e.target.closest(".view-station-guns");
            if (!btn) return;
            $("gunStationFilter").value = btn.dataset.stationId;
            loadDriverGuns();
        });

        $("heartbeatCheckBtn").addEventListener("click", runHeartbeatCheck);
        $("commissionPileBtn").addEventListener("click", commissionSinglePile);
        $("reportHeartbeatBtn").addEventListener("click", reportSingleHeartbeat);
        $("loadFaultsBtn").addEventListener("click", loadFaults);
        $("loadLogsBtn").addEventListener("click", loadStatusLogs);

        $("faultsBody").addEventListener("click", async (e) => {
            const pileBtn = e.target.closest(".recover-pile-btn");
            const gunBtn = e.target.closest(".recover-gun-btn");
            try {
                if (pileBtn) {
                    const pid = pileBtn.dataset.pileId;
                    await apiFetch(`/api/stations/piles/${pid}/status`, {
                        method: "PATCH",
                        body: JSON.stringify({
                            new_status: "IDLE",
                            change_source: "MANUAL",
                            todo_status: "DONE",
                            remark: "运维工作台恢复充电桩为空闲",
                        }),
                    });
                    showToast(`充电桩 #${pid} 已恢复 IDLE`);
                    loadFaults();
                    loadStatusLogs();
                } else if (gunBtn) {
                    const gid = gunBtn.dataset.gunId;
                    await apiFetch(`/api/stations/guns/${gid}/status`, {
                        method: "PATCH",
                        body: JSON.stringify({
                            new_status: "IDLE",
                            change_source: "MANUAL",
                            todo_status: "DONE",
                            remark: "运维工作台恢复充电枪为空闲",
                        }),
                    });
                    showToast(`充电枪 #${gid} 已恢复 IDLE`);
                    loadFaults();
                    loadStatusLogs();
                }
            } catch (err) {
                showToast(`状态恢复失败 (${err.status || "-"})：${err.message}`, true);
            }
        });

        $("statusLogsBody").addEventListener("click", async (e) => {
            const btn = e.target.closest(".resolve-todo-btn");
            if (!btn) return;
            const lid = btn.dataset.logId;
            try {
                const { data } = await apiFetch(`/api/stations/status-logs/${lid}/resolve-todo`, {
                    method: "POST",
                    body: JSON.stringify({ remark: "运维工作台确认排查并办结" }),
                });
                showToast(`${data.message} (#${data.id})`);
                loadStatusLogs();
            } catch (err) {
                showToast(`办结失败 (${err.status || "-"})：${err.message}`, true);
            }
        });

        $("loadRealtimeBtn").addEventListener("click", loadRealtimeBoard);
        $("loadOperatorsFullBtn").addEventListener("click", () => loadOperators(false));
        $("loadOperatorsSummaryBtn").addEventListener("click", () => loadOperators(true));
        $("drillPilesBtn").addEventListener("click", () => drillPiles(Number($("drillStationId").value || 1)));
        $("drillGunsBtn").addEventListener("click", () => drillGuns(Number($("drillPileId").value || 1)));

        $("realtimeBody").addEventListener("click", (e) => {
            const btn = e.target.closest(".drill-station-btn");
            if (btn) drillPiles(Number(btn.dataset.stationId));
        });
        $("drillResultBox").addEventListener("click", (e) => {
            const btn = e.target.closest(".drill-pile-btn");
            if (btn) drillGuns(Number(btn.dataset.pileId));
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
                loadRealtimeBoard();
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
            try {
                const { data } = await apiFetch("/api/stations/piles", {
                    method: "POST",
                    body: JSON.stringify(payload),
                });
                showToast(`充电桩建档成功：#${data.pile_id} ${data.pile_code}`);
                drillPiles(payload.station_id);
            } catch (err) {
                showToast(`建档失败 (${err.status || "-"})：${err.message}`, true);
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

        $("loadOrdersBtn").addEventListener("click", () => loadCrossPreview("/api/orders?page=1&page_size=10", "GET /api/orders"));
        $("loadBillsBtn").addEventListener("click", () => loadCrossPreview("/api/settlements?page=1&page_size=10", "GET /api/settlements"));
        $("loadDiffsBtn").addEventListener("click", () => loadCrossPreview("/api/settlements/diffs?page=1&page_size=10", "GET /api/settlements/diffs"));

        $("probeFaultsBtn").addEventListener("click", () => probeEndpoint("GET", "/api/stations/faults?page=1&page_size=5"));
        $("probeRealtimeBtn").addEventListener("click", () => probeEndpoint("GET", "/api/stations/realtime-status?page=1&page_size=5"));
        $("probeOperatorsBtn").addEventListener("click", () => probeEndpoint("GET", "/api/stations/operators"));
        $("probeCommission1Btn").addEventListener("click", () =>
            probeEndpoint("POST", "/api/stations/piles/1/commission", { remark: "触发重复投运1644" })
        );
        $("clearConsoleBtn").addEventListener("click", () => {
            $("apiConsole").textContent = "等待请求...";
        });
    }

    async function init() {
        bindEvents();
        await Promise.all([checkDbHealth(), loadRolesMeta()]);
        await restoreSessionOrDefault();
        refreshActiveTabData();
    }

    init();
})();
