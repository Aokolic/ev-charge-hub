"""全组公共 Pydantic v2 数据契约与分页参数模块。

职责：
1. 提供全组统一的 FastAPI 分页入参依赖（PageParams）与泛型分页响应模型（PageResult）；
2. 提供写操作通用回执模型（ActionResult）；
3. 提供认证鉴权模块（/api/auth/*）所需的登录、注册、测试签发 Token、角色字典与用户画像契约。
"""

from typing import Generic, Self, TypeVar

from fastapi import Query
from fastapi.params import Query as _QueryParam
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.auth import RoleEnum

# 泛型类型变量，供 PageResult[ItemSchema] 声明当前页元素类型
T = TypeVar("T")


class PageParams:
    """FastAPI 统一分页查询依赖，供列表接口通过 `page: PageParams = Depends()` 注入。"""

    def __init__(
        self,
        page: int = Query(default=1, ge=1, description="当前页码（从 1 开始）"),
        page_size: int = Query(default=10, ge=1, le=100, description="每页记录数（1~100）"),
    ) -> None:
        # 兼容脱离 FastAPI 注入直接调用 PageParams() 的单元测试场景，解包 Query 默认值
        raw_page = page.default if isinstance(page, _QueryParam) else page
        raw_page_size = page_size.default if isinstance(page_size, _QueryParam) else page_size
        self.page: int = int(raw_page)
        self.page_size: int = int(raw_page_size)

    @property
    def offset(self) -> int:
        """计算 SQL LIMIT/OFFSET 所需的起始偏移量。"""
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        """返回单页最大拉取条数，语义对齐 SQL LIMIT 子句。"""
        return self.page_size


class PageResult(BaseModel, Generic[T]):
    """全组统一的泛型分页响应结构。"""

    total: int = Field(ge=0, description="符合条件的总记录数")
    page: int = Field(ge=1, description="当前页码（从 1 开始）")
    page_size: int = Field(ge=1, description="每页记录数")
    items: list[T] = Field(default_factory=list, description="当前页数据列表")


class ActionResult(BaseModel):
    """通用写操作结果回执模型（适用于状态变更、工单流转、审批等无复杂实体返回的接口）。"""

    success: bool = Field(default=True, description="操作是否成功")
    message: str = Field(description="操作结果提示信息")
    id: int | str | None = Field(default=None, description="关联业务主键 ID 或单据编号")


class LoginIn(BaseModel):
    """账号密码登录请求体。"""

    username: str = Field(min_length=2, max_length=50, description="登录用户名")
    password: str = Field(min_length=6, max_length=128, description="登录密码")


class RegisterIn(BaseModel):
    """普通车主自助注册请求体（保留 role 字段以对越权角色返回明确 403）。"""

    username: str = Field(
        min_length=2,
        max_length=50,
        pattern=r"^[a-zA-Z0-9_\u4e00-\u9fa5]+$",
        description="用户名（2~50位字母、数字、下划线或中文）",
    )
    password: str = Field(min_length=6, max_length=128, description="登录密码（6~128位）")
    phone: str = Field(pattern=r"^1[3-9]\d{9}$", description="11位中国大陆手机号")
    role: RoleEnum = Field(default=RoleEnum.DRIVER, description="注册角色（自助注册仅支持普通车主）")
    fleet_id: int | None = Field(default=None, ge=1, description="所属企业车队 ID（自助注册忽略该字段）")

    @model_validator(mode="after")
    def _validate_fleet_binding(self) -> Self:
        """自助注册不再允许绑定企业车队，统一清空 fleet_id；非 driver 角色交由路由层返回 403。"""
        self.fleet_id = None
        return self


class DevTokenIn(BaseModel):
    """开发联调与答辩演示快捷签发测试 Token 请求体。"""

    user_id: int = Field(default=1, ge=1, description="模拟用户 ID")
    username: str = Field(default="demo_driver", min_length=1, max_length=50, description="模拟用户名")
    role: RoleEnum = Field(default=RoleEnum.DRIVER, description="模拟 RBAC 角色")
    fleet_id: int | None = Field(default=None, ge=1, description="所属企业车队 ID（仅企业车队角色有效）")


class RoleInfoOut(BaseModel):
    """系统 RBAC 角色字典项响应模型。"""

    role: RoleEnum = Field(description="角色枚举标识")
    label: str = Field(description="角色中文名称")
    description: str = Field(description="角色核心职责与数据隔离边界说明")


class UserProfileOut(BaseModel):
    """当前登录用户基础画像响应模型（仅输出脱敏手机号）。"""

    model_config = ConfigDict(from_attributes=True)

    user_id: int = Field(description="用户 ID")
    username: str = Field(description="用户名")
    role: RoleEnum = Field(description="角色枚举标识")
    role_label: str = Field(description="角色中文名称")
    fleet_id: int | None = Field(default=None, description="所属企业车队 ID（非车队用户为 None）")
    phone_masked: str | None = Field(default=None, description="脱敏手机号（如 138****5678）")


class TokenOut(BaseModel):
    """JWT 登录与测试签发统一响应模型。"""

    access_token: str = Field(description="HS256 Bearer 访问令牌")
    token_type: str = Field(default="bearer", description="令牌类型（固定 bearer）")
    expires_in: int = Field(description="令牌有效期（秒）")
    user: UserProfileOut = Field(description="当前登录用户基础画像")


__all__ = [
    "T",
    "PageParams",
    "PageResult",
    "ActionResult",
    "LoginIn",
    "RegisterIn",
    "DevTokenIn",
    "RoleInfoOut",
    "UserProfileOut",
    "TokenOut",
]
