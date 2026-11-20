"""业务线3 Pydantic 入参/出参契约单测（无需数据库连接）。"""

import unittest
from datetime import datetime
from decimal import Decimal

from pydantic import ValidationError

from app.schemas.monitor import (
    AlarmOut,
    EscalateIn,
    RealtimeTelemetryOut,
    RepairCompleteIn,
    RepairDispatchIn,
    RepairOrderOut,
    TelemetryIn,
)


class TelemetrySchemaTest(unittest.TestCase):
    def test_valid_telemetry_keeps_decimal(self) -> None:
        item = TelemetryIn(
            pile_id=1,
            collected_at=datetime(2026, 11, 15, 8, 0, 0),
            output_voltage=Decimal("420.0"),
            output_current=Decimal("52.00"),
            power=Decimal("21.840"),
            module_temp=Decimal("39.2"),
        )
        self.assertEqual(item.power, Decimal("21.840"))

    def test_blank_fault_code_becomes_none(self) -> None:
        item = TelemetryIn(
            pile_id=1,
            collected_at=datetime(2026, 11, 15, 8, 0, 0),
            power=Decimal("0"),
            fault_code="   ",
        )
        self.assertIsNone(item.fault_code)

    def test_reject_negative_or_excessive_power(self) -> None:
        for value in (Decimal("-1"), Decimal("1000.001")):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                TelemetryIn(
                    pile_id=1,
                    collected_at=datetime(2026, 11, 15, 8, 0, 0),
                    power=value,
                )

    def test_reject_temperature_out_of_range(self) -> None:
        with self.assertRaises(ValidationError):
            TelemetryIn(
                pile_id=1,
                collected_at=datetime(2026, 11, 15, 8, 0, 0),
                power=Decimal("0"),
                module_temp=Decimal("200"),
            )

    def test_reject_excessive_offline_duration(self) -> None:
        with self.assertRaises(ValidationError):
            TelemetryIn(
                pile_id=1,
                collected_at=datetime(2026, 11, 15, 8, 0, 0),
                power=Decimal("0"),
                offline_duration_sec=31_536_001,
                fault_code="E_OFFLINE",
            )

    def test_offline_duration_requires_fault_code(self) -> None:
        with self.assertRaises(ValidationError):
            TelemetryIn(
                pile_id=1,
                collected_at=datetime(2026, 11, 15, 8, 0, 0),
                power=Decimal("0"),
                offline_duration_sec=10,
            )


class ResponseSchemaTest(unittest.TestCase):
    def test_realtime_telemetry_out(self) -> None:
        item = RealtimeTelemetryOut.model_validate(
            {
                "pile_id": 1,
                "station_id": 1,
                "station_code": "ST-BJ-001",
                "station_name": "测试场站",
                "pile_code": "PL-001-01",
                "pile_status": "IDLE",
                "collected_at": datetime(2026, 11, 15, 8, 0, 0),
                "output_voltage": Decimal("420.0"),
                "output_current": Decimal("52.00"),
                "power": Decimal("21.840"),
                "module_temp": Decimal("39.2"),
                "offline_duration_sec": 0,
                "fault_code": None,
                "heartbeat_lag_sec": 12,
            }
        )
        self.assertEqual(item.power, Decimal("21.840"))

    def test_alarm_out_keeps_dashboard_fields(self) -> None:
        item = AlarmOut.model_validate(
            {
                "alarm_id": 1,
                "pile_id": 6,
                "gun_id": 11,
                "alarm_code": "E_CONTACTOR_STUCK",
                "alarm_level": "L1",
                "original_level": "L1",
                "response_limit_min": 15,
                "occurred_at": datetime(2026, 11, 16, 9, 30, 0),
                "recovered_at": None,
                "status": "ACTIVE",
                "escalated_count": 0,
                "escalated_at": None,
                "latest_maintainer_id": 2,
                "overdue_flag": 1,
            }
        )
        self.assertTrue(item.overdue_flag)
        self.assertEqual(item.latest_maintainer_id, 2)

    def test_repair_order_out_keeps_sla_fields(self) -> None:
        item = RepairOrderOut.model_validate(
            {
                "work_order_id": 1,
                "alarm_id": 1,
                "maintainer_id": 2,
                "status": "DISPATCHED",
                "dispatched_at": datetime(2026, 11, 16, 9, 32, 0),
                "accepted_at": None,
                "arrived_at": None,
                "repaired_at": None,
                "verified_at": None,
                "closed_at": None,
                "replaced_parts": None,
                "result": None,
                "response_minutes": 20,
                "overdue_flag": 1,
            }
        )
        self.assertTrue(item.overdue_flag)
        self.assertEqual(item.response_minutes, 20)


class RequestSchemaTest(unittest.TestCase):
    def test_dispatch_body_no_longer_repeats_alarm_id(self) -> None:
        item = RepairDispatchIn(maintainer_id=2)
        self.assertFalse(hasattr(item, "alarm_id"))

    def test_escalate_defaults_to_now_in_dao(self) -> None:
        self.assertIsNone(EscalateIn().check_time)

    def test_valid_repair_complete(self) -> None:
        item = RepairCompleteIn(
            repaired_at=datetime(2026, 11, 16, 9, 30, 0),
            replaced_parts="主接触器",
            result="更换主接触器并测试通过",
        )
        self.assertEqual(item.result, "更换主接触器并测试通过")

    def test_reject_short_result(self) -> None:
        with self.assertRaises(ValidationError):
            RepairCompleteIn(result="x")


if __name__ == "__main__":
    unittest.main()
