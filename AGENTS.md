# AGENT Instructions — 新能源汽车充电桩运营服务平台 (`ev-charge-hub`)

## 1. 项目概览与技术栈
- **课程背景**：北京林业大学《数据库系统课程设计》（2026–2027 学年第 1 学期，4 人小组项目）。
- **技术栈**：Python 3.14（`uv` 虚拟环境 `.venv`）+ FastAPI + SQLAlchemy 2.0（`mysql+pymysql`）+ MySQL 8.0/9.x（InnoDB，`utf8mb4`）+ 静态单页前端（`static/`）。
- **编码与注释**：所有源码与 SQL 统一使用 **UTF-8** 编码，核心逻辑、关键参数与复杂 SQL 须附清晰**中文注释**。

## 2. 目录结构与分层规范
- **数据库真源 `sql/`**（按顺序执行）：`01_ddl.sql`（建表/索引/生成列/分区）→ `02_views.sql`（每线 ≥3 个 RBAC 隔离视图）→ `03_procs_triggers.sql`（每线 ≥1 存储过程 + ≥1 触发器）→ `04_seed.sql`（每表 ≥20 条测试数据）→ `05_queries_compare.sql`（每线 ≥3 组双实现与 `EXPLAIN` 对比）。
- **公共底座**：[app/config.py](app/config.py)（只读配置单例）、[app/db.py](app/db.py)（连接池与 `get_db()`）、[app/auth.py](app/auth.py)（鉴权与 RBAC 守卫）、[app/main.py](app/main.py)（FastAPI 入口、全局 MySQL 异常翻译、`/health/db` 与 `static/` 挂载）。
- **分层规范文档**（修改对应目录代码前先对齐）：
  - `models`：[README.md](app/models/README.md)（SQLAlchemy 2.0 表映射，仅映射已有表）
  - `schemas`：[README.md](app/schemas/README.md)（Pydantic v2 入参校验、脱敏输出与 `Decimal` 精度）
  - `dao`：[README.md](app/dao/README.md)（ORM CRUD + `text()` 调视图/存储过程，显式事务边界）
  - `routers`：[README.md](app/routers/README.md)（`APIRouter` 路由定义与 5 类角色 RBAC 矩阵）

## 3. 5 条业务线模块命名与分工边界
各层均使用同名模块文件（`<module>.py`），按成员分工修改对应模块：
1. `station`（成员 1 / 组长）：场站 → 充电桩 → 充电枪三级资产 + 状态变更日志 + 全局整合/前端
2. `order`（成员 2）：分时计费规则 + 订单（生成列防同枪并发）+ 分时费用明细 + 1:N 支付退款
3. `monitor`（成员 4）：分钟级遥测分区表 + 告警分级升级 + 1:N 维修工单闭环
4. `user`（成员 3）：车主与车辆（敏感字段加密脱敏、1 车 1 主）+ 企业车队 + 优惠券发/核（1:1 防重）
5. `settle`（成员 4）：运营商按月唯一结算单 + 对账差异回指订单 + 开票与红冲（禁删原票）

## 4. 开发与运行铁律（严禁踩坑）
1. **数据库先行**：严禁调用 `Base.metadata.create_all()` 自动建表；所有结构变更以 `sql/01_ddl.sql` 为准。
2. **双轨查询与隔离**：原始表不直出前端，列表/看板优先通过 `text()` 查询 RBAC 隔离视图；严禁 f-string 拼接 SQL 参数。
3. **精度保留**：金额、电价、电量、功率在数据库用 `DECIMAL`，Python 侧全程保持 `Decimal`，禁止转成 `float`。
4. **事务与异常**：跨表写操作须在同一 `Session` 内一次 `db.commit()`；底层 `IntegrityError`（`1062` 冲突、`1451/1452` 外键、`3819` CHECK）与 `OperationalError`（`1644` 触发器 `SIGNAL`）放行交由 `app/main.py` 统一转 HTTP 409/400。
5. **安全与本地环境**：严禁将 `.env` 提交至 Git（新增配置同步更新 `.env.example`）；本地执行脚本或启动服务使用 `.venv\Scripts\python.exe` / `.venv\Scripts\uvicorn.exe`。