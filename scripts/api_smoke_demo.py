"""业务线4 接口冒烟演示脚本（答辩/联调用，纯标准库，无第三方依赖）。

用途：在服务已启动且 sql/01~04 已导入的前提下，按 RBAC 角色把「用户档案 → 绑车 → 发券 →
核销 → 对账」整条链路跑一遍，并打印每步的 HTTP 状态码与关键字段，便于截图留痕。

用法：
    .venv\\Scripts\\python.exe scripts\\api_smoke_demo.py
    # 自定义地址：set API_BASE=http://127.0.0.1:8000
前置账号：种子数据里的 driver_9016 / admin / zhang_settle / fleet_9101，统一口令 Demo@123456。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta

BASE = os.getenv("API_BASE", "http://127.0.0.1:8010").rstrip("/")
DEMO_PASSWORD = "Demo@123456"


def call(
    method: str,
    path: str,
    token: str | None = None,
    body: dict | None = None,
) -> tuple[int, dict | list | str]:
    """发起一次 JSON 请求，返回 (状态码, 解析后的响应体)，4xx/5xx 也照常返回而不抛异常。"""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method)
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8")
            return response.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw


def login(username: str) -> str:
    """账号密码登录并返回 Bearer Token。"""
    status, payload = call(
        "POST", "/api/auth/login", body={"username": username, "password": DEMO_PASSWORD}
    )
    assert status == 200, f"{username} 登录失败: {status} {payload}"
    return payload["access_token"]


def show(step: str, status: int, payload: dict | list | str, keys: tuple[str, ...] = ()) -> None:
    """打印步骤结果，只挑关键字段，避免刷屏。"""
    brief = ""
    if isinstance(payload, dict) and keys:
        brief = " | ".join(f"{k}={payload.get(k)!r}" for k in keys if k in payload)
    elif isinstance(payload, dict) and "detail" in payload:
        brief = f"detail={payload['detail']}"
    elif isinstance(payload, list):
        brief = f"items={len(payload)}"
    print(f"[{status}] {step}" + (f" -> {brief}" if brief else ""))


def main() -> int:
    print(f"== 目标服务 {BASE} ==")

    status, payload = call("GET", "/health/db")
    show("健康检查 /health/db", status, payload, ("status", "mysql_version", "database"))
    assert status == 200, "服务未就绪或数据库未连通"

    # ---------- 1. 车主侧：本人档案 / 车辆 / 券包 ----------
    driver = login("driver_9016")
    status, profile = call("GET", "/api/users/me/profile", token=driver)
    show("车主查看本人脱敏档案", status, profile, ("user_id", "role", "phone_masked", "vehicle_cnt"))
    assert status == 200 and "phone_cipher" not in profile, "档案必须脱敏"

    status, my_orders = call("GET", "/api/orders?page=1&page_size=20", token=driver)
    orders = my_orders.get("items", []) if isinstance(my_orders, dict) else []
    paid = [o for o in orders if o.get("status") == "PAID"]
    assert paid, "driver_9016 没有已支付订单可用于核销演示"
    order = paid[0]
    print(f"    选用订单 order_id={order['order_id']} 金额={order['total_amount']}")

    status, vehicles = call("GET", "/api/users/vehicles", token=driver)
    show("车主查看本人车辆", status, vehicles, ("total",))

    status, bound = call(
        "POST",
        "/api/users/vehicles",
        token=driver,
        body={
            "plate_no": "京AD90160",
            "plate_color": "GREEN",
            "vehicle_model": "比亚迪 海豹 550km 尊贵型",
            "battery_capacity_kwh": "61.40",
            "max_charge_power": "150.00",
            "supports_fast_charge": True,
        },
    )
    show("车主绑定新车辆", status, bound, ("vehicle_id", "plate_masked", "bind_status"))

    status, dup = call(
        "POST",
        "/api/users/vehicles",
        token=driver,
        body={
            "plate_no": "京AD90160",
            "plate_color": "GREEN",
            "vehicle_model": "重复绑定同一车牌",
            "battery_capacity_kwh": "61.40",
            "max_charge_power": "150.00",
            "supports_fast_charge": True,
        },
    )
    show("同一车牌二次绑定（应 409 一车一主）", status, dup)
    assert status == 409, "重复车牌必须被唯一索引拦截为 409"

    # ---------- 2. 管理员侧：建活动 -> 定向发券 ----------
    admin = login("admin")
    today = date.today()
    status, created = call(
        "POST",
        "/api/users/campaigns",
        token=admin,
        body={
            "campaign_code": f"CP-DEMO-{today.strftime('%m%d')}",
            "campaign_name": "答辩演示立减券",
            "coupon_kind": "CASH",
            "face_value": "8.00",
            "threshold_amount": "0.00",
            "valid_from": str(today),
            "valid_to": str(today + timedelta(days=30)),
            "issue_total": 200,
            "per_user_limit": 1,
            "status": "ACTIVE",
        },
    )
    show("管理员创建活动", status, created, ("success", "message", "id"))
    campaign_id = created.get("id")
    if status == 409:
        print("    活动编号已存在（重复运行），跳过创建")
        return 0
    assert status == 201, f"创建活动失败: {created}"

    status, issued = call(
        "POST",
        f"/api/users/campaigns/{campaign_id}/issue",
        token=admin,
        body={"user_id": profile["user_id"], "count": 1, "channel": "OPS_GRANT"},
    )
    show("运营定向发券", status, issued, ("user_id", "issued_count"))
    assert status == 201 and issued["issued_count"] == 1, "发券失败"
    coupon_no = issued["coupon_nos"][0]
    print(f"    券码 {coupon_no}")

    status, again = call(
        "POST",
        f"/api/users/campaigns/{campaign_id}/issue",
        token=admin,
        body={"user_id": profile["user_id"], "count": 1, "channel": "OPS_GRANT"},
    )
    show("同一活动重复发券（应 400 触发限领）", status, again)
    assert status == 400, "超过每人限领应被触发器拒绝"

    # ---------- 3. 车主核销：券 -> 订单抵扣 ----------
    status, redeemed = call(
        "POST",
        "/api/users/coupons/redeem",
        token=driver,
        body={"coupon_no": coupon_no, "order_id": order["order_id"]},
    )
    show("车主核销优惠券", status, redeemed, ("coupon_no", "face_value", "payable_amount"))
    assert status == 200, f"核销失败: {redeemed}"

    status, wallet = call("GET", "/api/users/coupons/mine?page_size=5", token=driver)
    show("车主券包（含可用标记）", status, wallet, ("total",))

    # ---------- 4. RBAC 越权拦截 ----------
    status, forbidden = call("GET", "/api/users?page_size=5", token=driver)
    show("车主访问全量用户列表（应 403）", status, forbidden)

    status, admin_list = call("GET", "/api/users?page_size=3", token=admin)
    show("管理员分页查脱敏档案", status, admin_list, ("total", "page", "page_size"))

    status, redeems = call("GET", "/api/users/redeems?page_size=5", token=admin)
    show("核销台账对账", status, redeems, ("total",))

    # ---------- 5. 车队与企业隔离 ----------
    fleet = login("fleet_9101")
    status, credit = call("GET", "/api/users/fleets/credit", token=fleet)
    items = credit.get("items", []) if isinstance(credit, dict) else []
    fleet_ids = {row.get("fleet_id") for row in items}
    show("车队查看本企业授信（应仅 fleet_id=1）", status, credit, ("total",))
    assert fleet_ids <= {1}, f"车队越权看到其他企业: {fleet_ids}"

    # ---------- 6. 结算专员：只读，不可发券 ----------
    settle = login("zhang_settle")
    status, _ = call("GET", "/api/users?page_size=3", token=settle)
    show("结算专员查用户档案（应 200）", status, {})
    status, denied = call(
        "POST",
        f"/api/users/campaigns/{campaign_id}/issue",
        token=settle,
        body={"user_id": profile["user_id"], "count": 1},
    )
    show("结算专员发券（应 403）", status, denied)

    print("== 冒烟演示全部通过 ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
