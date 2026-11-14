"""认证与 RBAC 身份鉴权路由模块。

职责：
1. 提供账号密码登录（含失败次数累计、防暴破临时锁定与成功解锁重置）；
2. 提供普通车主与企业车队自助注册（高权限角色禁止自助注册）；
3. 提供当前登录用户上下文回显、系统 5 类 RBAC 角色字典查询以及开发联调 Token 签发。

约束：本路由层严格遵循分层解耦规范，仅通过 app.dao.common 访问持久化层，禁止直接操作底层会话查询。
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import (
    CurrentUser,
    RoleEnum,
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.config import settings
from app.dao import common as common_dao
from app.db import get_db
from app.schemas.common import (
    DevTokenIn,
    LoginIn,
    RegisterIn,
    RoleInfoOut,
    TokenOut,
    UserProfileOut,
)

router = APIRouter(prefix="/api/auth", tags=["认证鉴权"])

# 平台 5 类 RBAC 角色的权限范围与数据隔离说明字典
_ROLE_DESCRIPTIONS: dict[RoleEnum, str] = {
    RoleEnum.DRIVER: (
        "普通车主：可查找场站、扫码启停充电、查看本人订单/余额/优惠券、报障评价与申请电子发票；"
        "严格隔离本人数据，不可查看他人订单、明文手机号、运维工单或运营商结算单。"
    ),
    RoleEnum.OPS: (
        "场站运维工程师：可查看分配给本人的告警与维修工单、执行接单/到场/修复闭环及设备核对；"
        "不可查看车主支付流水或修改平台结算账单。"
    ),
    RoleEnum.SETTLEMENT: (
        "客服与结算专员：可处理异常充电退款与发票申诉、核对月度结算单与差异明细、办理开票与红冲；"
        "不可越权修改设备额定功率或审批营销活动。"
    ),
    RoleEnum.FLEET: (
        "企业客户/车队：可批量开卡绑车、设置用车人与额度、查看本企业车队充电明细与月结账单；"
        "严格按 fleet_id 隔离，不可查看其他企业车队及个人车主数据。"
    ),
    RoleEnum.ADMIN: (
        "平台运营管理员：可维护场站/桩/枪与运营商档案、配置分时计费规则、审核优惠活动、审批结算调账及查看全局看板；"
        "敏感数据导出受脱敏与审计策略约束。"
    ),
}


def _normalize_utc_naive(value: datetime | str | None) -> datetime | None:
    """将锁定截止时间统一标准化为 UTC 无时区 datetime，兼容字符串或带时区对象。"""
    if value is None:
        return None
    dt = datetime.fromisoformat(value) if isinstance(value, str) else value
    if dt.tzinfo is not None:
        # 统一先换算到 UTC 再剥离 tzinfo，便于与 UTC 无时区当前时间直接比较
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _account_to_current_user(account: common_dao.AccountRecord) -> CurrentUser:
    """将持久层账号记录转换为平台只读认证上下文 CurrentUser。"""
    return CurrentUser(
        user_id=account.user_id,
        username=account.username,
        role=RoleEnum(account.role),
        fleet_id=account.fleet_id,
    )


def _profile_from_user(
    user: CurrentUser,
    phone_masked: str | None = None,
) -> UserProfileOut:
    """基于认证上下文构建脱敏用户资料响应模型。"""
    return UserProfileOut(
        user_id=user.user_id,
        username=user.username,
        role=user.role,
        role_label=user.role.label,
        fleet_id=user.fleet_id,
        phone_masked=phone_masked,
    )


def _issue_token_out(
    user: CurrentUser,
    phone_masked: str | None = None,
) -> TokenOut:
    """签发无状态访问令牌并封装为标准 TokenOut 响应。"""
    token = create_access_token(user)
    return TokenOut(
        access_token=token,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=_profile_from_user(user, phone_masked=phone_masked),
    )


@router.post(
    "/login",
    response_model=TokenOut,
    summary="账号密码登录（含防暴破锁定）",
)
def login(
    payload: LoginIn,
    db: Session = Depends(get_db),
) -> TokenOut:
    """校验用户名密码并在失败超限后触发临时锁定，校验通过则重置计数并签发令牌。"""
    account = common_dao.get_account_by_username(db, payload.username)
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )

    if account.status != "ACTIVE":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账号已被禁用，请联系平台管理员",
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    locked_until = _normalize_utc_naive(account.locked_until)
    if locked_until is not None and locked_until > now:
        # 剩余锁定分钟数向上取整（至少提示 1 分钟），避免出现“0 分钟后再试”
        remain_minutes = max(1, int((locked_until - now).total_seconds() // 60) + 1)
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=f"连续登录失败次数过多，账号已锁定，请 {remain_minutes} 分钟后再试",
        )

    if not verify_password(payload.password, account.password_hash):
        new_fails = (account.failed_login_count or 0) + 1
        lock_until: datetime | None = (
            now + timedelta(minutes=settings.LOGIN_LOCK_MINUTES)
            if new_fails >= settings.LOGIN_MAX_FAILS
            else None
        )
        common_dao.record_login_failure(db, account.user_id, new_fails, lock_until)

        if lock_until is not None:
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail=(
                    f"密码连续错误 {settings.LOGIN_MAX_FAILS} 次，"
                    f"账号已锁定 {settings.LOGIN_LOCK_MINUTES} 分钟"
                ),
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"用户名或密码错误，还可尝试 {settings.LOGIN_MAX_FAILS - new_fails} 次",
        )

    common_dao.reset_login_success(db, account.user_id, now)
    current_user = _account_to_current_user(account)
    return _issue_token_out(current_user, phone_masked=account.phone_masked)


@router.post(
    "/register",
    response_model=UserProfileOut,
    status_code=status.HTTP_201_CREATED,
    summary="普通车主/企业车队自助注册",
)
def register(
    payload: RegisterIn,
    db: Session = Depends(get_db),
) -> UserProfileOut:
    """开放普通车主与企业车队自助注册，平台运维/结算/管理员账号禁止自助开通。"""
    if payload.role not in (RoleEnum.DRIVER, RoleEnum.FLEET):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="自助注册仅支持普通车主或企业车队角色",
        )

    password_hash = hash_password(payload.password)
    account = common_dao.create_account(
        db=db,
        username=payload.username,
        password_hash=password_hash,
        role=payload.role.value,
        phone_plain=payload.phone,
        fleet_id=payload.fleet_id,
    )
    return _profile_from_user(
        _account_to_current_user(account),
        phone_masked=account.phone_masked,
    )


@router.get(
    "/me",
    response_model=UserProfileOut,
    summary="获取当前登录用户信息",
)
def get_me(user: CurrentUser = Depends(get_current_user)) -> UserProfileOut:
    """解析 Bearer 令牌并返回当前已认证用户的脱敏上下文信息。"""
    return _profile_from_user(user)


@router.get(
    "/roles",
    response_model=list[RoleInfoOut],
    summary="获取系统 5 类 RBAC 角色字典",
)
def list_roles() -> list[RoleInfoOut]:
    """返回平台全部 5 类 RBAC 角色标识、中文名称及权限隔离说明。"""
    return [
        RoleInfoOut(
            role=role,
            label=role.label,
            description=_ROLE_DESCRIPTIONS[role],
        )
        for role in RoleEnum
    ]


@router.post(
    "/dev-token",
    response_model=TokenOut,
    summary="开发联调/答辩快捷签发测试 Token",
)
def issue_dev_token(payload: DevTokenIn) -> TokenOut:
    """按指定角色快捷签发测试令牌；仅企业车队角色保留 fleet_id 隔离键。"""
    fleet_id = payload.fleet_id if payload.role == RoleEnum.FLEET else None
    current_user = CurrentUser(
        user_id=payload.user_id,
        username=payload.username,
        role=payload.role,
        fleet_id=fleet_id,
    )
    return _issue_token_out(current_user)
