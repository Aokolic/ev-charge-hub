(() => {
    "use strict";

    const supportsPopover = Object.hasOwn(HTMLElement.prototype, "popover");

    const state = {
        token: localStorage.getItem("ev_hub_token") || "",
        user: null,
        authMode: localStorage.getItem("ev_hub_auth_mode") || "",
        authEpoch: 0,
        rolesMap: {},
        activePanel: "panel-home",
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

    // 请求并发控制器注册表（按业务目标 key 管理，新请求自动中止未完成的旧请求）
    const abortControllers = new Map();

    function beginRequest(key) {
        const prev = abortControllers.get(key);
        if (prev) prev.abort();
        const controller = new AbortController();
        abortControllers.set(key, controller);
        return controller;
    }

    function finishRequest(key, controller) {
        if (abortControllers.get(key) === controller) {
            abortControllers.delete(key);
        }
    }

    function abortRequestsByPrefix(prefix = "") {
        for (const [key, controller] of abortControllers.entries()) {
            if (!prefix || key.startsWith(prefix)) {
                controller.abort();
                abortControllers.delete(key);
            }
        }
    }

    function isAbortError(err) {
        return Boolean(err && (err.name === "AbortError" || err.code === 20));
    }

    const ROUTES = {
        "#/home": {
            panel: "panel-home",
            title: "总览",
            desc: "按角色查看充电网络的实时状态与业务入口",
        },
        "#/stations": {
            panel: "panel-driver",
            title: "找站与开单",
            desc: "查询营业场站与充电枪可用状态，支持扫码启动充电",
        },
        "#/orders": {
            panel: "panel-orders",
            title: "订单与分时计费",
            desc: "充电订单隔离查看、结束充电分时拆账与支付退款流水",
        },
        "#/ops": {
            panel: "panel-ops",
            title: "运维排障与巡检",
            desc: "全网心跳离线巡检、故障枪/离线桩恢复与设备状态审计日志",
        },
        "#/assets": {
            panel: "panel-admin",
            title: "资产看板与建档",
            desc: "全平台场站实时资产汇总、运营商档案与场站-桩-枪三级钻取建档",
        },
        "#/settle": {
            panel: "panel-settle",
            title: "结算对账与开票",
            desc: "按月唯一结算单生成、未处理对账差异核销与发票开具/红冲",
        },
        "#/lab": {
            panel: "panel-cross",
            title: "权限与规则校验",
            desc: "使用当前登录身份验证跨角色权限边界与业务防呆保护机制",
        },
    };

    const TAB_ACCESS = {
        "panel-home": {
            roles: ["driver", "fleet", "ops", "settlement", "admin"],
            hint: "",
            suggest: [],
        },
        "panel-driver": {
            roles: ["driver", "fleet", "ops", "admin"],
            hint: "找站与开单面向车主、车队、运维与管理员；扫码开单仅限车主或车队角色。",
            suggest: ["driver", "fleet"],
        },
        "panel-orders": {
            roles: ["driver", "fleet", "ops", "settlement", "admin"],
            hint: "全部角色均可查看订单列表（车主与车队仅看本人名下订单，其余角色可看全量）。",
            suggest: ["driver"],
        },
        "panel-ops": {
            roles: ["ops", "admin"],
            hint: "运维排障、心跳巡检与设备状态日志仅限场站运维与平台管理员访问。",
            suggest: ["ops", "admin"],
        },
        "panel-admin": {
            roles: ["admin", "settlement", "ops"],
            hint: "实时资产看板限管理员与结算专员；桩/枪钻取限运维与管理员；资产建档与编辑限管理员。",
            suggest: ["admin", "settlement"],
        },
        "panel-settle": {
            roles: ["settlement", "admin"],
            hint: "月度结算单、对账差异核销与发票开具/红冲仅限结算专员与管理员。",
            suggest: ["settlement", "admin"],
        },
        "panel-cross": {
            roles: ["driver", "fleet", "ops", "settlement", "admin"],
            hint: "支持切换任意身份实测权限边界与业务保护规则。",
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

    const ROLE_AVATAR = {
        driver: "车",
        fleet: "队",
        ops: "维",
        settlement: "结",
        admin: "管",
    };

    const GUN_TYPE_LABELS = {
        DC_FAST: "直流快充",
        AC_SLOW: "交流慢充",
    };

    const DEVICE_TYPE_LABELS = {
        PILE: "充电桩",
        GUN: "充电枪",
    };

    const SOURCE_LABELS = {
        INIT: "建档初始化",
        ACCEPTANCE: "验收投运",
        MANUAL: "人工维护",
        ORDER: "订单联动",
        HEARTBEAT: "心跳巡检",
        ALARM: "告警触发",
    };

    const DIFF_TYPE_LABELS = {
        MISSING_ORDER: "缺失订单",
        AMOUNT_MISMATCH: "金额不符",
        DUPLICATE_PAY: "重复支付",
        UNPAID: "订单未结清",
    };

    const HOME_MODULES = [
        {
            href: "#/stations",
            panel: "panel-driver",
            icon: "i-bolt",
            title: "找站与开单",
            desc: "营业场站概览、快慢充空闲枪筛选与扫码开单",
            roles: ["driver", "fleet", "ops", "admin"],
        },
        {
            href: "#/orders",
            panel: "panel-orders",
            icon: "i-receipt",
            title: "订单与分时计费",
            desc: "充电中结束拆账、峰平谷统计与支付流水（退款在结算面板发起）",
            roles: ["driver", "fleet", "ops", "settlement", "admin"],
        },
        {
            href: "#/ops",
            panel: "panel-ops",
            icon: "i-wrench",
            title: "运维排障与巡检",
            desc: "全网心跳巡检、故障桩枪恢复与待办备注办结",
            roles: ["ops", "admin"],
        },
        {
            href: "#/assets",
            panel: "panel-admin",
            icon: "i-building",
            title: "资产看板与建档",
            desc: "场站实时资产汇总、运营商档案与桩枪三级建档",
            roles: ["admin", "settlement", "ops"],
        },
        {
            href: "#/settle",
            panel: "panel-settle",
            icon: "i-wallet",
            title: "结算对账与开票",
            desc: "按月结算单生成、对账差异核销与发票红冲",
            roles: ["settlement", "admin"],
        },
        {
            href: "#/lab",
            panel: "panel-cross",
            icon: "i-flask",
            title: "权限与规则校验",
            desc: "平台模块上线状态、角色隔离验证与业务防呆保护测试",
            roles: ["driver", "fleet", "ops", "settlement", "admin"],
        },
    ];

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

    function getLocalDateString(d = new Date()) {
        const y = d.getFullYear();
        const m = String(d.getMonth() + 1).padStart(2, "0");
        const day = String(d.getDate()).padStart(2, "0");
        return `${y}-${m}-${day}`;
    }

    function parsePositiveInt(el, label) {
        const raw = String(el?.value ?? "").trim();
        const num = Number(raw);
        if (!raw || !Number.isInteger(num) || num < 1) {
            showToast(`请输入有效的正整数${label}`, true);
            el?.focus();
            return null;
        }
        return num;
    }

    // 格式化 FastAPI / Pydantic v2 返回的错误 detail（兼容 422 校验数组与对象）
    function formatApiErrorDetail(detail, fallback = "请求失败") {
        if (Array.isArray(detail)) {
            const msgs = detail
                .map((item) => {
                    if (typeof item === "string") return item.trim();
                    if (item && typeof item === "object") {
                        const locParts = Array.isArray(item.loc)
                            ? item.loc.filter((part) => part !== "body" && part !== "query" && part !== "path")
                            : [];
                        const field = locParts.length ? String(locParts[locParts.length - 1]) : "";
                        const rawMsg = String(item.msg || item.detail || item.message || "")
                            .replace(/^Value error,\s*/i, "")
                            .trim();
                        if (field && rawMsg) return `${field}: ${rawMsg}`;
                        if (rawMsg) return rawMsg;
                        try {
                            return JSON.stringify(item);
                        } catch {
                            return "";
                        }
                    }
                    return String(item ?? "").trim();
                })
                .filter(Boolean);
            return msgs.length ? msgs.join("；") : fallback;
        }
        if (typeof detail === "string") {
            const trimmed = detail.trim();
            return trimmed || fallback;
        }
        if (detail && typeof detail === "object") {
            if (typeof detail.message === "string" && detail.message.trim()) return detail.message.trim();
            try {
                const json = JSON.stringify(detail);
                return json && json !== "{}" ? json : fallback;
            } catch {
                return fallback;
            }
        }
        return detail !== undefined && detail !== null ? String(detail).trim() || fallback : fallback;
    }

    function normalizeDecimalText(raw) {
        const s = String(raw).trim();
        const match = s.match(/^([+-]?)(\d+)(?:\.(\d*))?[eE]([+-]?\d+)$/);
        if (!match) return s;
        const [, sign, intPart, fracPart = "", expStr] = match;
        const exp = Number(expStr);
        const digits = intPart + fracPart;
        const decimalPos = intPart.length + exp;
        if (decimalPos <= 0) {
            return `${sign === "-" ? "-" : ""}0.${"0".repeat(-decimalPos)}${digits}`;
        }
        if (decimalPos >= digits.length) {
            return `${sign === "-" ? "-" : ""}${digits}${"0".repeat(decimalPos - digits.length)}`;
        }
        return `${sign === "-" ? "-" : ""}${digits.slice(0, decimalPos)}.${digits.slice(decimalPos)}`;
    }

    function sumDecimalStrings(values, scale = 2) {
        const factor = 10n ** BigInt(scale);
        let totalScaled = 0n;
        for (const raw of values) {
            if (raw === null || raw === undefined || raw === "") continue;
            const s = normalizeDecimalText(raw);
            const neg = s.startsWith("-");
            const clean = neg ? s.slice(1) : s.startsWith("+") ? s.slice(1) : s;
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

    function announceStatus(message, isError = false) {
        const target = isError ? $("assertiveAnnouncer") || $("liveAnnouncer") : $("liveAnnouncer");
        if (target) {
            target.textContent = "";
            requestAnimationFrame(() => {
                target.textContent = message;
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
            announceStatus(message, isError);
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
        announceStatus(message, isError);
    }

    // ------------------------------------------------------------------------
    // 原生 Dialog 辅助与降级兼容
    // ------------------------------------------------------------------------
    function closeAllDialogs() {
        document.querySelectorAll("dialog[open]").forEach((dlg) => {
            try {
                dlg.close("abort");
            } catch {
                // 忽略已关闭状态
            }
        });
        currentActionHandler = null;
    }

    function setupDialogs() {
        const supportsClosedBy = "closedBy" in HTMLDialogElement.prototype;

        document.querySelectorAll("dialog").forEach((dlg) => {
            dlg.addEventListener("close", () => {
                dlg.returnValue = "";
                if (dlg.id === "actionDialog") {
                    currentActionHandler = null;
                }
            });

            if (!supportsClosedBy && dlg.getAttribute("closedby") === "any") {
                const isOutside = (e) => {
                    const r = dlg.getBoundingClientRect();
                    return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
                };
                let pressedOutside = false;
                dlg.addEventListener("pointerdown", (e) => {
                    pressedOutside = e.target === dlg && isOutside(e);
                });
                dlg.addEventListener("pointerup", (e) => {
                    if (pressedOutside && e.target === dlg && isOutside(e)) dlg.close();
                    pressedOutside = false;
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
    let sessionExpiring = false;

    async function apiFetch(url, options = {}) {
        const { skipAuthRedirect = false, signal, ...fetchOptions } = options;
        const headers = new Headers(fetchOptions.headers || {});
        if (state.token) {
            headers.set("Authorization", `Bearer ${state.token}`);
        }
        if (fetchOptions.body && !headers.has("Content-Type")) {
            headers.set("Content-Type", "application/json");
        }
        const started = performance.now();
        let resp;
        try {
            resp = await fetch(url, { ...fetchOptions, headers, signal });
        } catch (err) {
            if (isAbortError(err)) throw err;
            // 断网 / DNS 失败 / 连接被拒：统一转成可读中文，避免把 "Failed to fetch" 原样抛给用户
            const netErr = new Error("网络连接失败，请检查网络连接后重试");
            netErr.status = "网络异常";
            netErr.network = true;
            throw netErr;
        }
        const elapsedMs = Math.round(performance.now() - started);
        const text = await resp.text();
        let data = text;
        try {
            data = text ? JSON.parse(text) : {};
        } catch {
            // 保留原始文本
        }
        if (!resp.ok) {
            const fallback = `请求失败 (${resp.status})`;
            const rawDetail =
                typeof data === "object" && data !== null && "detail" in data && data.detail !== undefined && data.detail !== null
                    ? data.detail
                    : typeof data === "string" && data.trim()
                    ? data.trim().slice(0, 200)
                    : null;
            const detail = rawDetail !== null ? formatApiErrorDetail(rawDetail, fallback) : fallback;
            if (
                resp.status === 401 &&
                !skipAuthRedirect &&
                !String(url).startsWith("/api/auth/") &&
                (state.token || state.user)
            ) {
                handleSessionExpired(detail);
            }
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
            IDLE: ["空闲", "badge-ok"],
            OPEN: ["营业中", "badge-ok"],
            ACTIVE: ["正常", "badge-ok"],
            PASSED: ["已验收", "badge-ok"],
            DONE: ["已办结", "badge-ok"],
            PAID: ["已支付", "badge-ok"],
            ISSUED: ["已开具", "badge-ok"],
            CONFIRMED: ["已确认", "badge-ok"],
            INVOICED: ["已开票", "badge-info"],
            OCCUPIED: ["占用", "badge-info"],
            CHARGING: ["充电中", "badge-info"],
            FINISHED: ["待支付", "badge-warn"],
            PENDING: ["待处理", "badge-warn"],
            DRAFT: ["草稿", "badge-warn"],
            MAINTENANCE: ["维护中", "badge-warn"],
            SUSPENDED: ["已暂停", "badge-warn"],
            FAULT: ["故障", "badge-danger"],
            REJECTED: ["未通过", "badge-danger"],
            RED_FLUSHED: ["已红冲", "badge-danger"],
            REFUNDED: ["已退款", "badge-warn"],
            OFFLINE: ["离线", "badge-muted"],
            CLOSED: ["已关闭", "badge-muted"],
            CANCELLED: ["已取消", "badge-muted"],
            NONE: ["无需处理", "badge-muted"],
            PEAK: ["峰时", "badge-warn"],
            FLAT: ["平时", "badge-info"],
            VALLEY: ["谷时", "badge-ok"],
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
            txt.textContent = `服务在线 · MySQL ${data.mysql_version}`;
        } catch (err) {
            dot.className = "status-dot err";
            txt.textContent = `服务异常：${err.message}`;
        }
    }

    async function loadRolesMeta() {
        try {
            const { data } = await apiFetch("/api/auth/roles");
            for (const item of data) {
                state.rolesMap[item.role] = item;
            }
            if (state.user) {
                const roleMeta = state.rolesMap[state.user.role];
                if (roleMeta) {
                    if ($("currentRoleDesc")) $("currentRoleDesc").textContent = roleMeta.description;
                    if ($("homeRoleDesc")) $("homeRoleDesc").textContent = roleMeta.description;
                }
            }
        } catch {
            // 忽略字典拉取失败
        }
    }

    function resetPeriodStatPlaceholder({ force = false } = {}) {
        const box = $("periodStatBox");
        if (!box) return;
        if (!force && box.querySelector("table")) return;
        const role = state.user?.role;
        if (role === "ops" || role === "admin") {
            box.innerHTML = '<div class="empty-hint">请选择日期范围并点击“查询峰平谷统计”。</div>';
        } else {
            box.innerHTML = '<div class="empty-hint">请切换至“运维”或“管理员”身份后查询分时电价统计。</div>';
        }
    }

    function syncRoleActionVisibility() {
        const role = state.user?.role;
        const canStartOrder = role === "driver" || role === "fleet";
        const canOps = role === "ops" || role === "admin";
        const isAdmin = role === "admin";
        const canSeeRealtime = role === "admin" || role === "settlement";
        const canSettle = role === "settlement" || role === "admin";

        const manualOrderBtn = $("openManualOrderBtn");
        if (manualOrderBtn) {
            manualOrderBtn.hidden = !canStartOrder;
            manualOrderBtn.disabled = !canStartOrder;
        }

        ["heartbeatCheckBtn", "commissionPileBtn", "reportHeartbeatBtn", "openPileStatusBtn"].forEach((id) => {
            const btn = $(id);
            if (btn) btn.disabled = !canOps;
        });

        const opFullBtn = $("loadOperatorsFullBtn");
        if (opFullBtn) {
            opFullBtn.hidden = !isAdmin;
            opFullBtn.disabled = !isAdmin;
        }
        const opSummaryBtn = $("loadOperatorsSummaryBtn");
        if (opSummaryBtn) opSummaryBtn.disabled = !canSeeRealtime;
        const createOpBtn = $("openCreateOperatorBtn");
        if (createOpBtn) {
            createOpBtn.hidden = !isAdmin;
            createOpBtn.disabled = !isAdmin;
        }
        const assetDisclosure = $("assetCreateDisclosure");
        if (assetDisclosure) {
            assetDisclosure.hidden = !isAdmin;
            assetDisclosure.querySelectorAll('button[type="submit"]').forEach((btn) => {
                btn.disabled = !isAdmin;
            });
        }

        const drillPilesBtn = $("drillPilesBtn");
        const drillGunsBtn = $("drillGunsBtn");
        if (drillPilesBtn) drillPilesBtn.disabled = !canOps;
        if (drillGunsBtn) drillGunsBtn.disabled = !canOps;

        const genBtn = $("generateBillForm")?.querySelector('button[type="submit"]');
        const billSearchBtn = $("billSearchForm")?.querySelector('button[type="submit"]');
        const rfBtn = $("redFlushForm")?.querySelector('button[type="submit"]');
        const refreshDiffsBtn = $("refreshDiffsBtn");
        if (genBtn) genBtn.disabled = !canSettle;
        if (billSearchBtn) billSearchBtn.disabled = !canSettle;
        if (rfBtn) rfBtn.disabled = !canSettle;
        if (refreshDiffsBtn) refreshDiffsBtn.disabled = !canSettle;

        // 订单退款：后端 /api/orders/{id}/refund 仅结算专员与管理员可发起
        const refundCard = $("orderRefundCard");
        const refundSubmitBtn = $("refundOrderForm")?.querySelector('button[type="submit"]');
        if (refundCard) refundCard.hidden = !canSettle;
        if (refundSubmitBtn) refundSubmitBtn.disabled = !canSettle;

        const statStationsLabel = $("statStationsLabel");
        if (statStationsLabel) {
            statStationsLabel.textContent = role === "settlement" ? "接入场站" : "营业场站";
        }
    }

    function resetWorkspacePaginationAndSession({ clearInvoices = true } = {}) {
        Object.keys(state.pages).forEach((k) => {
            state.pages[k] = 1;
        });
        state.stationCache.clear();
        state.operatorCache.clear();
        if (clearInvoices) {
            state.sessionInvoices = [];
            if ($("sessionInvoicesList")) renderSessionInvoices();
        }
        const drillBox = $("drillResultBox");
        if (drillBox) {
            drillBox.className = "empty-hint";
            drillBox.textContent = "输入场站 ID 或桩 ID，逐级查看下属设备。";
        }
        // 切换角色 / 切换账号时清掉上一次操作留下的行内错误
        setInlineAlert($("refundOrderError"), "");
        setInlineAlert($("payOrderError"), "");
        setInlineAlert($("finishOrderError"), "");
        resetPeriodStatPlaceholder({ force: true });
    }

    function setRoleButtonsDisabled(disabled) {
        document.querySelectorAll(".role-pill, .auth-role-btn, .quick-role-btn").forEach((btn) => {
            btn.disabled = disabled;
        });
        const reissueBtn = $("reissueRoleBtn");
        if (reissueBtn) reissueBtn.disabled = disabled;
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
                    `<button class="btn btn-outline btn-xs quick-role-btn" data-role="${escapeHtml(r)}" type="button">切换为 ${escapeHtml(ROLE_LABELS[r] || r)}</button>`
            )
            .join("");
        banner.hidden = false;
    }

    function closeIdentityPopover() {
        const pop = $("identityPopover");
        if (!pop) return;
        if (supportsPopover) {
            try {
                pop.hidePopover();
            } catch {
                // 已关闭时忽略状态异常
            }
        } else {
            pop.classList.remove(":popover-open");
        }
    }

    function renderUserContext() {
        const chip = $("identityChipText");
        const avatar = $("identityAvatar");
        const label = $("currentUserLabel");
        const modeBadge = $("currentAuthModeBadge");
        const accountMeta = $("currentAccountMeta");
        const desc = $("currentRoleDesc");
        const popoverBtn = $("identityPopoverBtn");
        const topbarLoginBtn = $("topbarLoginBtn");
        const switchBtn = $("switchAccountBtn");
        const logoutBtn = $("logoutBtn");
        const devDetails = $("devContextDetails");
        const currentRole = state.user?.role;

        document.querySelectorAll("#roleButtons .role-pill").forEach((btn) => {
            const active = Boolean(state.user && btn.dataset.role === state.user.role);
            btn.setAttribute("aria-pressed", active ? "true" : "false");
        });

        document.querySelectorAll("#mainNav .nav-link[data-panel]").forEach((link) => {
            const cfg = TAB_ACCESS[link.dataset.panel];
            const allowed = !cfg || !currentRole || cfg.roles.includes(currentRole);
            link.classList.toggle("is-locked", !allowed);
        });

        if (!state.user) {
            if (popoverBtn) popoverBtn.hidden = true;
            if (topbarLoginBtn) topbarLoginBtn.hidden = false;
            if (logoutBtn) logoutBtn.hidden = true;
            if (devDetails) devDetails.hidden = true;
            if (switchBtn) {
                switchBtn.textContent = "去登录 / 注册";
                switchBtn.className = "btn btn-primary btn-sm";
            }
            if (chip) chip.textContent = "未登录";
            if (avatar) {
                avatar.textContent = "?";
                avatar.removeAttribute("data-role");
            }
            if (label) label.textContent = "未登录";
            if (modeBadge) {
                modeBadge.textContent = "未登录";
                modeBadge.className = "badge badge-muted";
            }
            if (accountMeta) accountMeta.textContent = "";
            if (desc) desc.textContent = "请先登录平台账号或选择演示角色进入工作台。";
            syncRoleActionVisibility();
            renderRoleNotice();
            renderHomeOverview();
            return;
        }

        if (popoverBtn) popoverBtn.hidden = false;
        if (topbarLoginBtn) topbarLoginBtn.hidden = true;
        if (logoutBtn) logoutBtn.hidden = false;
        if (devDetails) devDetails.hidden = false;
        if (switchBtn) {
            switchBtn.textContent = "切换 / 登录账号";
            switchBtn.className = "btn btn-outline btn-sm";
        }

        const roleMeta = state.rolesMap[state.user.role];
        const fleetNote = state.user.fleet_id ? ` · 车队 #${state.user.fleet_id}` : "";
        const isDemo =
            state.authMode === "demo" ||
            (!state.authMode && String(state.user.username || "").startsWith("demo_"));

        if (chip) {
            chip.textContent = isDemo
                ? `${state.user.role_label} · #${state.user.user_id}${fleetNote}`
                : `${state.user.username} · ${state.user.role_label}`;
        }
        if (avatar) {
            avatar.textContent = ROLE_AVATAR[state.user.role] || "用";
            avatar.dataset.role = state.user.role;
        }
        if (label) label.textContent = state.user.role_label;
        if (modeBadge) {
            modeBadge.textContent = isDemo ? "演示身份" : "正式账号";
            modeBadge.className = isDemo ? "badge badge-info" : "badge badge-ok";
        }
        if (accountMeta) {
            const phoneNote = state.user.phone_masked ? ` · ${state.user.phone_masked}` : "";
            accountMeta.textContent = `账号 ${state.user.username} · #${state.user.user_id}${fleetNote}${phoneNote}`;
        }
        if (desc) desc.textContent = roleMeta ? roleMeta.description : "";

        // 仅在演示身份下同步演示联调输入框，避免正式账号登录覆盖调试 UID
        if (isDemo) {
            $("devUserId").value = state.user.user_id;
            if (state.user.fleet_id) {
                $("devFleetId").value = state.user.fleet_id;
            }
        }
        syncRoleActionVisibility();
        renderRoleNotice();
        renderHomeOverview();
    }

    function syncThemeToggles() {
        const pressed = document.documentElement.dataset.theme === "dark" ? "true" : "false";
        $("themeToggle")?.setAttribute("aria-pressed", pressed);
        $("authThemeToggle")?.setAttribute("aria-pressed", pressed);
    }

    function toggleTheme() {
        const cur = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
        const next = cur === "dark" ? "light" : "dark";
        document.documentElement.dataset.theme = next;
        localStorage.setItem("ev_hub_theme", next);
        syncThemeToggles();
    }

    function authModeFromHash(hash) {
        if (hash === "#/register") return "register";
        if (hash === "#/login") return "login";
        return null;
    }

    function switchAuthMode(mode) {
        const isLogin = mode !== "register";
        $("loginForm").hidden = !isLogin;
        $("registerForm").hidden = isLogin;
        $("authQuickWrap").hidden = !isLogin;
        $("authLoginSwitch").hidden = !isLogin;
        $("authRegisterSwitch").hidden = isLogin;
        $("authHeading").textContent = isLogin ? "欢迎回来" : "创建平台账号";
        if (isLogin && state.token && state.user) {
            $("authSubheading").textContent = `当前已登录 ${state.user.username}（${state.user.role_label}），可切换其他账号`;
        } else {
            $("authSubheading").textContent = isLogin
                ? "登录您的 EV Charge Hub 账号"
                : "自助注册普通车主账号";
        }
        setInlineAlert($("authError"), "");
    }

    function showAuthView(mode = "login", { updateHash = true } = {}) {
        const resolved = mode === "register" ? "register" : "login";
        $("appShell").hidden = true;
        $("authView").hidden = false;
        switchAuthMode(resolved);
        $("backToWorkspaceBtn").hidden = !Boolean(state.token && state.user);
        syncThemeToggles();

        if (updateHash) {
            const targetHash = resolved === "register" ? "#/register" : "#/login";
            if (location.hash !== targetHash) {
                history.replaceState(null, "", targetHash);
            }
        }
    }

    function hideAuthView({ updateHash = true, fetchData = true } = {}) {
        $("authView").hidden = true;
        $("appShell").hidden = false;
        syncThemeToggles();
        activatePanel(state.activePanel || "panel-home", { updateHash, fetchData });
    }

    function handleSessionExpired(reason = "") {
        if (sessionExpiring) return;
        sessionExpiring = true;
        closeAllDialogs();
        closeIdentityPopover();
        state.authEpoch += 1;
        abortRequestsByPrefix("panel:");
        abortRequestsByPrefix("modal:");
        abortRequestsByPrefix("auth:");
        state.token = "";
        state.user = null;
        state.authMode = "";
        localStorage.removeItem("ev_hub_token");
        localStorage.removeItem("ev_hub_auth_mode");
        resetWorkspacePaginationAndSession();
        renderUserContext();
        showAuthView("login", { updateHash: true });
        setInlineAlert($("authError"), "登录状态已失效，请重新登录或选择演示角色进入。");
        showToast(reason ? `登录已过期：${reason}` : "登录状态已失效，请重新登录", true);
    }

    function logoutUser() {
        sessionExpiring = false;
        closeAllDialogs();
        closeIdentityPopover();
        state.authEpoch += 1;
        abortRequestsByPrefix("panel:");
        abortRequestsByPrefix("modal:");
        abortRequestsByPrefix("auth:");
        state.token = "";
        state.user = null;
        state.authMode = "";
        localStorage.removeItem("ev_hub_token");
        localStorage.removeItem("ev_hub_auth_mode");
        resetWorkspacePaginationAndSession();
        renderUserContext();
        showAuthView("login", { updateHash: true });
        showToast("已退出登录");
    }

    // 进入登录 / 注册页前清空身份，避免"点了切换账号、顶栏还显示上一个账号"
    function resetSessionForAuthView() {
        sessionExpiring = false;
        closeAllDialogs();
        closeIdentityPopover();
        state.authEpoch += 1;
        abortRequestsByPrefix("panel:");
        abortRequestsByPrefix("modal:");
        abortRequestsByPrefix("auth:");
        state.token = "";
        state.user = null;
        state.authMode = "";
        localStorage.removeItem("ev_hub_token");
        localStorage.removeItem("ev_hub_auth_mode");
        resetWorkspacePaginationAndSession();
        renderUserContext();
    }

    async function switchDevRole(role, { fetchData = true } = {}) {
        const rawUid = Number($("devUserId").value);
        const userId = Number.isFinite(rawUid) && rawUid >= 1 ? Math.trunc(rawUid) : 9001;
        const rawFid = Number($("devFleetId").value);
        const fleetId = Number.isFinite(rawFid) && rawFid >= 1 ? Math.trunc(rawFid) : 1;

        const controller = beginRequest("auth:switchRole");
        const startEpoch = state.authEpoch;
        abortRequestsByPrefix("panel:");
        abortRequestsByPrefix("modal:");
        setRoleButtonsDisabled(true);

        try {
            const { data } = await apiFetch("/api/auth/dev-token", {
                method: "POST",
                signal: controller.signal,
                body: JSON.stringify({
                    user_id: userId,
                    username: `demo_${role}`,
                    role,
                    fleet_id: role === "fleet" ? fleetId : null,
                }),
            });
            if (controller.signal.aborted || startEpoch !== state.authEpoch) {
                return false;
            }
            sessionExpiring = false;
            state.authEpoch += 1;
            abortRequestsByPrefix("panel:");
            abortRequestsByPrefix("modal:");
            state.token = data.access_token;
            state.user = data.user;
            state.authMode = "demo";
            localStorage.setItem("ev_hub_token", state.token);
            localStorage.setItem("ev_hub_auth_mode", "demo");
            resetWorkspacePaginationAndSession();
            renderUserContext();
            showToast(`已切换为 ${data.user.role_label}（#${data.user.user_id}）`);
            if (fetchData) {
                refreshActivePanelData();
            }
            return true;
        } catch (err) {
            if (isAbortError(err) || startEpoch !== state.authEpoch) {
                return false;
            }
            showToast(err.message, true);
            if ($("authView") && !$("authView").hidden) {
                setInlineAlert($("authError"), `演示角色登录失败：${err.message}`);
            }
            return false;
        } finally {
            finishRequest("auth:switchRole", controller);
            if (!abortControllers.has("auth:switchRole")) {
                setRoleButtonsDisabled(false);
            }
        }
    }

    async function restoreSession() {
        if (!state.token) return { ok: false, reason: "empty" };
        const controller = beginRequest("auth:restore");
        const epoch = state.authEpoch;
        try {
            const { data } = await apiFetch("/api/auth/me", {
                signal: controller.signal,
                skipAuthRedirect: true,
            });
            if (controller.signal.aborted || epoch !== state.authEpoch) {
                return { ok: false, reason: "aborted" };
            }
            sessionExpiring = false;
            state.user = data;
            const savedMode = localStorage.getItem("ev_hub_auth_mode");
            state.authMode =
                savedMode === "account" || savedMode === "demo"
                    ? savedMode
                    : String(data.username || "").startsWith("demo_")
                    ? "demo"
                    : "account";
            renderUserContext();
            return { ok: true };
        } catch (err) {
            if (isAbortError(err) || epoch !== state.authEpoch) {
                return { ok: false, reason: "aborted" };
            }
            // 仅在 401/403 凭证无效或账号被禁时清理本地 JWT；500/网络异常保留凭证
            if (err.status === 401 || err.status === 403) {
                state.token = "";
                state.user = null;
                state.authMode = "";
                localStorage.removeItem("ev_hub_token");
                localStorage.removeItem("ev_hub_auth_mode");
                return { ok: false, reason: "invalid", err };
            }
            return { ok: false, reason: "transient", err };
        } finally {
            finishRequest("auth:restore", controller);
        }
    }

    // ------------------------------------------------------------------------
    // Page 0: 总览页与充电枪实时阵列
    // ------------------------------------------------------------------------
    function renderHomeOverview() {
        const role = state.user?.role;
        const greeting = $("homeGreeting");
        const roleDesc = $("homeRoleDesc");
        const ordersLabel = $("statOrdersLabel");
        const quickActions = $("homeQuickActions");
        const modulesBox = $("homeModules");

        if (greeting) {
            greeting.textContent = state.user
                ? `${state.user.role_label}（账号 #${state.user.user_id}）`
                : "未登录访客";
        }
        if (roleDesc) {
            roleDesc.textContent = state.user
                ? state.rolesMap[role]?.description || "当前身份权限已就绪，可通过下方模块卡片进入对应工作台。"
                : "请在顶部选择角色身份，或点击右上角登录账号。";
        }
        if (ordersLabel) {
            ordersLabel.textContent = role === "driver" || role === "fleet" ? "我的订单" : "平台订单";
        }

        if (quickActions) {
            const actionMap = {
                driver: [
                    { href: "#/stations", label: "找站扫码开单", primary: true },
                    { href: "#/orders", label: "查看我的订单", primary: false },
                ],
                fleet: [
                    { href: "#/stations", label: "找站扫码开单", primary: true },
                    { href: "#/orders", label: "查看车队订单", primary: false },
                ],
                ops: [
                    { href: "#/ops", label: "运维排障与巡检", primary: true },
                    { href: "#/assets", label: "桩枪三级钻取", primary: false },
                ],
                settlement: [
                    { href: "#/settle", label: "月度结算与开票", primary: true },
                    { href: "#/assets", label: "场站实时看板", primary: false },
                ],
                admin: [
                    { href: "#/assets", label: "全平台资产看板", primary: true },
                    { href: "#/ops", label: "运维巡检", primary: false },
                    { href: "#/settle", label: "结算对账", primary: false },
                ],
            };
            const list = actionMap[role] || [{ href: "#/lab", label: "进入权限校验", primary: true }];
            quickActions.innerHTML = list
                .map(
                    (a) =>
                        `<a class="btn btn-sm ${a.primary ? "btn-primary" : "btn-outline"}" href="${escapeHtml(a.href)}">${escapeHtml(a.label)}</a>`
                )
                .join("");
        }

        if (modulesBox) {
            modulesBox.innerHTML = HOME_MODULES.map((m) => {
                const allowed = !role || m.roles.includes(role);
                const lockBadge = allowed
                    ? ""
                    : `<span class="module-lock"><svg class="icon" aria-hidden="true"><use href="#i-lock"/></svg>当前身份受限</span>`;
                const chipsHtml = m.roles
                    .map(
                        (r) =>
                            `<span class="role-chip ${r === role ? "is-current" : ""}">${escapeHtml(ROLE_LABELS[r] || r)}</span>`
                    )
                    .join("");
                return `
                    <li>
                        <a class="module-card ${allowed ? "" : "is-locked"}" href="${escapeHtml(m.href)}">
                            <div class="module-head-row">
                                <div class="module-title">
                                    <svg class="icon" aria-hidden="true"><use href="#${escapeHtml(m.icon)}"/></svg>
                                    <span>${escapeHtml(m.title)}</span>
                                </div>
                                ${lockBadge}
                            </div>
                            <p class="module-desc">${escapeHtml(m.desc)}</p>
                            <div class="module-foot">
                                <div class="module-roles">${chipsHtml}</div>
                                <span class="module-enter">进入模块 →</span>
                            </div>
                        </a>
                    </li>`;
            }).join("");
        }
    }

    async function loadHomeData() {
        const matrixBox = $("gunMatrix");
        const statStations = $("statStations");
        const statGuns = $("statGuns");
        const statOrders = $("statOrders");
        const role = state.user?.role;
        const epoch = state.authEpoch;

        const updateLegend = (counts) => {
            ["IDLE", "OCCUPIED", "FAULT", "OFFLINE"].forEach((st) => {
                const el = document.querySelector(`#gunMatrixLegend [data-legend="${st}"]`);
                if (el) el.textContent = String(counts[st] || 0);
            });
        };

        // 1. 订单总数
        if (state.user) {
            const ordersCtrl = beginRequest("panel:home:orders");
            apiFetch("/api/orders?page=1&page_size=1", { signal: ordersCtrl.signal })
                .then(({ data }) => {
                    if (ordersCtrl.signal.aborted || epoch !== state.authEpoch) return;
                    if (statOrders) statOrders.textContent = String(data?.total ?? 0);
                })
                .catch((err) => {
                    if (isAbortError(err) || epoch !== state.authEpoch) return;
                    if (statOrders) statOrders.textContent = "—";
                })
                .finally(() => finishRequest("panel:home:orders", ordersCtrl));
        } else if (statOrders) {
            statOrders.textContent = "—";
        }

        if (!matrixBox) return;
        if (!state.user) {
            if (statStations) statStations.textContent = "—";
            if (statGuns) statGuns.textContent = "—";
            updateLegend({ IDLE: 0, OCCUPIED: 0, FAULT: 0, OFFLINE: 0 });
            matrixBox.innerHTML = '<div class="empty-hint">请先在顶栏切换任意角色，以加载充电枪实时阵列。</div>';
            return;
        }

        const statusMap = {
            IDLE: { cls: "s-idle", text: "空闲" },
            OCCUPIED: { cls: "s-occupied", text: "占用" },
            FAULT: { cls: "s-fault", text: "故障" },
            OFFLINE: { cls: "s-offline", text: "离线" },
        };

        const controller = beginRequest("panel:home:main");
        try {
            if (role === "settlement") {
                // 结算专员无 /api/stations/overview 权限，改用 /api/stations/realtime-status 合成阵列
                const { data } = await apiFetch("/api/stations/realtime-status?page=1&page_size=20", {
                    signal: controller.signal,
                });
                if (controller.signal.aborted || epoch !== state.authEpoch) return;
                const items = data?.items || [];
                const totalGuns = items.reduce((acc, s) => acc + Number(s.total_guns || 0), 0);
                if (statStations) statStations.textContent = String(data?.total ?? 0);
                if (statGuns) statGuns.textContent = String(totalGuns);

                const counts = { IDLE: 0, OCCUPIED: 0, FAULT: 0, OFFLINE: 0 };
                const rowsHtml = items
                    .filter((s) => Number(s.total_guns || 0) > 0)
                    .map((s) => {
                        const idleCnt = Number(s.idle_guns || 0);
                        const occCnt = Number(s.occupied_guns || 0);
                        const fltCnt = Number(s.fault_guns || 0);
                        const offCnt = Number(s.offline_guns || 0);
                        counts.IDLE += idleCnt;
                        counts.OCCUPIED += occCnt;
                        counts.FAULT += fltCnt;
                        counts.OFFLINE += offCnt;

                        const seqList = [
                            ...Array(idleCnt).fill("IDLE"),
                            ...Array(occCnt).fill("OCCUPIED"),
                            ...Array(fltCnt).fill("FAULT"),
                            ...Array(offCnt).fill("OFFLINE"),
                        ];
                        const cellsHtml = seqList
                            .map((st, idx) => {
                                const info = statusMap[st] || statusMap.OFFLINE;
                                const seqLabel = String(idx + 1).padStart(2, "0");
                                return `<span class="gun-cell ${info.cls}" title="${escapeHtml(s.station_name)} · #${escapeHtml(idx + 1)} · ${escapeHtml(info.text)}">${escapeHtml(seqLabel)}</span>`;
                            })
                            .join("");

                        return `
                            <div class="matrix-row">
                                <div class="matrix-station" title="${escapeHtml(s.station_name)}">
                                    ${escapeHtml(s.station_name)}
                                    <small>${escapeHtml(s.station_code)}</small>
                                </div>
                                <div class="matrix-cells">${cellsHtml}</div>
                                <div class="matrix-count"><b>${escapeHtml(idleCnt)}</b> / ${escapeHtml(seqList.length)} 闲</div>
                            </div>`;
                    })
                    .join("");

                updateLegend(counts);
                matrixBox.innerHTML =
                    rowsHtml || '<div class="empty-hint">当前暂无可展示的充电枪状态数据。</div>';
                return;
            }

            const [stationsResp, gunsResp] = await Promise.all([
                apiFetch("/api/stations/overview?page=1&page_size=20", { signal: controller.signal }),
                apiFetch("/api/stations/guns/availability?page=1&page_size=100", { signal: controller.signal }),
            ]);
            if (controller.signal.aborted || epoch !== state.authEpoch) return;

            const stationItems = stationsResp.data?.items || [];
            const gunItems = gunsResp.data?.items || [];

            if (statStations) statStations.textContent = String(stationsResp.data?.total ?? 0);
            if (statGuns) statGuns.textContent = String(gunsResp.data?.total ?? 0);

            const stationMap = new Map();
            for (const s of stationItems) {
                stationMap.set(s.station_id, {
                    station_id: s.station_id,
                    station_code: s.station_code,
                    station_name: s.station_name,
                    guns: [],
                });
            }
            const counts = { IDLE: 0, OCCUPIED: 0, FAULT: 0, OFFLINE: 0 };
            for (const g of gunItems) {
                if (Object.hasOwn(counts, g.gun_status)) {
                    counts[g.gun_status] += 1;
                }
                if (!stationMap.has(g.station_id)) {
                    stationMap.set(g.station_id, {
                        station_id: g.station_id,
                        station_code: g.station_code,
                        station_name: g.station_name,
                        guns: [],
                    });
                }
                stationMap.get(g.station_id).guns.push(g);
            }

            updateLegend(counts);

            const canOrder = role === "driver" || role === "fleet";
            const rowsHtml = Array.from(stationMap.values())
                .filter((st) => st.guns.length > 0)
                .map((st) => {
                    st.guns.sort((a, b) => (a.pile_id - b.pile_id) || (a.gun_seq - b.gun_seq) || (a.gun_id - b.gun_id));
                    let idleCnt = 0;
                    const cellsHtml = st.guns
                        .map((g) => {
                            if (g.gun_status === "IDLE") idleCnt += 1;
                            const info = statusMap[g.gun_status] || statusMap.OFFLINE;
                            const typeLabel = g.gun_type === "DC_FAST" ? "直流快充" : "交流慢充";
                            const tip = `${g.gun_code} · ${typeLabel} ${g.max_power}kW · ${info.text}`;
                            const actionHint = g.gun_status === "IDLE" && canOrder ? "，点击扫码开单" : "，点击查看场站充电枪";
                            const ariaLabel = `${st.station_name} ${tip}${actionHint}`;
                            return `<button type="button" class="gun-cell ${info.cls}" data-gun-id="${escapeHtml(g.gun_id)}" data-station-id="${escapeHtml(g.station_id)}" data-station-name="${escapeHtml(st.station_name)}" data-gun-code="${escapeHtml(g.gun_code)}" data-gun-status="${escapeHtml(g.gun_status)}" title="${escapeHtml(tip)}" aria-label="${escapeHtml(ariaLabel)}">${escapeHtml(String(g.gun_seq).padStart(2, "0"))}</button>`;
                        })
                        .join("");

                    return `
                        <div class="matrix-row">
                            <div class="matrix-station" title="${escapeHtml(st.station_name)}">
                                ${escapeHtml(st.station_name)}
                                <small>${escapeHtml(st.station_code)}</small>
                            </div>
                            <div class="matrix-cells">${cellsHtml}</div>
                            <div class="matrix-count"><b>${escapeHtml(idleCnt)}</b> / ${escapeHtml(st.guns.length)} 闲</div>
                        </div>`;
                })
                .join("");

            matrixBox.innerHTML = rowsHtml || '<div class="empty-hint">暂无营业中的充电枪状态数据。</div>';
        } catch (err) {
            if (isAbortError(err) || epoch !== state.authEpoch) return;
            if (statStations) statStations.textContent = "—";
            if (statGuns) statGuns.textContent = "—";
            updateLegend({ IDLE: 0, OCCUPIED: 0, FAULT: 0, OFFLINE: 0 });
            matrixBox.innerHTML = `<div class="empty-hint">加载充电枪阵列失败 (${escapeHtml(err.status || "-")})：${escapeHtml(err.message)}</div>`;
        } finally {
            finishRequest("panel:home:main", controller);
        }
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

        const controller = beginRequest("panel:stations");
        const epoch = state.authEpoch;
        try {
            const { data } = await apiFetch(`/api/stations/overview?${qs.toString()}`, {
                signal: controller.signal,
            });
            if (controller.signal.aborted || epoch !== state.authEpoch) return;
            if (!data.items.length && page > 1 && Number(data.total || 0) > 0) {
                const clampedPage = Math.max(1, Math.ceil(Number(data.total) / state.pageSize.stations));
                if (clampedPage !== page) return loadDriverStations(clampedPage);
            }
            if (!data.items.length) {
                grid.innerHTML = '<div class="empty-hint">未找到符合条件的营业场站。</div>';
                pager.innerHTML = "";
                return;
            }
            grid.innerHTML = data.items
                .map((s) => {
                    const spacePct = pctRatio(s.available_spaces, s.total_spaces);
                    const gunPct = pctRatio(s.idle_gun_count, s.total_gun_count);
                    const hasFast = Number(s.idle_fast_gun_count) > 0;
                    const statusTag = hasFast
                        ? `<span class="badge badge-ok">快充可用</span>`
                        : `<span class="badge badge-muted">慢充为主</span>`;
                    return `
                        <article class="station-card">
                            <div class="station-card-head">
                                <div class="station-title-row">
                                    <h3>${escapeHtml(s.station_name)}</h3>
                                    ${statusTag}
                                </div>
                                <p class="meta">${escapeHtml(s.station_code)} · ${escapeHtml(s.operator_name)}</p>
                            </div>
                            <div class="station-card-body">
                                <p class="station-addr">${escapeHtml(s.address)}</p>
                                <div class="station-metrics">
                                    <div class="station-metric">
                                        <span>空闲快充</span>
                                        <strong>${escapeHtml(s.idle_fast_gun_count)}</strong>
                                    </div>
                                    <div class="station-metric">
                                        <span>空闲慢充</span>
                                        <strong>${escapeHtml(s.idle_slow_gun_count)}</strong>
                                    </div>
                                    <div class="station-metric">
                                        <span>活跃充电桩</span>
                                        <strong>${escapeHtml(s.active_pile_count)} 台</strong>
                                    </div>
                                </div>
                                <div class="station-meters">
                                    <div class="meter-row">
                                        <div class="meter-label">
                                            <span>空闲充电枪</span>
                                            <strong>${escapeHtml(s.idle_gun_count)} / ${escapeHtml(s.total_gun_count)}</strong>
                                        </div>
                                        <div class="meter-track"><div class="meter-fill" style="--pct:${gunPct}%"></div></div>
                                    </div>
                                    <div class="meter-row">
                                        <div class="meter-label">
                                            <span>空闲车位</span>
                                            <strong>${escapeHtml(s.available_spaces)} / ${escapeHtml(s.total_spaces)}</strong>
                                        </div>
                                        <div class="meter-track"><div class="meter-fill meter-blue" style="--pct:${spacePct}%"></div></div>
                                    </div>
                                </div>
                            </div>
                            <div class="station-card-foot">
                                <span class="meta">停车 ¥${escapeHtml(s.parking_fee_rate)}/h · ${escapeHtml(s.business_hours)}</span>
                                <button class="btn btn-outline btn-xs view-station-guns" data-station-id="${escapeHtml(s.station_id)}" type="button">查看可用枪</button>
                            </div>
                        </article>`;
                })
                .join("");

            renderPagination(pager, data, (nextPage) => loadDriverStations(nextPage));
        } catch (err) {
            if (isAbortError(err) || epoch !== state.authEpoch) return;
            grid.innerHTML = `<div class="empty-hint">无法加载场站概览 (${escapeHtml(err.status || "-")})：${escapeHtml(err.message)}</div>`;
            pager.innerHTML = "";
        } finally {
            finishRequest("panel:stations", controller);
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

        const controller = beginRequest("panel:guns");
        const epoch = state.authEpoch;
        try {
            const { data } = await apiFetch(`/api/stations/guns/availability?${qs.toString()}`, {
                signal: controller.signal,
            });
            if (controller.signal.aborted || epoch !== state.authEpoch) return;
            if (!data.items.length && page > 1 && Number(data.total || 0) > 0) {
                const clampedPage = Math.max(1, Math.ceil(Number(data.total) / state.pageSize.guns));
                if (clampedPage !== page) return loadDriverGuns(clampedPage);
            }
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
                            <td data-label="枪型">${escapeHtml(GUN_TYPE_LABELS[g.gun_type] || g.gun_type)}</td>
                            <td data-label="接口标准">${escapeHtml(g.connector_standard)}</td>
                            <td data-label="最大功率 (kW)" class="num">${escapeHtml(g.max_power)}</td>
                            <td data-label="当前状态">${statusBadge(g.gun_status)}</td>
                            <td data-label="操作" class="actions-cell">
                                ${
                                    canOrder
                                        ? `<button
                                            class="btn ${orderBtnCls} btn-xs start-gun-order-btn"
                                            data-station-id="${escapeHtml(g.station_id)}"
                                            data-station-name="${escapeHtml(g.station_name)}"
                                            data-gun-id="${escapeHtml(g.gun_id)}"
                                            data-gun-code="${escapeHtml(g.gun_code)}"
                                            type="button"
                                        >${orderBtnLabel}</button>`
                                        : '<span class="meta">仅限车主/车队</span>'
                                }
                            </td>
                        </tr>`;
                })
                .join("");

            renderPagination(pager, data, (nextPage) => loadDriverGuns(nextPage));
        } catch (err) {
            if (isAbortError(err) || epoch !== state.authEpoch) return;
            tbody.innerHTML = `<tr><td colspan="9"><div class="empty-hint">查询失败 (${escapeHtml(err.status || "-")})：${escapeHtml(err.message)}</div></td></tr>`;
            pager.innerHTML = "";
        } finally {
            finishRequest("panel:guns", controller);
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
            : `手动指定场站 #${stationId} · 充电枪 #${gunId}（正在充电中的枪不可重复开单）`;
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
            scopeDesc.textContent = isPersonal
                ? `当前仅展示账号 #${state.user.user_id} 本人名下的充电订单与计费状态`
                : "当前展示全平台充电订单与实时计费状态";
        }
        resetPeriodStatPlaceholder();

        const qs = new URLSearchParams({
            page: String(page),
            page_size: String(state.pageSize.orders),
        });

        const controller = beginRequest("panel:orders");
        const epoch = state.authEpoch;
        try {
            const { data } = await apiFetch(`/api/orders?${qs.toString()}`, {
                signal: controller.signal,
            });
            if (controller.signal.aborted || epoch !== state.authEpoch) return;
            if (!data.items.length && page > 1 && Number(data.total || 0) > 0) {
                const clampedPage = Math.max(1, Math.ceil(Number(data.total) / state.pageSize.orders));
                if (clampedPage !== page) return loadOrders(clampedPage);
            }
            if (!data.items.length) {
                tbody.innerHTML = '<tr><td colspan="10"><div class="empty-hint">当前视角下暂无充电订单（可在“找站与开单”扫码启动充电，或在右上角将用户编号切为 9001 查看示例订单）。</div></td></tr>';
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
                            <td data-label="状态">${statusBadge(o.status)}</td>
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
                                            ? `<button class="btn btn-outline btn-xs pay-order-btn" data-order-id="${escapeHtml(o.order_id)}" data-order-no="${escapeHtml(o.order_no)}" data-unpaid="${escapeHtml(o.unpaid_amount)}" data-total="${escapeHtml(o.total_amount)}" type="button">支付</button>`
                                            : ""
                                    }
                                </div>
                            </td>
                        </tr>`;
                })
                .join("");

            renderPagination(pager, data, (nextPage) => loadOrders(nextPage));
        } catch (err) {
            if (isAbortError(err) || epoch !== state.authEpoch) return;
            tbody.innerHTML = `<tr><td colspan="10"><div class="empty-hint">加载订单失败 (${escapeHtml(err.status || "-")})：${escapeHtml(err.message)}</div></td></tr>`;
            pager.innerHTML = "";
        } finally {
            finishRequest("panel:orders", controller);
        }
    }

    async function showOrderDetailModal(orderId) {
        const dlg = $("detailDialog");
        $("detailDialogTitle").textContent = `订单 #${orderId} 分时计费明细`;
        $("detailDialogBody").innerHTML = '<div class="empty-hint">正在加载订单详情...</div>';
        dlg.showModal();

        const controller = beginRequest("modal:detail");
        const epoch = state.authEpoch;
        try {
            const { data } = await apiFetch(`/api/orders/${orderId}`, {
                signal: controller.signal,
            });
            if (controller.signal.aborted || epoch !== state.authEpoch) return;
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
                                            <td data-label="时段">${statusBadge(d.period)}</td>
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
                : '<div class="empty-hint">该订单暂无分时费用明细（充电结束后自动按峰平谷时段生成）。</div>';

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
            if (isAbortError(err) || epoch !== state.authEpoch) return;
            $("detailDialogBody").innerHTML = `<div class="inline-alert">加载失败 (${escapeHtml(err.status || "-")})：${escapeHtml(err.message)}</div>`;
        } finally {
            finishRequest("modal:detail", controller);
        }
    }

    async function loadPeriodStats() {
        const box = $("periodStatBox");
        const startDate = $("statStartDate").value;
        const endDate = $("statEndDate").value;
        if (!startDate || !endDate) return;

        const controller = beginRequest("panel:periodStats");
        const epoch = state.authEpoch;
        try {
            const qs = new URLSearchParams({ start_date: startDate, end_date: endDate });
            const { data } = await apiFetch(`/api/orders/stats/period?${qs.toString()}`, {
                signal: controller.signal,
            });
            if (controller.signal.aborted || epoch !== state.authEpoch) return;
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
                                    <td data-label="时段">${statusBadge(r.period)}</td>
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
            if (isAbortError(err) || epoch !== state.authEpoch) return;
            if (err.status === 500) {
                box.innerHTML = `<div class="empty-hint">分时统计服务异常 (500)：${escapeHtml(err.message)}，请稍后重试。</div>`;
            } else if (err.status === 403) {
                box.innerHTML = `<div class="empty-hint">当前身份无权查询分时电价统计 (403)：${escapeHtml(err.message)}，请切换为“运维”或“管理员”身份。</div>`;
            } else {
                box.innerHTML = `<div class="empty-hint">查询失败 (${escapeHtml(err.status || "-")})：${escapeHtml(err.message)}</div>`;
            }
        } finally {
            finishRequest("panel:periodStats", controller);
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

        const controller = beginRequest("panel:faults");
        const epoch = state.authEpoch;
        try {
            const { data } = await apiFetch(`/api/stations/faults?${qs.toString()}`, {
                signal: controller.signal,
            });
            if (controller.signal.aborted || epoch !== state.authEpoch) return;
            if (!data.items.length && page > 1 && Number(data.total || 0) > 0) {
                const clampedPage = Math.max(1, Math.ceil(Number(data.total) / state.pageSize.faults));
                if (clampedPage !== page) return loadFaults(clampedPage);
            }
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
                        <td data-label="心跳滞后 (s)" class="num">${escapeHtml(r.heartbeat_lag_sec ?? "-")}</td>
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
            if (isAbortError(err) || epoch !== state.authEpoch) return;
            tbody.innerHTML = `<tr><td colspan="8"><div class="empty-hint">无权限或加载失败 (${escapeHtml(err.status || "-")})：${escapeHtml(err.message)}</div></td></tr>`;
            pager.innerHTML = "";
        } finally {
            finishRequest("panel:faults", controller);
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

        const controller = beginRequest("panel:logs");
        const epoch = state.authEpoch;
        try {
            const { data } = await apiFetch(`/api/stations/status-logs?${qs.toString()}`, {
                signal: controller.signal,
            });
            if (controller.signal.aborted || epoch !== state.authEpoch) return;
            if (!data.items.length && page > 1 && Number(data.total || 0) > 0) {
                const clampedPage = Math.max(1, Math.ceil(Number(data.total) / state.pageSize.logs));
                if (clampedPage !== page) return loadStatusLogs(clampedPage);
            }
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
                        <td data-label="设备">${escapeHtml(DEVICE_TYPE_LABELS[l.device_type] || l.device_type)} · <code>${escapeHtml(l.gun_code || l.pile_code)}</code></td>
                        <td data-label="状态变迁">${l.prev_status ? statusBadge(l.prev_status) : '<span class="meta">初始</span>'} → ${statusBadge(l.new_status)}</td>
                        <td data-label="来源">${escapeHtml(SOURCE_LABELS[l.change_source] || l.change_source)}</td>
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
            if (isAbortError(err) || epoch !== state.authEpoch) return;
            tbody.innerHTML = `<tr><td colspan="9"><div class="empty-hint">无权限或加载失败 (${escapeHtml(err.status || "-")})：${escapeHtml(err.message)}</div></td></tr>`;
            pager.innerHTML = "";
        } finally {
            finishRequest("panel:logs", controller);
        }
    }

    async function runHeartbeatCheck() {
        const btn = $("heartbeatCheckBtn");
        const epoch = state.authEpoch;
        // 巡检按离线阈值批量判定，演示数据的心跳时间通常早已过期，一点就会把大量桩/枪置为离线
        const proceed = window.confirm(
            "执行心跳离线巡检会按各桩的离线阈值判定在线状态：心跳时间早于阈值的桩/枪将被统一标记为离线。\n\n" +
                "这会让「找站与开单」暂时没有空闲枪，需重跑 sql/04_seed.sql 才能恢复演示数据。\n\n确认执行？"
        );
        if (!proceed) return;
        if (btn) btn.disabled = true;
        try {
            const { data } = await apiFetch("/api/stations/piles/heartbeat-check", {
                method: "POST",
                body: JSON.stringify({ check_time: null }),
            });
            if (epoch !== state.authEpoch) return;
            showToast(`${data.message}：标记离线桩 ${data.affected_piles} 台、枪 ${data.affected_guns} 把`);
            loadFaults(1);
            loadStatusLogs(1);
        } catch (err) {
            if (epoch !== state.authEpoch) return;
            showToast(`巡检失败 (${err.status || "-"})：${err.message}`, true);
        } finally {
            if (btn) syncRoleActionVisibility();
        }
    }

    async function reportSingleHeartbeat() {
        const pileId = parsePositiveInt($("opsPileId"), "充电桩 ID");
        if (!pileId) return;
        const btn = $("reportHeartbeatBtn");
        const epoch = state.authEpoch;
        if (btn) btn.disabled = true;
        try {
            const { data } = await apiFetch(`/api/stations/piles/${pileId}/heartbeat`, { method: "POST" });
            if (epoch !== state.authEpoch) return;
            showToast(`桩 #${data.pile_id} (${data.pile_code}) 心跳已刷新`);
            loadFaults();
            loadStatusLogs(1);
        } catch (err) {
            if (epoch !== state.authEpoch) return;
            showToast(`上报失败 (${err.status || "-"})：${err.message}`, true);
        } finally {
            if (btn) syncRoleActionVisibility();
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

        const controller = beginRequest("panel:realtime");
        const epoch = state.authEpoch;
        try {
            const { data } = await apiFetch(`/api/stations/realtime-status?${qs.toString()}`, {
                signal: controller.signal,
            });
            if (controller.signal.aborted || epoch !== state.authEpoch) return;
            if (!data.items.length && page > 1 && Number(data.total || 0) > 0) {
                const clampedPage = Math.max(1, Math.ceil(Number(data.total) / state.pageSize.realtime));
                if (clampedPage !== page) return loadRealtimeBoard(clampedPage);
            }

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
                                    <span class="mini-pill">总 ${escapeHtml(s.total_piles)}</span>
                                    ${Number(s.pending_acceptance_piles) > 0 ? `<span class="mini-pill pill-warn">验 ${escapeHtml(s.pending_acceptance_piles)}</span>` : ""}
                                    <span class="mini-pill pill-ok">闲 ${escapeHtml(s.idle_piles)}</span>
                                    <span class="mini-pill pill-blue">忙 ${escapeHtml(s.occupied_piles)}</span>
                                    ${pileAbnormal > 0 ? `<span class="mini-pill pill-danger">异 ${escapeHtml(pileAbnormal)}</span>` : '<span class="mini-pill">异 0</span>'}
                                </div>
                            </td>
                            <td data-label="装机功率 (kW)" class="num"><strong>${escapeHtml(s.total_rated_power)}</strong></td>
                            <td data-label="充电枪分布">
                                <div class="mini-pills">
                                    <span class="mini-pill">总 ${escapeHtml(s.total_guns)}</span>
                                    <span class="mini-pill pill-ok">闲 ${escapeHtml(s.idle_guns)}</span>
                                    <span class="mini-pill pill-blue">忙 ${escapeHtml(s.occupied_guns)}</span>
                                    ${gunAbnormal > 0 ? `<span class="mini-pill pill-danger">异 ${escapeHtml(gunAbnormal)}</span>` : '<span class="mini-pill">异 0</span>'}
                                </div>
                            </td>
                            <td data-label="操作" class="actions-cell">
                                ${
                                    isAdmin
                                        ? `<div class="row-actions">
                                            <button class="btn btn-outline btn-xs drill-station-btn" data-station-id="${escapeHtml(s.station_id)}" type="button">钻取桩</button>
                                            <button class="btn btn-outline btn-xs edit-station-btn" data-station-id="${escapeHtml(s.station_id)}" type="button">编辑</button>
                                        </div>`
                                        : '<span class="meta">只读看板</span>'
                                }
                            </td>
                        </tr>`;
                })
                .join("");

            renderPagination(pager, data, (nextPage) => loadRealtimeBoard(nextPage));
        } catch (err) {
            if (isAbortError(err) || epoch !== state.authEpoch) return;
            kpis.innerHTML = "";
            tbody.innerHTML = `<tr><td colspan="8"><div class="empty-hint">无权限或加载失败 (${escapeHtml(err.status || "-")})：${escapeHtml(err.message)}</div></td></tr>`;
            pager.innerHTML = "";
        } finally {
            finishRequest("panel:realtime", controller);
        }
    }

    async function loadOperators(summaryOnly = state.operatorsMode === "summary") {
        state.operatorsMode = summaryOnly ? "summary" : "full";
        const fullBtn = $("loadOperatorsFullBtn");
        const summaryBtn = $("loadOperatorsSummaryBtn");
        if (fullBtn) {
            fullBtn.className = `btn btn-xs ${summaryOnly ? "btn-outline" : "btn-primary"}`;
            fullBtn.setAttribute("aria-pressed", summaryOnly ? "false" : "true");
        }
        if (summaryBtn) {
            summaryBtn.className = `btn btn-xs ${summaryOnly ? "btn-primary" : "btn-outline"}`;
            summaryBtn.setAttribute("aria-pressed", summaryOnly ? "true" : "false");
        }

        const tbody = $("operatorsBody");
        const endpoint = summaryOnly ? "/api/stations/operators/summary" : "/api/stations/operators";
        const controller = beginRequest("panel:operators");
        const epoch = state.authEpoch;
        try {
            const { data } = await apiFetch(endpoint, { signal: controller.signal });
            if (controller.signal.aborted || epoch !== state.authEpoch) return;
            state.operatorCache.clear();
            if (!data.length) {
                renderTableEmptyRow(tbody, 7, "暂无合作运营商档案。");
                return;
            }
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
            if (isAbortError(err) || epoch !== state.authEpoch) return;
            tbody.innerHTML = `<tr><td colspan="7"><div class="empty-hint">无权限或加载失败 (${escapeHtml(err.status || "-")})：${escapeHtml(err.message)}</div></td></tr>`;
        } finally {
            finishRequest("panel:operators", controller);
        }
    }

    async function drillPiles(stationId) {
        const box = $("drillResultBox");
        $("drillStationId").value = stationId;
        const controller = beginRequest("panel:drill");
        const epoch = state.authEpoch;
        try {
            const { data } = await apiFetch(`/api/stations/${stationId}/piles`, {
                signal: controller.signal,
            });
            if (controller.signal.aborted || epoch !== state.authEpoch) return;
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
            if (isAbortError(err) || epoch !== state.authEpoch) return;
            box.className = "empty-hint";
            box.textContent = `查桩失败 (${err.status || "-"})：${err.message}`;
        } finally {
            finishRequest("panel:drill", controller);
        }
    }

    async function drillGuns(pileId) {
        const box = $("drillResultBox");
        $("drillPileId").value = pileId;
        const controller = beginRequest("panel:drill");
        const epoch = state.authEpoch;
        try {
            const { data } = await apiFetch(`/api/stations/piles/${pileId}/guns`, {
                signal: controller.signal,
            });
            if (controller.signal.aborted || epoch !== state.authEpoch) return;
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
                                    <td data-label="枪型">${escapeHtml(GUN_TYPE_LABELS[g.gun_type] || g.gun_type)}</td>
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
            if (isAbortError(err) || epoch !== state.authEpoch) return;
            box.className = "empty-hint";
            box.textContent = `查枪失败 (${err.status || "-"})：${err.message}`;
        } finally {
            finishRequest("panel:drill", controller);
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

        const controller = beginRequest("panel:bills");
        const epoch = state.authEpoch;
        try {
            const { data } = await apiFetch(`/api/settlements?${qs.toString()}`, {
                signal: controller.signal,
            });
            if (controller.signal.aborted || epoch !== state.authEpoch) return;
            if (!data.items.length && page > 1 && Number(data.total || 0) > 0) {
                const clampedPage = Math.max(1, Math.ceil(Number(data.total) / state.pageSize.bills));
                if (clampedPage !== page) return loadBills(clampedPage);
            }
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
                                    ${
                                        Number(b.uninvoiced_amount) > 0
                                            ? `<button class="btn btn-primary btn-xs issue-invoice-btn" data-bill-id="${escapeHtml(b.bill_id)}" data-uninvoiced="${escapeHtml(b.uninvoiced_amount)}" data-payable="${escapeHtml(b.payable_amount)}" type="button">开票</button>`
                                            : `<button class="btn btn-outline btn-xs" type="button" disabled title="该结算单已全额开票，无需再开">已开满</button>`
                                    }
                                </div>
                            </td>
                        </tr>`;
                })
                .join("");

            renderPagination(pager, data, (nextPage) => loadBills(nextPage));
        } catch (err) {
            if (isAbortError(err) || epoch !== state.authEpoch) return;
            tbody.innerHTML = `<tr><td colspan="12"><div class="empty-hint">无权限或加载失败 (${escapeHtml(err.status || "-")})：${escapeHtml(err.message)}</div></td></tr>`;
            pager.innerHTML = "";
        } finally {
            finishRequest("panel:bills", controller);
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

        const controller = beginRequest("panel:diffs");
        const epoch = state.authEpoch;
        try {
            const { data } = await apiFetch(`/api/settlements/diffs?${qs.toString()}`, {
                signal: controller.signal,
            });
            if (controller.signal.aborted || epoch !== state.authEpoch) return;
            if (!data.items.length && page > 1 && Number(data.total || 0) > 0) {
                const clampedPage = Math.max(1, Math.ceil(Number(data.total) / state.pageSize.diffs));
                if (clampedPage !== page) return loadDiffs(clampedPage);
            }
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
                        <td data-label="类型">${escapeHtml(DIFF_TYPE_LABELS[d.diff_type] || d.diff_type)}</td>
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
            if (isAbortError(err) || epoch !== state.authEpoch) return;
            tbody.innerHTML = `<tr><td colspan="7"><div class="empty-hint">无权限或加载失败 (${escapeHtml(err.status || "-")})：${escapeHtml(err.message)}</div></td></tr>`;
            pager.innerHTML = "";
        } finally {
            finishRequest("panel:diffs", controller);
        }
    }

    async function showBillDetailModal(billId) {
        const dlg = $("detailDialog");
        $("detailDialogTitle").textContent = `结算单 #${billId} 汇总详情`;
        $("detailDialogBody").innerHTML = '<div class="empty-hint">正在加载账单详情...</div>';
        dlg.showModal();

        const controller = beginRequest("modal:detail");
        const epoch = state.authEpoch;
        try {
            const { data } = await apiFetch(`/api/settlements/${billId}`, {
                signal: controller.signal,
            });
            if (controller.signal.aborted || epoch !== state.authEpoch) return;
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
            if (isAbortError(err) || epoch !== state.authEpoch) return;
            $("detailDialogBody").innerHTML = `<div class="inline-alert">加载失败 (${escapeHtml(err.status || "-")})：${escapeHtml(err.message)}</div>`;
        } finally {
            finishRequest("modal:detail", controller);
        }
    }

    function renderSessionInvoices() {
        const list = $("sessionInvoicesList");
        if (!state.sessionInvoices.length) {
            list.innerHTML = '<li class="meta">暂无本会话开票记录（种子发票 ID 为 1 ~ 20）</li>';
            return;
        }
        list.innerHTML = state.sessionInvoices
            .map((inv) => {
                const label = `#${escapeHtml(inv.invoice_id)} · ${escapeHtml(inv.invoice_no)} (¥${escapeHtml(inv.amount)})`;
                if (inv.red_flushed) {
                    return `<li><button class="btn btn-outline btn-xs" type="button" disabled>已红冲 · ${label}</button></li>`;
                }
                return `<li><button class="btn btn-outline btn-xs pick-invoice-btn" data-invoice-id="${escapeHtml(inv.invoice_id)}" data-amount="${escapeHtml(inv.amount)}" type="button">${label}</button></li>`;
            })
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
                skipAuthRedirect: true,
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
    // Hash 路由与面板数据调度
    // ------------------------------------------------------------------------
    function panelFromHash(hash) {
        if (hash && Object.hasOwn(ROUTES, hash)) {
            return ROUTES[hash].panel;
        }
        return "panel-home";
    }

    function hashFromPanel(panelId) {
        for (const [hash, cfg] of Object.entries(ROUTES)) {
            if (cfg.panel === panelId) return hash;
        }
        return "#/home";
    }

    function activatePanel(panelId, { updateHash = true, pushHistory = false, fetchData = true } = {}) {
        const targetHash = hashFromPanel(panelId);
        const routeCfg = ROUTES[targetHash] || ROUTES["#/home"];
        const resolvedPanel = routeCfg.panel;

        state.activePanel = resolvedPanel;

        if ($("pageTitle")) $("pageTitle").textContent = routeCfg.title;
        if ($("pageDesc")) $("pageDesc").textContent = routeCfg.desc;

        document.querySelectorAll("#mainNav .nav-link[data-panel]").forEach((link) => {
            if (link.dataset.panel === resolvedPanel) {
                link.setAttribute("aria-current", "page");
            } else {
                link.removeAttribute("aria-current");
            }
        });

        document.querySelectorAll(".tabpanel").forEach((panel) => {
            if (panel.id === resolvedPanel) {
                panel.removeAttribute("hidden");
                panel.tabIndex = 0;
            } else {
                panel.setAttribute("hidden", "until-found");
                panel.removeAttribute("tabindex");
            }
        });

        if (updateHash && location.hash !== targetHash) {
            if (pushHistory) {
                history.pushState(null, "", targetHash);
            } else {
                history.replaceState(null, "", targetHash);
            }
        }

        renderRoleNotice();
        if (fetchData) refreshActivePanelData();
    }

    function renderTableEmptyRow(tbody, colspan, message) {
        if (!tbody) return;
        tbody.innerHTML = `<tr><td colspan="${colspan}"><div class="empty-hint">${escapeHtml(message)}</div></td></tr>`;
    }

    function refreshActivePanelData() {
        const role = state.user?.role;
        const p = state.activePanel;
        if (p === "panel-home") {
            renderHomeOverview();
            loadHomeData();
        } else if (p === "panel-driver") {
            if (!role || TAB_ACCESS[p].roles.includes(role)) {
                loadDriverStations();
                loadDriverGuns();
            } else {
                const msg = "当前身份无权访问车主找站视图，请顶部切换为 车主 / 车队 / 运维 / 管理员";
                $("driverStationGrid").innerHTML = `<div class="empty-hint">${msg}</div>`;
                $("driverStationPager").innerHTML = "";
                renderTableEmptyRow($("driverGunsBody"), 9, msg);
                $("driverGunsPager").innerHTML = "";
            }
        } else if (p === "panel-orders") {
            loadOrders();
        } else if (p === "panel-ops") {
            if (!role || TAB_ACCESS[p].roles.includes(role)) {
                loadFaults();
                loadStatusLogs();
            } else {
                const msg = "当前身份无权查看运维排障数据，请切换为 场站运维 (ops) 或 平台管理员 (admin)";
                renderTableEmptyRow($("faultsBody"), 8, msg);
                $("faultsPager").innerHTML = "";
                renderTableEmptyRow($("statusLogsBody"), 9, msg);
                $("logsPager").innerHTML = "";
            }
        } else if (p === "panel-admin") {
            if (role === "admin" || role === "settlement") {
                loadRealtimeBoard();
                loadOperators(role === "settlement");
            } else {
                const msg = "实时资产看板与运营商档案限 平台管理员 (admin) 或 结算专员 (settlement) 查看";
                $("realtimeKpis").innerHTML = "";
                $("realtimePager").innerHTML = "";
                renderTableEmptyRow($("realtimeBody"), 8, msg);
                renderTableEmptyRow($("operatorsBody"), 7, msg);
            }
            if (role === "ops") {
                const stId = Number($("drillStationId").value);
                drillPiles(Number.isInteger(stId) && stId >= 1 ? stId : 1);
            } else if (role === "admin") {
                const box = $("drillResultBox");
                if (box && box.className === "empty-hint") {
                    box.textContent = "输入场站 ID 或桩 ID，逐级查看下属设备。";
                }
            } else {
                const box = $("drillResultBox");
                if (box) {
                    box.className = "empty-hint";
                    box.textContent = "桩 / 枪明细钻取限 场站运维 (ops) 或 平台管理员 (admin) 使用。";
                }
            }
        } else if (p === "panel-settle") {
            if (!role || TAB_ACCESS[p].roles.includes(role)) {
                loadBills();
                loadDiffs();
            } else {
                const msg = "月度结算单与对账差异限 结算专员 (settlement) 或 平台管理员 (admin) 查看";
                renderTableEmptyRow($("billsBody"), 12, msg);
                $("billsPager").innerHTML = "";
                renderTableEmptyRow($("diffsBody"), 7, msg);
                $("diffsPager").innerHTML = "";
            }
        }
    }

    // ------------------------------------------------------------------------
    // 事件绑定
    // ------------------------------------------------------------------------
    function bindEvents() {
        setupDialogs();

        // 主题切换（浅色 / 深色，顶栏与登录页双向同步）
        $("themeToggle")?.addEventListener("click", toggleTheme);
        $("authThemeToggle")?.addEventListener("click", toggleTheme);

        // 侧边导航与 Hash 路由同步（显式点击侧栏推进浏览器历史记录）
        $("mainNav")?.addEventListener("click", (e) => {
            const link = e.target.closest(".nav-link[data-panel]");
            if (!link) return;
            e.preventDefault();
            activatePanel(link.dataset.panel, { pushHistory: true });
        });
        window.addEventListener("hashchange", () => {
            const authMode = authModeFromHash(location.hash);
            if (authMode) {
                showAuthView(authMode, { updateHash: false });
                return;
            }
            const nextPanel = panelFromHash(location.hash);
            if (!state.user) {
                state.activePanel = nextPanel;
                showAuthView("login", { updateHash: true });
                return;
            }
            if ($("authView") && !$("authView").hidden) {
                $("authView").hidden = true;
                $("appShell").hidden = false;
            }
            activatePanel(nextPanel, { updateHash: false });
        });

        // 总览页充电枪阵列点击交互
        $("gunMatrix")?.addEventListener("click", (e) => {
            const cell = e.target.closest(".gun-cell[data-gun-id]");
            if (!cell) return;
            const role = state.user?.role;
            const canOrder = role === "driver" || role === "fleet";
            if (cell.dataset.gunStatus === "IDLE" && canOrder) {
                openStartOrderModal({
                    stationId: cell.dataset.stationId,
                    gunId: cell.dataset.gunId,
                    stationName: cell.dataset.stationName,
                    gunCode: cell.dataset.gunCode,
                });
            } else if (!role || TAB_ACCESS["panel-driver"].roles.includes(role)) {
                $("gunStationFilter").value = cell.dataset.stationId || "";
                activatePanel("panel-driver", { pushHistory: true });
            }
        });

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
            closeIdentityPopover();
        });
        $("topbarLoginBtn").addEventListener("click", () => {
            resetSessionForAuthView();
            showAuthView("login", { updateHash: true });
        });
        $("switchAccountBtn").addEventListener("click", () => {
            closeIdentityPopover();
            resetSessionForAuthView();
            showAuthView("login", { updateHash: true });
        });
        $("logoutBtn").addEventListener("click", () => {
            closeIdentityPopover();
            logoutUser();
        });

        // 独立全屏登录 / 注册页（login-04）交互
        $("goRegisterBtn").addEventListener("click", () => {
            showAuthView("register", { updateHash: true });
            $("regUsername")?.focus();
        });
        $("goLoginBtn").addEventListener("click", () => {
            showAuthView("login", { updateHash: true });
            $("loginUsername")?.focus();
        });
        $("backToWorkspaceBtn").addEventListener("click", () => {
            hideAuthView({ updateHash: true, fetchData: false });
        });

        document.querySelectorAll(".auth-role-btn[data-quick-role]").forEach((btn) => {
            btn.addEventListener("click", async () => {
                const role = btn.dataset.quickRole;
                if (!role) return;
                setInlineAlert($("authError"), "");
                btn.disabled = true;
                try {
                    const ok = await switchDevRole(role, { fetchData: false });
                    if (ok) {
                        hideAuthView({ updateHash: true, fetchData: true });
                    }
                } finally {
                    btn.disabled = false;
                }
            });
        });

        // 多标签页认证与主题状态同步（含 localStorage.clear()、登出与切换账号）
        window.addEventListener("storage", async (e) => {
            if (e.key === "ev_hub_theme") {
                const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
                document.documentElement.dataset.theme = e.newValue || (prefersDark ? "dark" : "light");
                syncThemeToggles();
                return;
            }
            if (e.key !== null && e.key !== "ev_hub_token") return;

            if (e.key === null || !e.newValue) {
                if (state.token || state.user) {
                    closeAllDialogs();
                    closeIdentityPopover();
                    state.authEpoch += 1;
                    abortRequestsByPrefix("panel:");
                    abortRequestsByPrefix("modal:");
                    abortRequestsByPrefix("auth:");
                    state.token = "";
                    state.user = null;
                    state.authMode = "";
                    resetWorkspacePaginationAndSession();
                    renderUserContext();
                    showAuthView("login", { updateHash: true });
                    showToast("登录凭证已清除，请重新登录");
                }
                return;
            }

            if (e.newValue !== state.token) {
                closeAllDialogs();
                closeIdentityPopover();
                state.authEpoch += 1;
                abortRequestsByPrefix("panel:");
                abortRequestsByPrefix("modal:");
                abortRequestsByPrefix("auth:");
                state.token = e.newValue;
                state.authMode = localStorage.getItem("ev_hub_auth_mode") || "";
                resetWorkspacePaginationAndSession();
                const res = await restoreSession();
                if (res.ok) {
                    if ($("authView") && !$("authView").hidden) {
                        hideAuthView({ updateHash: true, fetchData: true });
                    } else {
                        refreshActivePanelData();
                    }
                    showToast("已在其他标签页切换账号，已同步最新身份");
                } else if (res.reason === "invalid") {
                    renderUserContext();
                    showAuthView("login", { updateHash: true });
                }
            }
        });

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

        $("loginForm").addEventListener("submit", async (e) => {
            e.preventDefault();
            const submitBtn = $("loginSubmitBtn");
            submitBtn.disabled = true;
            setInlineAlert($("authError"), "");
            try {
                const { data } = await apiFetch("/api/auth/login", {
                    method: "POST",
                    body: JSON.stringify({
                        username: $("loginUsername").value.trim(),
                        password: $("loginPassword").value,
                    }),
                });
                sessionExpiring = false;
                state.authEpoch += 1;
                abortRequestsByPrefix("panel:");
                abortRequestsByPrefix("modal:");
                abortRequestsByPrefix("auth:");
                state.token = data.access_token;
                state.user = data.user;
                state.authMode = "account";
                localStorage.setItem("ev_hub_token", state.token);
                localStorage.setItem("ev_hub_auth_mode", "account");
                $("loginPassword").value = "";
                resetWorkspacePaginationAndSession();
                renderUserContext();
                hideAuthView({ updateHash: true, fetchData: true });
                showToast(`登录成功：${data.user.username}`);
            } catch (err) {
                const msg =
                    err.status === 500
                        ? "账号登录服务异常 (500)，请稍后重试；演示环境下也可点击下方演示角色快速进入。"
                        : `登录失败 (${err.status || "-"})：${err.message}`;
                setInlineAlert($("authError"), msg);
            } finally {
                submitBtn.disabled = false;
            }
        });

        $("registerForm").addEventListener("submit", async (e) => {
            e.preventDefault();
            const submitBtn = $("registerSubmitBtn");
            submitBtn.disabled = true;
            setInlineAlert($("authError"), "");
            try {
                const role = $("regRole").value || "driver";
                const payload = {
                    username: $("regUsername").value.trim(),
                    phone: $("regPhone").value.trim(),
                    password: $("regPassword").value,
                    role,
                    fleet_id: null,
                };
                const { data } = await apiFetch("/api/auth/register", {
                    method: "POST",
                    body: JSON.stringify(payload),
                });
                $("regPassword").value = "";
                showAuthView("login", { updateHash: true });
                $("loginUsername").value = data.username;
                $("loginPassword")?.focus();
                showToast(`账号 ${data.username} 注册成功，请登录`);
            } catch (err) {
                const msg =
                    err.status === 500
                        ? "自助注册服务异常 (500)，请稍后重试；演示环境下也可点击下方演示角色快速进入。"
                        : `注册失败 (${err.status || "-"})：${err.message}`;
                setInlineAlert($("authError"), msg);
            } finally {
                submitBtn.disabled = false;
            }
        });

        // hidden="until-found" 页内查找自动展开对应面板
        document.querySelectorAll(".tabpanel").forEach((panel) => {
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
        // 下拉筛选改动即刷新，避免"选了没反应、还要再点查询"的困惑
        ["gunTypeFilter", "gunStatusFilter", "gunStationFilter"].forEach((id) => {
            $(id).addEventListener("change", () => loadDriverGuns(1));
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
            const epoch = state.authEpoch;
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
                if (epoch !== state.authEpoch) return;
                $("startOrderDialog").close();
                showToast(`开单成功：${data.order_no}（订单 #${data.order_id}）`);
                if (state.activePanel === "panel-home") loadHomeData();
                if (state.activePanel === "panel-driver") {
                    loadDriverStations();
                    loadDriverGuns();
                }
                if (state.activePanel === "panel-orders") loadOrders(1);
            } catch (err) {
                if (epoch !== state.authEpoch) return;
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
                $("finishOrderInfo").textContent = `正在结束订单 #${finishBtn.dataset.orderId}（${finishBtn.dataset.orderNo}），提交后将自动按峰、平、谷时段核算电费与服务费。`;
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
            const epoch = state.authEpoch;
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
                if (epoch !== state.authEpoch) return;
                $("finishOrderDialog").close();
                showToast(`订单 #${data.order_id} 计费完成：应付 ¥${data.total_amount}`);
                loadOrders();
            } catch (err) {
                if (epoch !== state.authEpoch) return;
                setInlineAlert($("finishOrderError"), `结束计费失败 (${err.status || "-"})：${err.message}`);
            } finally {
                submitBtn.disabled = false;
            }
        });

        $("genTxnBtn").addEventListener("click", () => {
            $("poTxnNo").value = genTxnNo("PAY");
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
            const epoch = state.authEpoch;
            try {
                const orderId = $("poOrderId").value;
                // 支付只收正数（后端 PaymentIn.amount>0）；退款走结算端独立接口，前端先拦一层
                const amountRaw = $("poAmount").value.trim();
                if (!/^\d+(\.\d{1,2})?$/.test(amountRaw) || Number(amountRaw) <= 0) {
                    setInlineAlert(
                        $("payOrderError"),
                        "支付金额需为大于 0 的金额（最多两位小数）；退款请由结算专员在「结算对账与开票 → 订单退款」中发起。"
                    );
                    return;
                }
                const { data } = await apiFetch(`/api/orders/${orderId}/pay`, {
                    method: "POST",
                    body: JSON.stringify({
                        amount: amountRaw,
                        method: $("poMethod").value,
                        txn_no: $("poTxnNo").value.trim(),
                    }),
                });
                if (epoch !== state.authEpoch) return;
                $("payOrderDialog").close();
                showToast(`流水已记账：${data.txn_no}（¥${data.amount} · ${data.status}）`);
                loadOrders();
            } catch (err) {
                if (epoch !== state.authEpoch) return;
                setInlineAlert($("payOrderError"), `支付被拦截 (${err.status || "-"})：${err.message}`);
            } finally {
                submitBtn.disabled = false;
            }
        });

        // Tab 3 运维排障与巡检
        $("heartbeatCheckBtn").addEventListener("click", runHeartbeatCheck);
        $("reportHeartbeatBtn").addEventListener("click", reportSingleHeartbeat);
        $("commissionPileBtn").addEventListener("click", () => {
            const pileId = parsePositiveInt($("opsPileId"), "充电桩 ID");
            if (!pileId) return;
            openCommissionDialog(pileId);
        });
        $("openPileStatusBtn").addEventListener("click", () => {
            const pileId = parsePositiveInt($("opsPileId"), "充电桩 ID");
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
            const actionBtn = pileBtn || gunBtn;
            const epoch = state.authEpoch;
            if (actionBtn) actionBtn.disabled = true;
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
                    if (epoch !== state.authEpoch) return;
                    showToast(`充电桩 #${pid} 已恢复空闲`);
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
                    if (epoch !== state.authEpoch) return;
                    showToast(`充电枪 #${gid} 已恢复空闲`);
                    loadFaults();
                    loadStatusLogs(1);
                } else if (customBtn) {
                    openDeviceStatusDialog("PILE", customBtn.dataset.pileId, customBtn.dataset.cur);
                }
            } catch (err) {
                if (epoch !== state.authEpoch) return;
                showToast(`状态更新失败 (${err.status || "-"})：${err.message}`, true);
            } finally {
                if (actionBtn && document.contains(actionBtn)) actionBtn.disabled = false;
            }
        });

        $("statusLogsBody").addEventListener("click", (e) => {
            const btn = e.target.closest(".resolve-todo-btn");
            if (!btn) return;
            const logId = btn.dataset.logId;
            openActionDialog({
                title: `办结运维待办 #${logId}`,
                desc: `目标设备：${btn.dataset.device}。提交后会将待办置为已办结并追加处置备注。`,
                submitText: "确认办结",
                fields: [
                    { name: "remark", label: "现场处置备注", type: "text", value: "运维工程师已现场排查并确认恢复", required: true, maxlength: 255 },
                ],
                onSubmit: async (vals) => {
                    const { data } = await apiFetch(`/api/stations/status-logs/${logId}/resolve-todo`, {
                        method: "POST",
                        body: JSON.stringify({ remark: vals.remark.trim() }),
                    });
                    loadStatusLogs();
                    return `${data.message} (#${data.id})`;
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
                title: "新增合作运营商",
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
                            { value: "ACTIVE", label: "正常合作" },
                            { value: "SUSPENDED", label: "暂停合作" },
                        ],
                    },
                ],
                onSubmit: async (vals) => {
                    const { data } = await apiFetch("/api/stations/operators", {
                        method: "POST",
                        body: JSON.stringify(vals),
                    });
                    loadOperators(false);
                    return `运营商创建成功：#${data.operator_id} ${data.operator_name}`;
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
                            { value: "ACTIVE", label: "正常合作" },
                            { value: "SUSPENDED", label: "暂停合作" },
                        ],
                    },
                ],
                onSubmit: async (vals) => {
                    const { data } = await apiFetch(`/api/stations/operators/${op.operator_id}`, {
                        method: "PATCH",
                        body: JSON.stringify(vals),
                    });
                    loadOperators(false);
                    return `运营商 #${data.operator_id} 已更新`;
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
                    desc: "提示：空闲车位不可大于总车位数，否则提交将被系统校验拦截。",
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
                                { value: "OPEN", label: "营业中" },
                                { value: "MAINTENANCE", label: "维护中" },
                                { value: "CLOSED", label: "已关闭" },
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
                        loadRealtimeBoard();
                        return `场站 #${data.station_id} 更新成功`;
                    },
                });
            }
        });

        $("drillPilesBtn").addEventListener("click", () => {
            const stId = parsePositiveInt($("drillStationId"), "场站 ID");
            if (stId) drillPiles(stId);
        });
        $("drillGunsBtn").addEventListener("click", () => {
            const pileId = parsePositiveInt($("drillPileId"), "充电桩 ID");
            if (pileId) drillGuns(pileId);
        });
        $("drillResultBox").addEventListener("click", (e) => {
            const gunBtn = e.target.closest(".drill-pile-btn");
            const pileStatusBtn = e.target.closest(".drill-pile-status-btn");
            const commBtn = e.target.closest(".drill-commission-btn");
            const gunStatusBtn = e.target.closest(".drill-gun-status-btn");
            if (gunBtn) {
                drillGuns(Number(gunBtn.dataset.pileId));
            } else if (pileStatusBtn) {
                openDeviceStatusDialog("PILE", pileStatusBtn.dataset.pileId, pileStatusBtn.dataset.cur, () => {
                    drillPiles(Number($("drillStationId").value || 1));
                    if (state.user?.role === "admin") loadRealtimeBoard();
                });
            } else if (commBtn) {
                openCommissionDialog(Number(commBtn.dataset.pileId), () => {
                    drillPiles(Number($("drillStationId").value || 1));
                    if (state.user?.role === "admin") loadRealtimeBoard();
                });
            } else if (gunStatusBtn) {
                openDeviceStatusDialog("GUN", gunStatusBtn.dataset.gunId, gunStatusBtn.dataset.cur, () => {
                    drillGuns(Number($("drillPileId").value || 1));
                    if (state.user?.role === "admin") loadRealtimeBoard();
                });
            }
        });

        $("createStationForm").addEventListener("submit", async (e) => {
            e.preventDefault();
            const submitBtn = e.target.querySelector('button[type="submit"]');
            if (submitBtn) submitBtn.disabled = true;
            const epoch = state.authEpoch;
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
                if (epoch !== state.authEpoch) return;
                showToast(`场站创建成功：#${data.station_id} ${data.station_name}`);
                loadRealtimeBoard(1);
            } catch (err) {
                if (epoch !== state.authEpoch) return;
                showToast(`创建场站被拦截 (${err.status || "-"})：${err.message}`, true);
            } finally {
                if (submitBtn) syncRoleActionVisibility();
            }
        });

        $("createPileForm").addEventListener("submit", async (e) => {
            e.preventDefault();
            const submitBtn = e.target.querySelector('button[type="submit"]');
            if (submitBtn) submitBtn.disabled = true;
            const epoch = state.authEpoch;
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
                if (epoch !== state.authEpoch) return;
                showToast(`充电桩建档成功：#${data.pile_id} ${data.pile_code}`);
                drillPiles(payload.station_id);
                loadRealtimeBoard();
            } catch (err) {
                if (epoch !== state.authEpoch) return;
                showToast(`建档被拦截 (${err.status || "-"})：${err.message}`, true);
            } finally {
                if (submitBtn) syncRoleActionVisibility();
            }
        });

        $("createGunForm").addEventListener("submit", async (e) => {
            e.preventDefault();
            const submitBtn = e.target.querySelector('button[type="submit"]');
            if (submitBtn) submitBtn.disabled = true;
            const epoch = state.authEpoch;
            const fd = new FormData(e.target);
            const payload = Object.fromEntries(fd.entries());
            payload.pile_id = Number(payload.pile_id);
            payload.gun_seq = Number(payload.gun_seq);
            try {
                const { data } = await apiFetch("/api/stations/guns", {
                    method: "POST",
                    body: JSON.stringify(payload),
                });
                if (epoch !== state.authEpoch) return;
                showToast(`充电枪建档成功：#${data.gun_id} ${data.gun_code}`);
                drillGuns(payload.pile_id);
                loadRealtimeBoard();
            } catch (err) {
                if (epoch !== state.authEpoch) return;
                showToast(`建枪被拦截 (${err.status || "-"})：${err.message}`, true);
            } finally {
                if (submitBtn) syncRoleActionVisibility();
            }
        });

        // Tab 5 结算对账与开票红冲
        $("generateBillForm").addEventListener("submit", async (e) => {
            e.preventDefault();
            const submitBtn = e.target.querySelector('button[type="submit"]');
            if (submitBtn) submitBtn.disabled = true;
            const epoch = state.authEpoch;
            try {
                const { data } = await apiFetch("/api/settlements/generate", {
                    method: "POST",
                    body: JSON.stringify({
                        operator_id: Number($("genOperatorId").value),
                        bill_month: $("genBillMonth").value.trim(),
                        platform_rate: $("genPlatformRate").value.trim(),
                    }),
                });
                if (epoch !== state.authEpoch) return;
                showToast(data.message);
                loadBills(1);
                loadDiffs(1);
            } catch (err) {
                if (epoch !== state.authEpoch) return;
                showToast(`生成结算单失败 (${err.status || "-"})：${err.message}`, true);
            } finally {
                if (submitBtn) syncRoleActionVisibility();
            }
        });

        $("billSearchForm").addEventListener("submit", (e) => {
            e.preventDefault();
            loadBills(1);
        });
        $("refreshDiffsBtn").addEventListener("click", () => loadDiffs(1));

        // 订单退款：调用独立的 /refund 接口（后端生成负数流水），金额在这里校验后取负
        $("roTxnNo").value = genTxnNo("REF");
        $("genRefundTxnBtn").addEventListener("click", () => {
            $("roTxnNo").value = genTxnNo("REF");
        });
        $("refundOrderForm").addEventListener("submit", async (e) => {
            e.preventDefault();
            const submitBtn = $("refundOrderForm").querySelector('button[type="submit"]');
            if (submitBtn) submitBtn.disabled = true;
            setInlineAlert($("refundOrderError"), "");
            const epoch = state.authEpoch;
            const orderId = Number($("roOrderId").value);
            const amtRaw = $("roAmount").value.trim();
            const txnNo = $("roTxnNo").value.trim();
            try {
                if (!Number.isInteger(orderId) || orderId < 1) {
                    setInlineAlert($("refundOrderError"), "请填写有效的订单 ID（正整数）。");
                    return;
                }
                // 金额全程按字符串处理，避免浮点误差（项目规范：金额保持 Decimal 语义）
                if (!/^\d+(\.\d{1,2})?$/.test(amtRaw) || Number(amtRaw) <= 0) {
                    setInlineAlert($("refundOrderError"), "退款金额需为大于 0 的金额（最多两位小数）。");
                    return;
                }
                const { data } = await apiFetch(`/api/orders/${orderId}/refund`, {
                    method: "POST",
                    body: JSON.stringify({
                        amount: `-${amtRaw}`,
                        method: $("roMethod").value,
                        txn_no: txnNo,
                    }),
                });
                if (epoch !== state.authEpoch) return;
                showToast(`退款已记账：${data.txn_no}（¥${data.amount} · ${data.status}）`);
                $("roAmount").value = "";
                $("roTxnNo").value = genTxnNo("REF");
                loadBills();
                if (state.activePanel === "panel-orders") loadOrders();
            } catch (err) {
                if (epoch !== state.authEpoch) return;
                setInlineAlert($("refundOrderError"), `退款被拦截 (${err.status || "-"})：${err.message}`);
            } finally {
                if (submitBtn) syncRoleActionVisibility();
            }
        });

        $("billsBody").addEventListener("click", (e) => {
            const detailBtn = e.target.closest(".view-bill-detail-btn");
            const invBtn = e.target.closest(".issue-invoice-btn");
            if (detailBtn) {
                showBillDetailModal(detailBtn.dataset.billId);
            } else if (invBtn) {
                const billId = invBtn.dataset.billId;
                const uninvoiced = invBtn.dataset.uninvoiced;
                if (!(Number(uninvoiced) > 0)) {
                    showToast("该结算单已全额开票，无需重复开票", true);
                    return;
                }
                openActionDialog({
                    title: `为结算单 #${billId} 开具发票`,
                    desc: `账单应付 ¥${invBtn.dataset.payable}，当前剩余未开票 ¥${uninvoiced}。开票金额不可超出剩余未开票额度。`,
                    submitText: "确认开票",
                    fields: [
                        { name: "invoice_no", label: "发票号码", value: genTxnNo("INV").slice(0, 18), minlength: 4, maxlength: 32, required: true },
                        { name: "amount", label: "开票金额 (¥)", value: uninvoiced, inputmode: "decimal", required: true },
                        {
                            name: "invoice_type",
                            label: "发票类型",
                            type: "select",
                            value: "NORMAL",
                            options: [
                                { value: "NORMAL", label: "普通发票" },
                                { value: "SPECIAL", label: "专用发票" },
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
                        loadBills();
                        return `开票成功：#${data.invoice_id} ${data.invoice_no}（¥${data.amount}）`;
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
                    loadDiffs();
                    loadBills();
                    return `差异 #${data.diff_id} 已标记处理`;
                },
            });
        });

        $("redFlushForm").addEventListener("submit", async (e) => {
            e.preventDefault();
            const submitBtn = e.target.querySelector('button[type="submit"]');
            if (submitBtn) submitBtn.disabled = true;
            const epoch = state.authEpoch;
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
                if (epoch !== state.authEpoch) return;
                $("rfInvoiceNo").value = "";
                $("rfAmount").value = "";
                const idx = state.sessionInvoices.findIndex((inv) => Number(inv.invoice_id) === invoiceId);
                if (idx !== -1) {
                    state.sessionInvoices[idx] = { ...state.sessionInvoices[idx], red_flushed: true };
                    renderSessionInvoices();
                }
                showToast(`红冲完成：生成负数票 #${data.invoice_id} ${data.invoice_no}（¥${data.amount}）`);
                loadBills();
            } catch (err) {
                if (epoch !== state.authEpoch) return;
                showToast(`红冲被拦截 (${err.status || "-"})：${err.message}`, true);
            } finally {
                if (submitBtn) syncRoleActionVisibility();
            }
        });

        $("sessionInvoicesList").addEventListener("click", (e) => {
            const btn = e.target.closest(".pick-invoice-btn");
            if (!btn) return;
            $("rfInvoiceId").value = btn.dataset.invoiceId;
            $("rfInvoiceNo").value = genTxnNo("RED").slice(0, 18);
            $("rfAmount").value = `-${btn.dataset.amount}`;
        });

        // 通用弹窗提交处理器（先关闭对话框，再触发成功 Toast，避免被 dialog[open] 拦截）
        $("actionDialogForm").addEventListener("submit", async (e) => {
            e.preventDefault();
            if (!currentActionHandler) return;
            const submitBtn = $("actionDialogSubmitBtn");
            submitBtn.disabled = true;
            setInlineAlert($("actionDialogError"), "");
            const epoch = state.authEpoch;
            const handler = currentActionHandler;
            try {
                const fd = new FormData(e.target);
                const vals = Object.fromEntries(fd.entries());
                const result = await handler(vals);
                if (epoch !== state.authEpoch) return;
                $("actionDialog").close();
                if (typeof result === "string" && result) {
                    showToast(result);
                } else if (result && typeof result === "object" && result.message) {
                    showToast(result.message, Boolean(result.isError));
                }
            } catch (err) {
                if (epoch !== state.authEpoch) return;
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
            probeEndpoint("POST", "/api/stations/piles/1/commission", { remark: "重复验收已投运充电桩" })
        );
        $("probeOverpayBtn").addEventListener("click", async () => {
            const role = state.user?.role;
            if (role !== "driver" && role !== "fleet") {
                showToast("超额支付拦截由 /api/orders/{id}/pay 触发，该接口只对车主与车队开放，请先在顶栏切换角色后再测", true);
                return;
            }
            let targetOrderId = 6;
            if (state.user && (state.user.role === "driver" || state.user.role === "fleet")) {
                try {
                    const { data } = await apiFetch("/api/orders?page=1&page_size=20");
                    const paidOrFinished = data.items?.find((o) => o.status === "PAID" || o.status === "FINISHED");
                    if (paidOrFinished) targetOrderId = paidOrFinished.order_id;
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
        const today = getLocalDateString();
        openActionDialog({
            title: `充电桩 #${pileId} 协议联调验收投运`,
            desc: "仅已配置充电枪且未验收的充电桩可执行投运；未配枪或重复验收将被规则拦截。",
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
                if (onSuccess) {
                    onSuccess();
                } else {
                    loadFaults();
                    loadStatusLogs(1);
                }
                return `桩 #${data.pile_id} (${data.pile_code}) 已验收投运`;
            },
        });
    }

    function openDeviceStatusDialog(deviceType, targetId, currentStatus = "IDLE", onSuccess = null) {
        const isPile = deviceType === "PILE";
        const endpoint = isPile ? `/api/stations/piles/${targetId}/status` : `/api/stations/guns/${targetId}/status`;
        openActionDialog({
            title: `变更${isPile ? "充电桩" : "充电枪"} #${targetId} 运行状态`,
            desc: "提交后将实时更新设备运行状态，并自动记录至设备状态变更历史。",
            submitText: "提交状态变更",
            fields: [
                {
                    name: "new_status",
                    label: "目标运行状态",
                    type: "select",
                    value: currentStatus,
                    options: [
                        { value: "IDLE", label: "空闲" },
                        { value: "OCCUPIED", label: "占用" },
                        { value: "FAULT", label: "故障" },
                        { value: "OFFLINE", label: "离线" },
                    ],
                },
                {
                    name: "change_source",
                    label: "变更来源",
                    type: "select",
                    value: "MANUAL",
                    options: [
                        { value: "MANUAL", label: "人工维护" },
                        { value: "ALARM", label: "告警触发" },
                        { value: "HEARTBEAT", label: "心跳检测" },
                    ],
                },
                {
                    name: "todo_status",
                    label: "运维待办标记",
                    type: "select",
                    value: "",
                    options: [
                        { value: "", label: "自动判定（故障或离线时自动生成待处理）" },
                        { value: "PENDING", label: "待处理" },
                        { value: "DONE", label: "已办结" },
                        { value: "NONE", label: "无需处理" },
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
                if (onSuccess) {
                    onSuccess();
                } else {
                    loadFaults();
                    loadStatusLogs(1);
                }
                return `${isPile ? "充电桩" : "充电枪"} #${targetId} 状态已变更为 ${vals.new_status}`;
            },
        });
    }

    async function init() {
        const urlParams = new URLSearchParams(location.search);
        const themeParam = urlParams.get("theme");
        if (themeParam === "dark" || themeParam === "light") {
            document.documentElement.dataset.theme = themeParam;
            localStorage.setItem("ev_hub_theme", themeParam);
        }

        const roleParam = urlParams.get("role");
        if (urlParams.has("role")) {
            urlParams.delete("role");
            const nextSearch = urlParams.toString();
            const nextUrl = `${location.pathname}${nextSearch ? `?${nextSearch}` : ""}${location.hash}`;
            history.replaceState(null, "", nextUrl);
        }

        syncThemeToggles();
        bindEvents();

        const initialAuthMode = authModeFromHash(location.hash);
        state.activePanel = initialAuthMode ? "panel-home" : panelFromHash(location.hash);

        // 后台非阻塞并发拉取服务健康状态与角色字典，不阻塞首屏渲染
        checkDbHealth();
        loadRolesMeta();

        const validRoles = ["driver", "fleet", "ops", "settlement", "admin"];
        if (roleParam && validRoles.includes(roleParam)) {
            renderUserContext();
            showAuthView("login", { updateHash: false });
            const ok = await switchDevRole(roleParam, { fetchData: false });
            if (ok) {
                hideAuthView({ updateHash: true, fetchData: true });
            } else {
                showAuthView("login", { updateHash: true });
            }
            return;
        }

        if (!state.token) {
            renderUserContext();
            showAuthView(initialAuthMode || "login", { updateHash: true });
            return;
        }

        const restored = await restoreSession();
        if (restored.ok) {
            hideAuthView({ updateHash: true, fetchData: true });
        } else {
            renderUserContext();
            showAuthView(initialAuthMode || "login", { updateHash: true });
            if (restored.reason === "invalid") {
                setInlineAlert($("authError"), "登录状态已失效，请重新登录或选择演示角色进入。");
            } else if (restored.reason === "transient") {
                setInlineAlert(
                    $("authError"),
                    `恢复登录会话失败 (${restored.err?.status || "网络异常"})，已保留本地凭证，可刷新重试或重新登录。`
                );
            }
        }
    }

    init();
})();
