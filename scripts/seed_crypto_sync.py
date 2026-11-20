"""把种子数据里的“占位密文/占位盲索引”重算成与运行时一致的加密值。

背景（Issue #19 High-5）：
    `sql/04_seed.sql` 为了能在纯 SQL 里造数，把手机号/车牌写成
    `SEED_FERNET::<明文>` 与 `SHA2('seed:'||明文, 256)`。这两者与运行时使用的
    Fernet 密文、HMAC-SHA256 盲索引格式不同，会导致：
      1. 种子车牌绕开唯一约束（别人还能绑同一车牌，一车多主）；
      2. `get_user_by_phone` 查不到种子账号；
      3. `decrypt_sensitive()` 对种子数据必然抛 InvalidToken。

本脚本在种子导入后运行，按当前 `.env` 的 SECRET_KEY 重新计算：
    phone_cipher / phone_hash / phone_masked      （user_account、fleet_account）
    plate_cipher / plate_hash / plate_masked      （vehicle）
明文来自占位密文本身（去掉 `SEED_FERNET::` 前缀），因此对新增种子行同样生效。

用法：
    .venv\\Scripts\\python.exe scripts\\seed_crypto_sync.py            # 执行
    .venv\\Scripts\\python.exe scripts\\seed_crypto_sync.py --dry-run  # 只统计

脚本幂等：已经是真实 Fernet 密文的行会被跳过，可重复执行。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from sqlalchemy import text  # noqa: E402

from app.dao.common import (  # noqa: E402
    decrypt_sensitive,
    encrypt_sensitive,
    hash_sensitive_lookup,
    mask_phone,
    mask_plate,
)
from app.db import SessionLocal  # noqa: E402

PLACEHOLDER_PREFIX = "SEED_FERNET::"

# (表, 主键列, 密文列, 盲索引列, 脱敏列, 是否手机号)
TARGETS = [
    ("user_account", "user_id", "phone_cipher", "phone_hash", "phone_masked", True),
    ("fleet_account", "fleet_id", "contact_phone_cipher", "contact_phone_hash", "contact_phone_masked", True),
    ("vehicle", "vehicle_id", "plate_cipher", "plate_hash", "plate_masked", False),
]


def sync_table(db, table: str, pk: str, cipher_col: str, hash_col: str,
               masked_col: str, is_phone: bool, dry_run: bool) -> tuple[int, int]:
    """重算一张表的敏感三列，返回 (处理行数, 跳过行数)。"""
    rows = db.execute(
        text(
            f"SELECT `{pk}` AS id, `{cipher_col}` AS cipher FROM `{table}` "
            f"WHERE `{cipher_col}` LIKE :prefix"
        ),
        {"prefix": f"{PLACEHOLDER_PREFIX}%"},
    ).mappings().all()

    if not rows:
        return 0, 0

    if dry_run:
        return len(rows), 0

    for row in rows:
        plain = str(row["cipher"])[len(PLACEHOLDER_PREFIX):].strip()
        masked = mask_phone(plain) if is_phone else mask_plate(plain)
        db.execute(
            text(
                f"UPDATE `{table}` SET "
                f"`{cipher_col}` = :cipher, `{hash_col}` = :hash, `{masked_col}` = :masked "
                f"WHERE `{pk}` = :id"
            ),
            {
                "cipher": encrypt_sensitive(plain),
                "hash": hash_sensitive_lookup(plain),
                "masked": masked,
                "id": row["id"],
            },
        )

    db.commit()
    return len(rows), 0


def verify(db) -> list[str]:
    """校验：占位密文应清零、盲索引应可反查、密文应可解密。返回问题列表。"""
    problems: list[str] = []

    for table, pk, cipher_col, hash_col, masked_col, is_phone in TARGETS:
        left = db.execute(
            text(
                f"SELECT COUNT(*) FROM `{table}` WHERE `{cipher_col}` LIKE :prefix"
            ),
            {"prefix": f"{PLACEHOLDER_PREFIX}%"},
        ).scalar_one()
        if int(left):
            problems.append(f"{table} 仍有 {left} 行占位密文")

        sample = db.execute(
            text(f"SELECT `{pk}` AS id, `{cipher_col}` AS cipher, `{hash_col}` AS hash FROM `{table}` LIMIT 1")
        ).mappings().first()
        if sample is None:
            continue
        plain = decrypt_sensitive(str(sample["cipher"]))
        if hash_sensitive_lookup(plain) != str(sample["hash"]):
            problems.append(f"{table} 的密文与盲索引不匹配（{pk}={sample['id']}）")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只统计待处理行数")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        total = 0
        for table, pk, cipher_col, hash_col, masked_col, is_phone in TARGETS:
            count, _ = sync_table(db, table, pk, cipher_col, hash_col, masked_col, is_phone, args.dry_run)
            total += count
            print(f"{table:<16} 重算 {count} 行")

        if args.dry_run:
            print(f"待处理合计 {total} 行（dry-run，未写库）")
            return 0

        problems = verify(db)
        if problems:
            print("校验未通过：")
            for item in problems:
                print("  -", item)
            return 1

        print(f"完成：共重算 {total} 行，占位密文已清零，密文/盲索引/脱敏三列自洽")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
