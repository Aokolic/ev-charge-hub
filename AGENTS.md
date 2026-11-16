# AGENT Instructions — 新能源汽车充电桩运营服务平台 (`ev-charge-hub`)

## 1. 项目概览与技术栈
- **课程背景**：北京林业大学《数据库系统课程设计》（2026–2027 学年第 1 学期，4 人小组项目）。
- **技术栈**：Python 3.14（`uv` 虚拟环境 `.venv`）+ FastAPI + SQLAlchemy 2.0（`mysql+pymysql`）+ MySQL 8.0/9.x（InnoDB，`utf8mb4`）+ 静态单页前端（`static/`）。
- **编码与注释**：所有源码与 SQL 统一使用 **UTF-8** 编码；核心逻辑、关键参数与复杂 SQL 须附清晰**中文注释**。

## 2. 目录结构与公共底座

### 2.1 数据库真源 `sql/`（按顺序执行）
`01_ddl.sql`（建表/索引/生成列/分区）→ `02_views.sql`（每线 ≥3 个 RBAC 隔离视图）→ `03_procs_triggers.sql`（每线 ≥1 存储过程 + ≥1 触发器）→ `04_seed.sql`（每表 ≥20 条测试数据）→ `05_queries_compare.sql`（每线 ≥3 组双实现与 `EXPLAIN ANALYZE` 对比）。

### 2.2 全组公共底座（严禁业务线重复造轮子）
| 模块 | 职责与核心导出 |
| :--- | :--- |
| [app/config.py](app/config.py) | 只读配置单例 `settings`（读取 `.env`，含 `DATABASE_URL`、`SECRET_KEY`、`LOGIN_MAX_FAILS` 等） |
| [app/db.py](app/db.py) | SQLAlchemy 2.0 连接池、`Base` 基类与请求级会话依赖 `get_db()` |
| [app/auth.py](app/auth.py) | 5 类角色枚举 `RoleEnum`、`CurrentUser`、PBKDF2 密码哈希、HS256 JWT 及守卫 `get_current_user` / `require_roles` |
| [app/main.py](app/main.py) | FastAPI 总入口、全局 MySQL 异常翻译（`1062/1451/1452/3819/1644`）、`/health/db`、`/static` 挂载与路由装配点 |
| [app/schemas/common.py](app/schemas/common.py) | 统一分页 `PageParams` / `PageResult[T]`、通用回执 `ActionResult` 及认证鉴权契约模型 |
| [app/dao/common.py](app/dao/common.py) | 视图参数化分页 `fetch_view_page`、脱敏 `mask_phone`/`mask_plate`、Fernet 加解密、HMAC 盲索引及认证账号 DAO |
| [app/routers/auth.py](app/routers/auth.py) | `/api/auth` 公共认证路由（`POST /login` 防暴破锁定、`POST /register`、`GET /me`、`GET /roles`、`POST /dev-token`） |

### 2.3 分层规范文档（修改对应目录代码前先对齐）
- `models`：[README.md](app/models/README.md)（SQLAlchemy 2.0 ORM 映射，仅映射已有表）
- `schemas`：[README.md](app/schemas/README.md)（Pydantic v2 入参校验、脱敏输出、`Decimal` 精度与 `common.py` 分页契约）
- `dao`：[README.md](app/dao/README.md)（ORM CRUD + `text()` 调视图/存储过程、显式事务边界与 `common.py` 复用）
- `routers`：[README.md](app/routers/README.md)（`APIRouter` 路由定义、5 类角色 RBAC 矩阵与 `auth.py` 鉴权入口）

## 3. 跨线共享契约（联调对齐基准）
1. **统一角色标识（`app/auth.py` `RoleEnum`）**：数据库与接口统一使用小写字符串 `driver`（普通车主）、`ops`（场站运维工程师）、`settlement`（客服与结算专员）、`fleet`（企业客户/车队）、`admin`（平台运营管理员）。
2. **`user_account` 表字段契约（供 `sql/01_ddl.sql` 与业务线 4 对齐）**：[app/dao/common.py](app/dao/common.py) 认证持久化依赖 `user_id`、`username`、`password_hash`、`role`、`fleet_id`、`phone_cipher`、`phone_hash`、`phone_masked`、`status`（默认 `'ACTIVE'`）、`failed_login_count`、`locked_until`、`last_login_at`、`created_at`、`updated_at`，建表时字段名与语义须保持一致。

