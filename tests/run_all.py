"""Запуск всех тестов: python tests/run_all.py"""
from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FAILED = []

for name in ("test_logic.py", "test_backup.py", "test_bot_smoke.py", "test_webhook.py"):
    print(f"\n=== {name} ===")
    result = subprocess.run([sys.executable, os.path.join(HERE, name)])
    if result.returncode != 0:
        FAILED.append(name)

print("\n" + "=" * 40)
if FAILED:
    print("ПРОВАЛЕНО:", ", ".join(FAILED))
    sys.exit(1)
print("ВСЕ ТЕСТЫ ПРОЙДЕНЫ ✅")
