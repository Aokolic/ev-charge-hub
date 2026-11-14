# `app/dao/` —— 持久层数据访问规范（ORM + 原生 SQL 双轨）

> **对应任务书要求**：《新能源汽车充电桩运营服务平台》第 3.2 / 3.3 / 3.4 节（持久层封装、视图隔离、存储过程、事务边界与并发一致性）

---

## 一、 本层职责与核心原则

1. **路由与 SQL 解耦**：
   所有数据库读写统一封装在 `app/dao/*.py` 中；**禁止在 `app/routers/` 中直接编写 SQL 或 `db.execute(...)`**。
2. **双轨混用策略**：
   - **常规单表/主从 CRUD**：使用 SQLAlchemy 2.0 `select()` / `db.add()` / `db.flush()`，参数自动绑定防 SQL 注入；
   - **角色查询走视图**：任务书规定**原始数据表不得直接暴露给前端**，查询列表/看板统一使用 `db.execute(text("SELECT ... FROM v_xxx WHERE ..."), params)`；
   - **核心核价/流转走存储过程**：如充电结束分段核价、月度结算汇总，使用 `db.execute(text("CALL proc_xxx(:p1, ...)"), {...})`；
   - **严禁 f-string 拼接任何用户输入到 SQL 中**。
3. **显式事务与异常边界**：
   - `get_db()` 不会自动提交，写操作完成后须显式调用 `db.commit()`（调用写型存储过程后同样需要 `db.commit()`）；
   - 跨表原子操作（例如：**核销优惠券 + 写入支付记录 + 释放充电枪状态**）必须在**同一次请求、同一个 `db` 会话内**一次 `commit()`；
   - 底层抛出的 `IntegrityError`（`1062` 同枪并发下单冲突、优惠券重复核销、结算单账期重复）和 `OperationalError`（`1644` 触发器 `SIGNAL` 报错）**直接向上抛出交由 `app/main.py` 统一转 HTTP 409/400**；若 DAO 内部捕获了异常，必须先 `db.rollback()`。

---

## 二、 公共持久层与 5 条业务线核心实现清单

| 文件 | 模块 / 业务线 | 必须体现的公共能力 / 视图查询 / 存储过程 / 事务场景 |
| :--- | :--- | :--- |
| [`common.py`](common.py) | **全组公共底座（已实现）** | 视图两段式参数化分页 `fetch_view_page`；脱敏与加解密工具 `mask_phone`、`mask_plate`、`hash_sensitive_lookup`（HMAC-SHA256 盲索引）、`encrypt_sensitive` / `decrypt_sensitive`（Fernet 对称加解密）；认证账号读写 `AccountRecord`、`get_account_by_username`、`record_login_failure`、`reset_login_success`、`create_account` |
| `station.py` | 业务线 1 | 查询 `v_station_realtime_status`、`v_fault_gun_detail` 等视图（≥3 个）；设备投运/状态切换同时写 `device_status_log` 同一事务提交 |
| `order.py` | 业务线 2 | 扫码创建进行中订单（依靠 `active_gun_id` 唯一索引防并发撞枪）；`CALL` 分时核价存储过程写 `order_fee_detail`；1:N 支付/退款与补缴事务 |
| `monitor.py` | 业务线 3 | 遥测分钟级数据批量写入；告警派发多工单（1:N）；工单闭环并校验连续正常后更新告警恢复状态 |
| `user.py` | 业务线 4 | 敏感字段加解密写入；查询脱敏车主视图；车队绑定与额度扣减；发券与核销记录（与 `order_id` 1:1 防重事务） |
| `settle.py` | 业务线 5 | `CALL` 月结存储过程生成 `settlement_bill`；对账差异回指 `order_id` 并留痕调账；开票校验与红冲新增冲正记录（严禁 `DELETE`） |

### 公共工具复用要点

- **视图分页**：各业务线查询视图列表时直接调用 `fetch_view_page(db, base_sql, params, page_params, order_by=...)`（`base_sql` 与 `order_by` 须使用 DAO 白名单常量，动态条件一律走 `params` 参数绑定）。
- **敏感字段**：业务线 4 写入手机号/车牌时统一复用 `encrypt_sensitive`、`hash_sensitive_lookup` 与 `mask_phone` / `mask_plate`，保证全库加解密派生规则与脱敏口径一致。

---

## 三、 自测与答辩留痕要求

每位组员需在 `tests/` 下为自己模块补充持久层测试，重点覆盖：
- **并发与约束拦截**：同把枪同时下两单触发 `1062` → 回滚返回 `409`；单枪功率超桩功率触发 `1644`；
- **多表事务一致性**：中途故意抛错验证 `rollback()` 后数据无残留；
- **安全隔离**：验证低权限角色视图查不到高权限字段或他人隐私数据。
