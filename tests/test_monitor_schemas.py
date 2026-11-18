"""业务线3 Pydantic 入参校验单测（无需数据库连接）。"""

from datetime import datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.monitor import RepairCompleteIn, TelemetryIn


class TestTelemetryIn:
    def test_valid_telemetry(self) -> None:
        item = TelemetryIn(
            pile_id=1,
            collected_at=datetime(2026, 11, 15, 8, 0, 0),
            output_voltage=Decimal("420.0"),
            output_current=Decimal("52.00"),
            power=Decimal("21.840"),
            module_temp=Decimal("39.2"),
        )
        assert item.power == Decimal("21.840")

    def test_reject_negative_power(self) -> None:
        with pytest.raises(ValidationError):
            TelemetryIn(
                pile_id=1,
                collected_at=datetime(2026, 11, 15, 8, 0, 0),
                power=Decimal("-1"),
            )

    def test_reject_temperature_out_of_range(self) -> None:
        with pytest.raises(ValidationError):
            TelemetryIn(
                pile_id=1,
                collected_at=datetime(2026, 11, 15, 8, 0, 0),
                power=Decimal("0"),
                module_temp=Decimal("200"),
            )

    def test_offline_duration_requires_fault_code(self) -> None:
        with pytest.raises(ValidationError):
            TelemetryIn(
                pile_id=1,
                collected_at=datetime(2026, 11, 15, 8, 0, 0),
                power=Decimal("0"),
                offline_duration_sec=10,
            )


class TestRepairCompleteIn:
    def test_valid_complete(self) -> None:
        item = RepairCompleteIn(
            repaired_at=datetime(2026, 11, 16, 9, 30, 0),
            replaced_parts="主接触器",
            result="更换主接触器并测试通过",
        )
        assert item.result == "更换主接触器并测试通过"

    def test_reject_empty_result(self) -> None:
        with pytest.raises(ValidationError):
            RepairCompleteIn(result="x")
