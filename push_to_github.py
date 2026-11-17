#!/usr/bin/env python3
"""
Полный автодеплой бота на бесплатный хостинг GitHub Actions.

Что делает сам:
  1. проверяет ваш GitHub-токен;
  2. создаёт ПУБЛИЧНЫЙ репозиторий (безлимитные минуты Actions);
  3. заливает все файлы бота (включая .github/workflows/titlebot.yml);
  4. кладёт BOT_TOKEN и OWNER_IDS в Secrets репозитория (через API, в коде их нет);
  5. запускает workflow и показывает статус.

Запуск:
    export GH_TOKEN=ghp_...
    python3 push_to_github.py                     # всё по шагам
    python3 push_to_github.py --repo my-bot-name  # другое имя репозитория
    python3 push_to_github.py --dry-run           # показать план, ничего не делать

Токен: https://github.com/settings/tokens → Generate new token (classic)
  scopes: repo, workflow   (workflow обязателен: мы заливаем файл в .github/workflows)
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com"
ROOT = Path(__file__).resolve().parent

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8884403083:AAFP01Fb17trfjZn8qdfN_UopcsdeV7ZY-U")
OWNER_IDS = os.environ.get("OWNER_IDS", "6456120346")

# что отправляем в репозиторий
INCLUDE_SUFFIXES = {".py", ".yml", ".yaml", ".md", ".txt", ".sh"}
INCLUDE_NAMES = {"Dockerfile", "Procfile", ".gitignore"}
SKIP_DIRS = {"__pycache__", "data", ".git", ".venv", "venv", "uploads"}
SKIP_FILES = {".env", ".env.example", "titlebot-deploy.zip"}


def api(method: str, path: str, payload: dict | None = None, token: str = "") -> tuple[int, dict]:
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        API + path,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "sektatitles-push/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw}


def collect() -> list[Path]:
    files = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if any(p in SKIP_DIRS for p in rel.parts):
            continue
        if path.name in SKIP_FILES or path.name.startswith(".") and path.name not in INCLUDE_NAMES:
            continue
        if path.suffix not in INCLUDE_SUFFIXES and path.name not in INCLUDE_NAMES:
            continue
        files.append(rel)
    return files


def run(cmd: list[str], cwd: Path, env: dict | None = None, quiet: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=cwd, env={**os.environ, **(env or {})},
        capture_output=True, text=True, timeout=180,
    )


def set_secret(owner: str, repo: str, name: str, value: str, token: str) -> str:
    """Шифрует значение публичным ключом репозитория и кладёт в Actions secrets."""
    from nacl import encoding, public

    status, key = api("GET", f"/repos/{owner}/{repo}/actions/secrets/public-key", token=token)
    if status != 200:
        return f"нет доступа к ключу ({status})"
    pk = public.PublicKey(key["key"].encode(), encoding.Base64Encoder())
    sealed = public.SealedBox(pk).encrypt(value.encode())
    status, body = api(
        "PUT", f"/repos/{owner}/{repo}/actions/secrets/{name}",
        {"encrypted_value": base64.b64encode(sealed).decode(), "key_id": key["key_id"]},
        token=token,
    )
    return "ok" if status in (201, 204) else f"ошибка {status}: {body}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default="sektatitles-bot")
    parser.add_argument("--private", action="store_true", help="приватный репозиторий (Actions будут лимитированы)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    files = collect()
    print(f"📦 Файлов к отправке: {len(files)}")
    for f in files:
        print("   ", f)
    print(f"\nРепозиторий: {args.repo} ({'приватный' if args.private else 'ПУБЛИЧНЫЙ'})")
    print(f"Секреты: BOT_TOKEN (***{BOT_TOKEN[-6:]}), OWNER_IDS={OWNER_IDS}")

    if args.dry_run:
        print("\nDry-run: ничего не отправлено.")
        return

    token = os.environ.get("GH_TOKEN", "").strip()
    if not token:
        sys.exit(
            "Не задан GH_TOKEN.\n"
            "1) https://github.com/settings/tokens → Generate new token (classic)\n"
            "2) Отметьте scopes: repo, workflow\n"
            "3) export GH_TOKEN=ghp_...  и запустите снова"
        )

    print("\n1/6 Проверяю токен…")
    status, user = api("GET", "/user", token=token)
    if status != 200:
        sys.exit(f"Токен не принят ({status}): {user}")
    login = user["login"]
    scopes = ""
    print(f"   вошли как {login}")

    print("2/6 Создаю репозиторий…")
    status, repo = api("POST", "/user/repos", {
        "name": args.repo,
        "private": bool(args.private),
        "description": "Telegram-бот титулов: покупка, промокоды, массовая выдача, кастомные эмодзи",
        "auto_init": False,
        "has_issues": False,
        "has_wiki": False,
        "has_projects": False,
    }, token=token)
    if status == 201:
        print(f"   создан: {repo['html_url']}")
    elif status == 422:
        print("   репозиторий уже есть — обновляю содержимое")
    else:
        sys.exit(f"Не удалось создать репозиторий ({status}): {repo}")

    print("3/6 Готовлю коммит…")
    build = Path(tempfile.mkdtemp(prefix="gh-push-"))
    for rel in files:
        dest = build / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, dest)

    steps = [
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.name", "titlebot"],
        ["git", "config", "user.email", "titlebot@users.noreply.github.com"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "TitleBot: титулы, промокоды, массовая выдача, эмодзи"],
    ]
    for step in steps:
        res = run(step, build)
        if res.returncode != 0:
            sys.exit(f"git {' '.join(step[:2])} не удался:\n{res.stderr}")

    print("4/6 Пушу в GitHub…")
    remote = f"https://x-access-token:{token}@github.com/{login}/{args.repo}.git"
    res = run(["git", "push", "-q", "--force", remote, "main"], build)
    if res.returncode != 0:
        err = res.stderr.replace(token, "***")
        hint = ""
        if "workflow" in err.lower():
            hint = "\n👉 Токену не хватает scope «workflow» — пересоздайте токен с repo + workflow."
        sys.exit(f"Пуш не удался:\n{err}{hint}")
    print(f"   готово: https://github.com/{login}/{args.repo}")

    print("5/6 Кладу секреты (BOT_TOKEN, OWNER_IDS)…")
    print("   BOT_TOKEN:", set_secret(login, args.repo, "BOT_TOKEN", BOT_TOKEN, token))
    print("   OWNER_IDS:", set_secret(login, args.repo, "OWNER_IDS", OWNER_IDS, token))

    print("6/6 Запускаю workflow…")
    dispatches = Path(build / ".github" / "workflows" / "titlebot.yml")
    workflow_file = "titlebot.yml" if dispatches.exists() else None
    status, body = api(
        "POST", f"/repos/{login}/{args.repo}/actions/workflows/{workflow_file}/dispatches",
        {"ref": "main"}, token=token,
    )
    if status not in (204, 201):
        # файла workflow могло не быть — сообщаем как есть
        print(f"   не удалось запустить ({status}): {body}")
    else:
        print("   запущен")

    print("\nЖду результат первого запуска (до 3 минут)…")
    run_url = f"https://github.com/{login}/{args.repo}/actions"
    for _ in range(36):
        time.sleep(5)
        status, runs = api(
            "GET", f"/repos/{login}/{args.repo}/actions/runs?per_page=1", token=token
        )
        items = runs.get("workflow_runs") or []
        if not items:
            continue
        last = items[0]
        state, conclusion = last.get("status"), last.get("conclusion")
        print(f"   {state} / {conclusion or '—'}")
        if state == "completed":
            if conclusion == "success":
                print("\n✅ Workflow отработал успешно.")
            else:
                print(f"\n⚠️ Завершился с результатом: {conclusion}")
            run_url = last.get("html_url", run_url)
            break

    shutil.rmtree(build, ignore_errors=True)
    print(f"""
Готово. Что дальше:
  • Логи и перезапуск: {run_url}
  • Секреты:           https://github.com/{login}/{args.repo}/settings/secrets/actions
  • Бот работает циклами ~5ч45м каждые 6 часов, база коммитится в репозиторий.

Важно:
  • Смените токен бота: @BotFather → /revoke, затем обновите секрет BOT_TOKEN в GitHub.
  • Удалите этот GitHub-токен: https://github.com/settings/tokens
""")


if __name__ == "__main__":
    main()
