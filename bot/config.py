"""Настройки бота из переменных окружения (файл .env).

Пароли ролей и ключ шифрования телефонов хранятся только в .env,
в коде и в репозитории их нет.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    bot_token: str
    db_host: str
    db_port: int
    db_name: str
    db_sslmode: str           # disable / prefer / require / verify-full
    bot_db_password: str      # роль bot_app
    manager_db_password: str  # роль manager_app
    phone_key: str            # ключ pgcrypto для шифрования телефонов
    managers_chat_id: int | None  # закрытый чат менеджеров для уведомлений


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"В .env не задана переменная {name}")
    return value


def load_config(env_file: Path | None = None) -> Config:
    load_dotenv(env_file or ROOT / ".env")
    chat = os.getenv("MANAGERS_CHAT_ID", "").strip()
    phone_key = _required("PHONE_KEY")
    if len(phone_key) < 16:
        raise RuntimeError("PHONE_KEY должен быть не короче 16 символов")
    return Config(
        bot_token=_required("BOT_TOKEN"),
        db_host=os.getenv("DB_HOST", "127.0.0.1"),
        db_port=int(os.getenv("DB_PORT", "5432")),
        db_name=os.getenv("DB_NAME", "posmshop"),
        db_sslmode=os.getenv("DB_SSLMODE", "prefer"),
        bot_db_password=_required("BOT_DB_PASSWORD"),
        manager_db_password=_required("MANAGER_DB_PASSWORD"),
        phone_key=phone_key,
        managers_chat_id=int(chat) if chat else None,
    )
