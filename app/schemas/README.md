# `app/schemas/` —— Pydantic v2 数据契约与校验层规范

> **对应任务书要求**：《新能源汽车充电桩运营服务平台》第 2.1 / 3.4 节（敏感数据脱敏、跨时段核价与业务边界校验）

---

## 一、 本层职责与开发铁律

1. **入参前置校验**：
   利用 Pydantic `Field(...)` 与 `@field_validator` 在进入数据库前先拦截明显非法请求：
   - 额定功率 / 充电电量 / 优惠券面额 / 支付与开票金额必须 `> 0`（或 `>= 0`）；
   - `start_soc` / `end_soc` 限制在 `0 ~ 100`；
   - 查询区间须校验 `end_time > start_time`；
   - 桩/枪状态、告警等级、差异类型（电量不符 / 价格不符 / 支付失败）使用 `Literal` 或 `Enum` 限定。
2. **金额与电量精度不丢失**：
   响应模型中的电量、电价、服务费、金额字段必须声明为 `decimal.Decimal`，**严禁转成 `float`**（避免分时计费与月结对账产生精度误差）。
3. **敏感信息强制脱敏输出**：
   任务书明确要求**手机号与车牌号须加密存储、脱敏展示并限制导出权限**。所有面向车主、运维或普通列表的 Out Schema 只能输出脱敏字段（如 `138****5678`、`京A*****5`），禁止直接透出明文。
4. **ORM 模型自动转换**：
   凡是从 ORM 对象直接序列化的响应模型，统一配置：
   ```python
   model_config = ConfigDict(from_attributes=True)
   ```

---

## 二、 公共模块与 5 条业务线 Schema 划分

| 文件 | 模块 / 业务线 | 核心请求 / 响应模型 | 重点校验 / 脱敏规则 |
| :--- | :--- | :--- | :--- |
| [`common.py`](common.py) | **全组公共底座（已实现）** | `PageParams`、`PageResult[T]`、`ActionResult`、`LoginIn`、`RegisterIn`、`DevTokenIn`、`RoleInfoOut`、`UserProfileOut`、`TokenOut` | `PageParams` 约束 `page >= 1`、`1 <= page_size <= 100`；`RegisterIn` 校验 `fleet` 必传 `fleet_id` 且非车队角色自动置空；`UserProfileOut` 仅输出 `phone_masked` |
| `station.py` | 业务线 1 | `StationCreate`、`PileCreate`、`GunCreate`、`GunOut`、`DeviceStatusLogOut` | 校验 `gun.max_power <= pile.rated_power`；营业时间格式规范 |
| `order.py` | 业务线 2 | `PricingRuleCreate`、`OrderStartIn`、`OrderFinishOut`、`FeeDetailOut`、`PaymentCreate` | 启停 SOC 范围校验；分时明细列表与总金额保留 `Decimal` |
| `monitor.py` | 业务线 3 | `TelemetryIn`、`AlarmOut`、`RepairDispatchIn`、`RepairCompleteIn` | 电压/电流/温度合理区间；派单/到场/修复时间节点递增校验 |
| `user.py` | 业务线 4 | `UserRegisterIn`、`UserMaskedOut`、`VehicleBindIn`、`FleetCardIn`、`CouponRedeemIn` | 输出只包含脱敏手机号与车牌；快充支持与最大功率校验 |
| `settle.py` | 业务线 5 | `SettlementGenerateIn`、`SettlementOut`、`ReconcileDiffIn`、`InvoiceCreateIn`、`InvoiceRedOffsetIn` | 账期格式 `YYYY-MM`；开票金额 `> 0` 且不得超过应结金额 |

### 公共分页与回执复用示例

各业务线列表接口统一通过 [`common.py`](common.py) 接收分页参数并返回泛型分页结果：

```python
from fastapi import Depends
from app.schemas.common import ActionResult, PageParams, PageResult

@router.get("/items", response_model=PageResult[ItemOut], summary="分页查询列表")
def list_items(page: PageParams = Depends(), db: Session = Depends(get_db)) -> PageResult[ItemOut]:
    return PageResult[ItemOut](**dao.list_items_page(db, page))
```
