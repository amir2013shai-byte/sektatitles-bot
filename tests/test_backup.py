"""Проверка бэкапа/восстановления базы (нужно для бесплатных хостингов с эфемерным диском)."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402
import services as svc  # noqa: E402


async def main() -> None:
    tmp = tempfile.mkdtemp()
    db_file = os.path.join(tmp, "live.sqlite3")
    await db.init_db(db_file)

    await svc.ensure_user(1, "a", "A")
    king = await svc.get_title_by_code("king")
    await svc.give_title(1, king["id"], source="test")

    backup_path = os.path.join(tmp, "backup.sqlite3")
    await db.backup_to(backup_path)
    assert os.path.exists(backup_path) and os.path.getsize(backup_path) > 1000
    print("  ✓ бэкап создан")

    # «теряем» данные: новый пользователь после бэкапа
    await svc.ensure_user(2, "b", "B")
    assert await db.scalar("SELECT COUNT(*) FROM users") == 2

    await db.restore_from(backup_path)
    users_after = await db.scalar("SELECT COUNT(*) FROM users")
    owned_after = await db.scalar("SELECT COUNT(*) FROM inventory")
    assert users_after == 1, f"после восстановления ожидался 1 пользователь, а {users_after}"
    assert owned_after == 1, "инвентарь не восстановился"
    print("  ✓ восстановление вернуло состояние на момент бэкапа")

    # база снова рабочая — можно писать
    await svc.ensure_user(3, "c", "C")
    assert await db.scalar("SELECT COUNT(*) FROM users") == 2
    print("  ✓ после восстановления запись работает")
    await db.close_db()
    print("\nБэкап/восстановление работают.")


if __name__ == "__main__":
    asyncio.run(main())
    sys.stdout.flush()
    os._exit(0)
