#!/usr/bin/env python3
"""
Автодеплой бота титулов на Bot-Hosting.net через их REST API.

Что делает:
  1. проверяет ключ и квоту;
  2. создаёт деплой (Python, 256 МБ, blank) или переиспользует существующий;
  3. заливает все файлы проекта одним запросом (files/writeMany);
  4. прописывает переменные окружения (BOT_TOKEN, OWNER_IDS, ...);
  5. настраивает запуск (entry main.py + pip install из requirements.txt);
  6. стартует, ждёт и печатает логи + диагноз.

Запуск:
    export BH_API_KEY=bhk_...
    python3 deploy_bothosting.py            # деплой
    python3 deploy_bothosting.py --dry-run  # показать план без запросов
    python3 deploy_bothosting.py --logs     # только логи/диагноз существующего деплоя

Ключ создаётся на https://bot-hosting.net/dash/developer
Нужные scopes: deployments:read, deployments:write, deployments:power,
               deployments:shell, files:read, files:write, env:read, env:write
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://bot-hosting.net/api/v1"
NAME = os.environ.get("BH_DEPLOY_NAME", "sektatitles")

# корень проекта = папка, где лежит этот скрипт
ROOT = Path(__file__).resolve().parent

# что не отправляем на хостинг
SKIP_DIRS = {".git", "__pycache__", "data", "tests", ".venv", "venv", "uploads"}
SKIP_FILES = {
    ".env", "render.yaml", "fly.toml", "Dockerfile", "docker-compose.yml",
    "Procfile", "runtime.txt", ".gitignore", "deploy_bothosting.py",
    "README.md", "RENDER.md", "HOSTING.md", ".env.example",
}

ENV_VARS = {
    "BOT_TOKEN": os.environ.get("BOT_TOKEN", ""),
    "OWNER_IDS": os.environ.get("OWNER_IDS", ""),
    "START_COINS": "50",
    "DAILY_BONUS": "75",
    "PAYMENTS_ENABLED": "1",
    "AUTO_BACKUP_HOURS": "24",
    "LOG_LEVEL": "INFO",
    "PYTHONUNBUFFERED": "1",
}
SECRETS = {"BOT_TOKEN"}


def collect_files() -> list[dict[str, str]]:
    """Все текстовые файлы проекта: [{path: '/main.py', content: '...'}]"""
    out: list[dict[str, str]] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if path.name in SKIP_FILES or path.suffix in {".sqlite3", ".pyc", ".zip"}:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # бинарники не нужны
        out.append({"path": "/" + rel.as_posix(), "content": content, "mode": "overwrite"})
    return out


def api(method: str, endpoint: str, payload: dict | None = None, key: str = "") -> tuple[int, dict]:
    url = f"{API}{endpoint}" if endpoint.startswith("/") else f"{API}/{endpoint}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "sektatitles-deploy/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read().decode()
            return resp.status, (json.loads(body) if body.strip() else {})
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"raw": body}
    except urllib.error.URLError as exc:
        return 0, {"error": str(exc)}


def find_deployment(key: str) -> dict | None:
    status, body = api("GET", "/deployments?brief=true", key=key)
    if status != 200:
        print(f"  ! список деплоев недоступен ({status}): {body}")
        return None
    for dep in body.get("deployments", []):
        if isinstance(dep, dict) and dep.get("name") == NAME:
            return dep
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--logs", action="store_true")
    args = parser.parse_args()

    files = collect_files()
    total_kb = sum(len(f["content"]) for f in files) / 1024

    print(f"📦 Файлов к отправке: {len(files)} ({total_kb:.0f} КБ)")
    for f in files:
        print(f"   {f['path']}")

    if args.dry_run:
        print("\nПеременные окружения:")
        for k, v in ENV_VARS.items():
            print(f"   {k} = {'*' * 8 + v[-6:] if k in SECRETS else v}")
        print("\nDry-run: запросы не отправлялись.")
        return

    key = os.environ.get("BH_API_KEY", "").strip()
    if not key:
        sys.exit(
            "Не задан BH_API_KEY.\n"
            "1) Зарегистрируйтесь: https://bot-hosting.net/login (e-mail, без карты)\n"
            "2) Ключ: https://bot-hosting.net/dash/developer → Create API key\n"
            "3) export BH_API_KEY=bhk_...  и запустите снова"
        )

    print("\n1/6 Проверяю ключ и квоту…")
    status, body = api("GET", "/deployments?brief=true", key=key)
    if status != 200:
        sys.exit(f"Ключ не принят ({status}): {body}")
    print(f"   ключ работает, деплоев найдено: {len(body.get('deployments', []))}")

    dep = find_deployment(key)
    if args.logs:
        if not dep:
            sys.exit(f"Деплой {NAME} не найден")
        status, diag = api("GET", f"/deployments/{dep['id']}/diagnose?waitSeconds=20", key=key)
        print(json.dumps(diag, ensure_ascii=False, indent=2)[:4000])
        return

    if dep:
        dep_id = dep["id"]
        print(f"2/6 Деплой уже есть: {dep_id} (state={dep.get('state')}) — обновляю файлы")
    else:
        print("2/6 Создаю деплой (python 3.12, 256 МБ, blank)…")
        status, body = api("POST", "/deployments", {
            "name": NAME,
            "source": "blank",
            "runtime": "python",
            "runtimeVersion": "3.12",
            "ramMB": 256,
            "cpuPct": 25,
            "storageMB": 512,
            "env": "".join(f"{k}={v}\n" for k, v in ENV_VARS.items()),
        }, key=key)
        if status not in (200, 201):
            sys.exit(f"Не удалось создать деплой ({status}): {body}")
        dep = body.get("deployment", body)
        dep_id = dep["id"]
        print(f"   создан: {dep_id}  RAM={dep.get('resources', {}).get('ramMB')} МБ")

    print(f"3/6 Заливаю {len(files)} файлов…")
    status, body = api("POST", f"/deployments/{dep_id}/files/writeMany", {"files": files}, key=key)
    if status not in (200, 201):
        sys.exit(f"Ошибка заливки ({status}): {body}")
    written = body.get("written", [])
    failed = body.get("failed") or []
    print(f"   записано: {len(written)}, ошибок: {len(failed)}")
    for bad in failed:
        print(f"   ! {bad}")

    print("4/6 Переменные окружения…")
    for k, v in ENV_VARS.items():
        status, body = api("POST", f"/deployments/{dep_id}/env",
                           {"key": k, "value": v, "secret": k in SECRETS}, key=key)
        mark = "ok" if status in (200, 201) else f"ошибка {status}: {body}"
        print(f"   {k}: {mark}")

    print("5/6 Настраиваю запуск…")
    status, body = api("PATCH", f"/deployments/{dep_id}/startup", {
        "runtime": "python",
        "runtimeVersion": "3.12",
        "entryFile": "main.py",
        "startCommand": "pip install -q -r requirements.txt && exec python main.py",
    }, key=key)
    print(f"   {status}: {json.dumps(body, ensure_ascii=False)[:300]}")

    print("6/6 Стартую и читаю логи…")
    status, apply = api("POST", f"/deployments/{dep_id}/apply",
                        {"restart": True, "waitSeconds": 45}, key=key)
    print(f"   state={apply.get('state')} installing={apply.get('installing')}")
    for line in (apply.get("logs") or [])[-40:]:
        print("   |", line)
    if apply.get("hint"):
        print("   подсказка:", apply["hint"])

    status, diag = api("GET", f"/deployments/{dep_id}/diagnose?waitSeconds=30", key=key)
    print(f"\nФинальный state: {diag.get('state')}")
    for line in (diag.get("logs") or [])[-25:]:
        print("   |", line)

    ok = "Run polling for bot" in "\n".join((diag.get("logs") or []) + (apply.get("logs") or []))
    print("\n" + ("✅ Бот подключился к Telegram — деплой успешен." if ok
                  else "⚠️ Подключение не подтверждено, смотрите логи выше."))
    print(f"Панель: https://bot-hosting.net/dash (деплой {NAME}, id {dep_id})")


if __name__ == "__main__":
    main()
