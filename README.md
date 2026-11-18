# 新能源汽车充电桩运营服务平台（`ev-charge-hub`）

北京林业大学《数据库系统课程设计》4 人小组项目后端与演示前端仓库。

## 1. 技术栈与核心约束

- **运行环境**：Python 3.14（`uv` `.venv`）+ FastAPI + SQLAlchemy 2.0（`mysql+pymysql`）+ MySQL 8.0 / 9.x（InnoDB，`utf8mb4_unicode_ci`）
- **数据库真源**：`sql/01_ddl.sql` → `02_views.sql` → `03_procs_triggers.sql` → `04_seed.sql` → `05_queries_compare.sql`（严禁调用 `Base.metadata.create_all()`）
- **核心规范**：金额/电价/电量/功率/经纬度全程保持 `DECIMAL` / `Decimal`；列表查询走 RBAC 隔离视图与参数绑定；全局入口自动将 MySQL `1062`/`1451`/`1452`/`3819`/`1644` 翻译为标准 HTTP 错误。

## 2. 业务线分工与目录结构

| 业务线 / 模块 | 分支 | 负责人 | 核心范围 |
| :--- | :--- | :--- | :--- |
| 公共底座 & 前端 | `main` / `01-develop` | 李武希（组长） | 配置、连接池、RBAC JWT 鉴权、异常转译、静态单页前端 `static/` |
| 业务线 1 `station` | `01-develop` | 李武希 | 运营商 → 场站 → 充电桩 → 充电枪三级资产 + 状态日志 |
| 业务线 2 `order` | `02-develop` | 孙墨涵 | 分时电价规则 + 充电订单（生成列防同枪并发）+ 分时明细 + 支付退款 |
| 业务线 3 `monitor` | `03-develop` | 代润森 | 设备遥测分区表 + 告警分级升级 + 维修工单闭环 |
| 业务线 4 `user` | `04-develop` | 李海波 | 车主与车辆（加密脱敏、1 车 1 主）+ 企业车队 + 优惠券发/核 |
| 业务线 5 `settle` | `05-develop` | 孙墨涵 | 运营商月度结算单 + 对账差异 + 开票与红冲 |

## 3. 快速启动与测试

1. 复制环境配置模板并填写本地数据库连接信息：
   ```powershell
   Copy-Item .env.example .env
   ```
2. 按顺序导入 `sql/01_ddl.sql` ~ `sql/04_seed.sql`。
3. 启动本地服务（首页入口 `http://127.0.0.1:8000/`，Swagger 文档 `http://127.0.0.1:8000/docs`）：
   ```powershell
   .venv\Scripts\uvicorn.exe app.main:app --reload
   ```
4. 运行自动化验收测试（使用独立临时库，不污染开发库）：
   ```powershell
   .venv\Scripts\python.exe -m unittest discover -s tests -v
   ```
