#!/usr/bin/env python3
"""Безопасное обновление Telegram-бота на GitHub Actions.

При первом запуске создаёт репозиторий и Actions Secrets. Для последующих запусков
клонирует текущую ветку и добавляет только файлы проекта — существующая база данных
и история коммитов сохраняются. Force-push не используется.

Переменные: GH_TOKEN, BOT_TOKEN, OWNER_IDS. Их можно положить в локальный .env;
файл .env никогда не включается в загрузку.
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

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

API = "https://api.github.com"
ROOT = Path(__file__).resolve().parent
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
OWNER_IDS = os.environ.get("OWNER_IDS", "").strip()
INCLUDE_SUFFIXES = {".py", ".yml", ".yaml", ".md", ".txt", ".sh", ".html", ".css", ".js", ".svg"}
INCLUDE_NAMES = {"Dockerfile", "Procfile", ".gitignore"}
SKIP_DIRS = {"__pycache__", "data", ".git", ".venv", "venv", "uploads", "node_modules"}
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
            "User-Agent": "sektatitles-safe-deploy/2.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw[:500]}


def collect() -> list[Path]:
    files: list[Path] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if path.name in SKIP_FILES or path.name.startswith(".") and path.name not in INCLUDE_NAMES:
            continue
        if path.suffix not in INCLUDE_SUFFIXES and path.name not in INCLUDE_NAMES:
            continue
        files.append(rel)
    return files


def run(cmd: list[str], cwd: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=cwd, env={**os.environ, **(env or {})},
        capture_output=True, text=True, timeout=180,
    )


def set_secret(owner: str, repo: str, name: str, value: str, token: str) -> str:
    """Шифрует Actions Secret публичным ключом репозитория; значение не выводится."""
    from nacl import encoding, public

    status, key = api("GET", f"/repos/{owner}/{repo}/actions/secrets/public-key", token=token)
    if status != 200:
        return f"ошибка получения ключа ({status})"
    pk = public.PublicKey(key["key"].encode(), encoding.Base64Encoder())
    sealed = public.SealedBox(pk).encrypt(value.encode())
    status, body = api(
        "PUT", f"/repos/{owner}/{repo}/actions/secrets/{name}",
        {"encrypted_value": base64.b64encode(sealed).decode(), "key_id": key["key_id"]},
        token=token,
    )
    return "ok" if status in (201, 204) else f"ошибка {status}: {body}"


def _safe_error(text: str, token: str) -> str:
    return text.replace(token, "***") if token else text


def main() -> None:
    parser = argparse.ArgumentParser(description="Обновить код бота, не перезаписывая базу данных на GitHub.")
    parser.add_argument("--repo", default="sektatitles-bot")
    parser.add_argument("--private", action="store_true", help="создать новый приватный репозиторий")
    parser.add_argument("--update-secrets", action="store_true", help="обновить BOT_TOKEN и OWNER_IDS в Secrets")
    parser.add_argument("--dry-run", action="store_true", help="показать файлы, не обращаться к GitHub")
    args = parser.parse_args()

    files = collect()
    print(f"Файлов проекта: {len(files)}")
    for file in files:
        print("  ", file)
    print(f"\nРепозиторий: {args.repo}")
    print("Файл .env и папка data/ исключены; принудительная перезапись истории отключена.")
    if args.dry_run:
        print("Dry-run завершён — ничего не отправлено.")
        return

    token = os.environ.get("GH_TOKEN", "").strip()
    if not token:
        sys.exit("Не задан GH_TOKEN. Создайте GitHub token с repo + workflow и задайте GH_TOKEN.")
    status, user = api("GET", "/user", token=token)
    if status != 200:
        sys.exit(f"GitHub token не принят ({status}).")
    login = user["login"]

    status, repo_info = api("GET", f"/repos/{login}/{args.repo}", token=token)
    is_new = status == 404
    if status not in (200, 404):
        sys.exit(f"Не удалось проверить репозиторий ({status}): {repo_info}")
    if is_new and (not BOT_TOKEN or not OWNER_IDS):
        sys.exit("Перед созданием репозитория задайте BOT_TOKEN и OWNER_IDS в локальном .env.")
    if is_new:
        status, repo_info = api("POST", "/user/repos", {
            "name": args.repo,
            "private": bool(args.private),
            "description": "Telegram-бот титулов, квестов, сезонов и коллекций",
            "auto_init": False,
            "has_issues": False,
            "has_wiki": False,
            "has_projects": False,
        }, token=token)
        if status != 201:
            sys.exit(f"Не удалось создать репозиторий ({status}): {repo_info}")
        print(f"Создан репозиторий https://github.com/{login}/{args.repo}")
    else:
        print(f"Обновляю существующий репозиторий https://github.com/{login}/{args.repo}")

    if args.update_secrets and (not BOT_TOKEN or not OWNER_IDS):
        sys.exit("Для --update-secrets нужны BOT_TOKEN и OWNER_IDS в локальном окружении.")

    build = Path(tempfile.mkdtemp(prefix="titlebot-update-"))
    try:
        remote = f"https://x-access-token:{token}@github.com/{login}/{args.repo}.git"
        branch_status, _ = api("GET", f"/repos/{login}/{args.repo}/branches/main", token=token)
        has_main = branch_status == 200
        if has_main:
            res = run(["git", "clone", "--depth", "1", "--branch", "main", remote, str(build)], ROOT)
            if res.returncode != 0:
                sys.exit("Не удалось клонировать ветку main: " + _safe_error(res.stderr, token))
        else:
            res = run(["git", "init", "-q", "-b", "main"], build)
            if res.returncode != 0:
                sys.exit("Не удалось инициализировать Git: " + res.stderr)
            run(["git", "remote", "add", "origin", remote], build)

        # Перекладываем только известные файлы поверх клона. data/ в клоне остаётся нетронутой.
        for rel in files:
            dest = build / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / rel, dest)

        for step in (
            ["git", "config", "user.name", "titlebot"],
            ["git", "config", "user.email", "titlebot@users.noreply.github.com"],
            ["git", "add", "-A"],
        ):
            res = run(step, build)
            if res.returncode != 0:
                sys.exit("Ошибка подготовки git-изменений: " + res.stderr)

        changed = run(["git", "diff", "--cached", "--quiet"], build).returncode != 0
        if changed:
            res = run(["git", "commit", "-q", "-m", "TitleBot: крупное обновление — игры, квесты, сезоны"], build)
            if res.returncode != 0:
                sys.exit("Не удалось создать коммит: " + res.stderr)
            if has_main:
                # Переносим коммит поверх свежего remote main, если Actions успел сохранить БД параллельно.
                for attempt in range(3):
                    res = run(["git", "fetch", "origin", "main"], build)
                    if res.returncode == 0:
                        rebase = run(["git", "rebase", "origin/main"], build)
                        if rebase.returncode != 0:
                            run(["git", "rebase", "--abort"], build)
                            sys.exit("Не удалось безопасно объединить ветку main: " + rebase.stderr)
                    res = run(["git", "push", "origin", "main"], build)
                    if res.returncode == 0:
                        break
                else:
                    sys.exit("Push отклонён после 3 попыток. Ничего не перезаписывал; повторите позже.")
            else:
                res = run(["git", "push", "-u", "origin", "main"], build)
                if res.returncode != 0:
                    sys.exit("Push отклонён: " + _safe_error(res.stderr, token))
            print("Код запушен; удалённая база данных и история сохранены.")
        else:
            print("Код уже актуален — новый коммит не нужен.")

        should_set_secrets = is_new or args.update_secrets
        if should_set_secrets:
            try:
                import nacl  # noqa: F401
            except ImportError:
                sys.exit("Для шифрования Actions Secrets выполните: pip install pynacl")

            for name, value in (("BOT_TOKEN", BOT_TOKEN), ("OWNER_IDS", OWNER_IDS)):
                result = set_secret(login, args.repo, name, value, token)
                print(f"Actions Secret {name}: {result}")
        else:
            print("Существующие Actions Secrets не изменялись.")

        # На новом репозитории создать Pages нужно один раз в Settings → Pages → GitHub Actions.
        status, body = api(
            "POST", f"/repos/{login}/{args.repo}/actions/workflows/titlebot.yml/dispatches",
            {"ref": "main"}, token=token,
        )
        if status not in (204, 201):
            print(f"Не удалось запустить workflow ({status}): {body}")
        else:
            print("GitHub Actions запущен.")
    finally:
        shutil.rmtree(build, ignore_errors=True)

    print(f"\nКод: https://github.com/{login}/{args.repo} · Actions: https://github.com/{login}/{args.repo}/actions")
    if is_new:
        print("Для GitHub Pages один раз выберите Settings → Pages → Source: GitHub Actions.")


if __name__ == "__main__":
    main()
