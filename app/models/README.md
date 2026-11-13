# `app/models/` —— SQLAlchemy 2.0 ORM 模型层规范

> **对应任务书要求**：《新能源汽车充电桩运营服务平台》第 3.1 / 3.3 节（数据库结构与持久层映射）

---

## 一、 本层职责与开发铁律

1. **只做映射，严禁建表**：
   所有表结构、外键、索引、生成列与分区均以 `sql/01_ddl.sql` 为唯一真源。**绝对禁止**在代码中调用 `Base.metadata.create_all(bind=engine)`。
2. **统一继承基类**：
   全部模型统一继承 `from app.db import Base`，使用 SQLAlchemy 2.0 `Mapped[...]` 与 `mapped_column(...)` 语法。
3. **精度与类型规范**：
   - 电量（kWh）、功率（kW）使用 `Numeric(10, 3)` / `Numeric(8, 2)` 映射为 Python `Decimal`；
   - 所有金额字段统一 `Numeric(10, 2)`，**严禁使用 `Float`**；
   - 时间统一 `DateTime`，标配 `created_at` / `updated_at`。
4. **特殊结构映射提醒**：
   - **生成列**（如 `charge_order.active_gun_id`）：在 ORM 中需标注 `Computed(...)` 或 `server_default=FetchedValue()`，写入时**不要主动赋值**；
   - **分区表**（如 `pile_telemetry`）：MySQL 分区表不支持外键，模型中不要声明 `ForeignKey`（但需保留 `pile_id` 与分区键在联合主键中）。

---

## 二、 5 条业务线模型文件分工

| 文件 | 业务线 & 负责人 | 对应核心数据表 | 关键约束与模型要点 |
| :--- | :--- | :--- | :--- |
| `station.py` | 业务线 1（成员 1 / 组长） | `operator`、`station`、`charging_pile`、`charging_gun`、`device_status_log` | **场站→桩→枪三级独立实体**（枪是最小计费粒度，禁止做成桩字段）；单枪最大功率 ≤ 所属桩额定功率 |
| `order.py` | 业务线 2（成员 2） | `pricing_rule`、`charge_order`、`order_fee_detail`、`payment_record` | 生成列 `active_gun_id` + `UNIQUE` 防同枪并发；分时计费明细按峰/平/谷拆分；订单与支付 `1:N` |
| `monitor.py` | 业务线 3（成员 4） | `pile_telemetry`、`alarm_record`、`repair_order` | `pile_telemetry` 按桩/月 `RANGE` 分区（无外键）；告警与维修工单为 `1:N` 关系 |
| `user.py` | 业务线 4（成员 3） | `user_account`、`vehicle`、`fleet_account`、`coupon_campaign`、`coupon_record` | 手机号/车牌加密存储；1 车同刻仅归 1 主；优惠券发放与核销分离，核销与订单 `1:1` 绑定 |
| `settle.py` | 业务线 5（成员 4） | `settlement_bill`、`reconcile_diff`、`invoice_record` | `(运营商, 场站, 账期)` 联合唯一；差异回指具体 `order_id`；开票红冲新增冲正行、禁删原票 |
