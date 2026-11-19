"""业务线4 Pydantic 入参校验单测（无需数据库连接）。

覆盖：车牌归一化与格式、快充功率一致性、活动有效期与券面额上限、
核销券码归一化、代开账号的角色与车队绑定规则、脱敏出参不含敏感字段。
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.user import (
    CampaignIn,
    CouponRedeemIn,
    UserMaskedOut,
    UserRegisterIn,
    VehicleBindIn,
)


class TestVehicleBindIn:
    def _payload(self, **overrides):
        base = dict(
            plate_no="京ad12345",
            plate_color="GREEN",
            vehicle_model="比亚迪 汉 EV",
            battery_capacity_kwh=Decimal("85.40"),
            max_charge_power=Decimal("120.00"),
            supports_fast_charge=True,
        )
        base.update(overrides)
        return VehicleBindIn(**base)

    def test_plate_normalized_to_upper(self) -> None:
        item = self._payload()
        assert item.plate_no == "京AD12345"
        assert item.battery_capacity_kwh == Decimal("85.40")

    def test_reject_invalid_plate(self) -> None:
        with pytest.raises(ValidationError):
            self._payload(plate_no="AB12345")

    def test_reject_fast_charge_with_low_power(self) -> None:
        # 声明支持直流快充但功率只有 7 kW（交流慢充水平）应被拦截
        with pytest.raises(ValidationError):
            self._payload(max_charge_power=Decimal("7.00"))

    def test_slow_charge_vehicle_allowed(self) -> None:
        item = self._payload(
            plate_no="京AF00001", supports_fast_charge=False, max_charge_power=Decimal("6.60")
        )
        assert item.supports_fast_charge is False

    def test_reject_non_positive_battery(self) -> None:
        with pytest.raises(ValidationError):
            self._payload(battery_capacity_kwh=Decimal("0"))


class TestCampaignIn:
    def _payload(self, **overrides):
        today = date.today()
        base = dict(
            campaign_code="CP-202612-NEW",
            campaign_name="双旦新人立减",
            coupon_kind="CASH",
            face_value=Decimal("10.00"),
            threshold_amount=Decimal("50.00"),
            valid_from=today,
            valid_to=today + timedelta(days=30),
            issue_total=1000,
            per_user_limit=2,
            status="ACTIVE",
        )
        base.update(overrides)
        return CampaignIn(**base)

    def test_valid_campaign(self) -> None:
        item = self._payload()
        assert item.face_value == Decimal("10.00")
        assert item.coupon_kind == "CASH"

    def test_reject_reversed_window(self) -> None:
        today = date.today()
        with pytest.raises(ValidationError):
            self._payload(valid_from=today, valid_to=today - timedelta(days=1))

    def test_reject_discount_face_above_threshold(self) -> None:
        # 折扣类券面额高于门槛等于白送，应直接拦截
        with pytest.raises(ValidationError):
            self._payload(coupon_kind="DISCOUNT", face_value=Decimal("60.00"),
                          threshold_amount=Decimal("50.00"))

    def test_reject_zero_face_value(self) -> None:
        with pytest.raises(ValidationError):
            self._payload(face_value=Decimal("0.00"))


class TestCouponRedeemIn:
    def test_coupon_no_normalized(self) -> None:
        item = CouponRedeemIn(coupon_no=" cp2026110001 ", order_id=10001)
        assert item.coupon_no == "CP2026110001"

    def test_reject_non_positive_order(self) -> None:
        with pytest.raises(ValidationError):
            CouponRedeemIn(coupon_no="CP2026110001", order_id=0)


class TestUserRegisterIn:
    def test_reject_driver_role(self) -> None:
        with pytest.raises(ValidationError):
            UserRegisterIn(
                username="driver01",
                password="Demo@123456",
                phone="13800009001",
                role="driver",
            )

    def test_fleet_requires_fleet_id(self) -> None:
        with pytest.raises(ValidationError):
            UserRegisterIn(
                username="fleet01",
                password="Demo@123456",
                phone="13800009101",
                role="fleet",
            )

    def test_non_fleet_role_clears_fleet_id(self) -> None:
        item = UserRegisterIn(
            username="ops01",
            password="Demo@123456",
            phone="13800000002",
            role="ops",
            fleet_id=3,
        )
        assert item.fleet_id is None


class TestUserMaskedOut:
    def test_no_sensitive_fields(self) -> None:
        fields = set(UserMaskedOut.model_fields)
        for forbidden in ("password_hash", "phone_cipher", "phone_hash", "phone"):
            assert forbidden not in fields
        assert "phone_masked" in fields