## 4. 5 条业务线分工与 Git 分支协作规范

| 业务线 | 模块名 | 负责人 | 开发分支名 | 核心业务范围 |
| :--- | :--- | :--- | :--- | :--- |
| 业务线 1 | `station` | 成员 1（组长，李武希） | `01-develop` | 场站 → 充电桩 → 充电枪三级资产 + 状态变更日志 + 全局整合/前端 |
| 业务线 2 | `order` | 成员 2（孙墨涵） | `02-develop` | 分时计费规则 + 订单（生成列防同枪并发）+ 分时费用明细 + 1:N 支付退款 |
| 业务线 3 | `monitor` | 成员 3（代润森） | `03-develop` | 分钟级遥测分区表 + 告警分级升级 + 1:N 维修工单闭环 |
| 业务线 4 | `user` | 成员 4（李海波） | `04-develop` | 车主与车辆（敏感字段加密脱敏、1 车 1 主）+ 企业车队 + 优惠券发/核（1:1 防重） |
| 业务线 5 | `settle` | 成员 2（孙墨涵） | `05-develop` | 运营商按月唯一结算单 + 对账差异回指订单 + 开票与红冲（禁删原票） |

- **分支开发流程**：**严禁直接在 `main` 分支开发或直接推送**。开发前先同步最新 `main` 并切出对应分支（命名格式 `{业务线序号}-develop`，如 `01-develop`）；各成员仅修改自己模块对应的 `<module>.py` 及所属 `tests/` 用例。
- **PR 与跨线协同**：在个人分支完成自测后提交 **Pull Request** 合并至 `main`；若遇到公共底座缺失或需其他业务线字段/接口配合，请在 **GitHub Issues** 提出或在群内 `@` 相关成员协商，严禁擅自改动他人模块或公共文件。

## 5. 开发与运行铁律（严禁踩坑）
1. **数据库先行**：严禁调用 `Base.metadata.create_all()` 自动建表；所有结构变更以 `sql/01_ddl.sql` 为唯一真源。
2. **双轨查询与隔离**：原始表禁止直出前端，列表/看板优先通过 `text()` 查询 RBAC 隔离视图；严禁使用 f-string 拼接任何用户输入到 SQL 中。
3. **精度保留**：金额、电价、电量、功率在数据库使用 `DECIMAL`，Python 侧全程保持 `Decimal`，严禁转为 `float`。
4. **事务与异常**：跨表写操作须在同一 `Session` 内一次 `db.commit()`；底层 `IntegrityError`（`1062` 冲突、`1451/1452` 外键、`3819` CHECK）与 `OperationalError`（`1644` 触发器 `SIGNAL`）直接向上抛出交由 [app/main.py](app/main.py) 统一转 HTTP 409/400（若内部捕获须先 `db.rollback()` 再上抛）。
5. **路由挂载规范**：各模块已在 `APIRouter(prefix=...)` 声明前缀，挂载到 [app/main.py](app/main.py) 时直接 `app.include_router(<module>.router)`，切勿重复传 `prefix`；联调期间可通过 `POST /api/auth/dev-token` 一键获取 5 类角色测试 Token。
6. **安全与环境**：严禁将 `.env` 或 `.idea/` 个人配置提交至 Git（新增环境变量须同步更新 `.env.example`）；本地运行脚本与服务统一使用 `.venv` 解释器。

## 6. 常用开发命令速查
- **启动本地服务**：`.venv\Scripts\uvicorn.exe app.main:app --reload`（接口文档：`http://127.0.0.1:8000/docs`；数据库探活：`GET /health/db`）
- **运行脚本 / 自测**：`.venv\Scripts\python.exe <script_or_test.py>`
