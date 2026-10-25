"""Сквозная проверка бота: обновления Telegram подаются в диспетчер напрямую,
а отправка сообщений перехватывается заглушкой вместо настоящего API.
База данных при этом настоящая (PostgreSQL)."""
from __future__ import annotations

import asyncio
import itertools
import random
from datetime import datetime, timezone
from decimal import Decimal

import pytest_asyncio
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Contact, Message, Update, User

from bot.app import create_dispatcher
from bot.config import Config
from bot.db import Database
from conftest import PHONE_KEY

MANAGERS_CHAT = -1001234567890
_DP = None
_ids = itertools.count(random.randint(100_000_000, 900_000_000))
_upd = itertools.count(1)


class FakeSession(BaseSession):
    """Вместо HTTP-запросов к Telegram запоминает, что бот хотел отправить."""

    def __init__(self) -> None:
        super().__init__()
        self.sent: list = []

    async def close(self) -> None:
        pass

    async def make_request(self, bot, method, timeout=None):
        self.sent.append(method)
        if isinstance(method, (SendMessage, EditMessageText)):
            chat_id = getattr(method, "chat_id", 0) or 0
            return Message(message_id=len(self.sent), date=datetime.now(timezone.utc),
                           chat=Chat(id=chat_id, type="private"), text=method.text)
        return True

    async def stream_content(self, *args, **kwargs):
        yield b""

    def texts_to(self, chat_id: int) -> list[str]:
        return [m.text for m in self.sent
                if isinstance(m, (SendMessage, EditMessageText)) and m.chat_id == chat_id]

    def last_markup(self, chat_id: int):
        for m in reversed(self.sent):
            if (isinstance(m, (SendMessage, EditMessageText)) and m.chat_id == chat_id
                    and m.reply_markup is not None and hasattr(m.reply_markup, "inline_keyboard")):
                return m.reply_markup
        return None


class Tg:
    """Пользователь Telegram, который пишет боту."""

    def __init__(self, env, tg_id: int, name: str = "Тест") -> None:
        self.env, self.id = env, tg_id
        self.user = User(id=tg_id, is_bot=False, first_name=name)
        self.chat = Chat(id=tg_id, type="private")

    def _msg(self, **kw) -> Message:
        return Message(message_id=next(_upd), date=datetime.now(timezone.utc),
                       chat=self.chat, from_user=self.user, **kw)

    async def say(self, text: str):
        await self.env.feed(Update(update_id=next(_upd), message=self._msg(text=text)))

    async def share_contact(self, phone: str, user_id: int | None = None):
        contact = Contact(phone_number=phone, first_name="Тест", user_id=user_id or self.id)
        await self.env.feed(Update(update_id=next(_upd), message=self._msg(contact=contact)))

    async def press(self, data: str):
        cb = CallbackQuery(id=str(next(_upd)), from_user=self.user, chat_instance="ci",
                           data=data, message=self._msg(text="кнопки"))
        await self.env.feed(Update(update_id=next(_upd), callback_query=cb))

    def texts(self) -> list[str]:
        return self.env.session.texts_to(self.id)

    def last(self) -> str:
        return self.texts()[-1]

    def buttons(self) -> dict[str, str]:
        markup = self.env.session.last_markup(self.id)
        return {b.text: b.callback_data for row in markup.inline_keyboard for b in row}


class Env:
    def __init__(self, bot, dp, session) -> None:
        self.bot, self.dp, self.session = bot, dp, session

    async def feed(self, update: Update) -> None:
        await self.dp.feed_update(self.bot, update)


@pytest_asyncio.fixture
async def env(pg):
    cfg = Config(bot_token="123456:TEST-token-for-tests", db_host=pg["host"], db_port=pg["port"],
                 db_name=pg["db"], db_sslmode="prefer",
                 bot_db_password=pg["passwords"]["bot_app"],
                 manager_db_password=pg["passwords"]["manager_app"],
                 phone_key=PHONE_KEY, managers_chat_id=MANAGERS_CHAT)
    db = await Database.connect(cfg)
    session = FakeSession()
    bot = Bot(cfg.bot_token, session=session, default=DefaultBotProperties(parse_mode="HTML"))
    global _DP
    if _DP is None:  # роутеры модульные, диспетчер создаётся один раз, как в боте
        _DP = create_dispatcher(db, cfg)
    _DP["db"], _DP["cfg"] = db, cfg  # у каждого теста свой пул соединений
    yield Env(bot, _DP, session)
    await db.close()


async def register_via_bot(user: Tg, org="ООО Ромашка", name="Анна Иванова", phone="8 (999) 111-22-33"):
    await user.say("/start")
    assert "согласны на обработку персональных данных" in user.last()
    await user.press("consent:yes")
    await user.say(org)
    await user.say(name)
    await user.say(phone)
    assert "Регистрация завершена" in user.last()


async def test_full_client_and_manager_flow(env, connect):
    client = Tg(env, next(_ids), "Анна")
    await register_via_bot(client)

    admin = await connect("postgres")
    phone = await admin.fetchval(
        "SELECT ext.pgp_sym_decrypt(phone_enc, $2) FROM shop.users WHERE tg_id = $1", client.id, PHONE_KEY)
    assert phone == "+79991112233"

    # каталог: раздел -> подраздел -> товар -> количество
    await client.say("Каталог")
    await client.press(client.buttons()["Ценникодержатели"])
    await client.press(client.buttons()["Полочные"])
    product_btn = next(v for k, v in client.buttons().items() if k.startswith("Ценникодержатель полочный DBR 39"))
    await client.press(product_btn)
    assert "PC-10" in client.last()
    await client.press(client.buttons()["50 шт."])

    # поиск по артикулу и ручной ввод количества
    await client.say("Поиск")
    await client.say("D-3")
    await client.press(client.buttons()["D-30 Разделитель полочный 300 мм"])
    await client.press(client.buttons()["Другое количество"])
    await client.say("25")

    await client.say("Корзина")
    assert "7 250,00 ₽" in client.last()  # 50 x 85 + 25 x 120
    await client.press(client.buttons()["Оформить заявку"])
    await client.say("Позвоните до обеда")
    assert "Отправить заявку?" in client.last()
    confirm = client.buttons()["Подтвердить заявку"]

    # двойное нажатие «Подтвердить»: заявка должна создаться одна
    await asyncio.gather(client.press(confirm), client.press(confirm))
    orders = await admin.fetch(
        "SELECT o.id, o.total, o.comment, count(oi.id) AS items FROM shop.orders o "
        "JOIN shop.users u ON u.id = o.user_id JOIN shop.order_items oi ON oi.order_id = o.id "
        "WHERE u.tg_id = $1 GROUP BY o.id", client.id)
    assert len(orders) == 1
    order = orders[0]
    assert order["total"] == Decimal("7250.00") and order["items"] == 2
    assert order["comment"] == "Позвоните до обеда"
    assert any(f"Новая заявка №{order['id']}" in t for t in env.session.texts_to(MANAGERS_CHAT))

    # персональные данные не попали в журнал диалогов
    logged = [r["message"] for r in await admin.fetch(
        "SELECT message FROM shop.dialog_log WHERE tg_id = $1", client.id)]
    assert "Каталог" in logged
    assert not any("111-22-33" in m or "Анна" in m or "Ромашка" in m for m in logged)

    # клиент не может открыть менеджерский раздел
    await client.say("Заявки клиентов")
    assert "только менеджерам" in client.last()

    # менеджер: регистрируется, администратор выдаёт роль в базе
    manager = Tg(env, next(_ids), "Мария")
    await register_via_bot(manager, org="PosmShop", name="Мария", phone="+7 916 000-00-01")
    await admin.execute("UPDATE shop.users SET role = 'manager' WHERE tg_id = $1", manager.id)
    await manager.say("Заявки клиентов")
    await manager.press(next(v for k, v in manager.buttons().items() if k.startswith(f"№{order['id']} ")))
    card = manager.last()
    assert "+79991112233" in card and "ООО Ромашка" in card
    await manager.press(manager.buttons()["-> В работе"])
    assert any("принята в работу" in t for t in client.texts())
    status = await admin.fetchval("SELECT status FROM shop.orders WHERE id = $1", order["id"])
    assert status == "in_work"
    audit = await admin.fetchrow(
        "SELECT db_role, app_tg_id FROM shop.audit_log WHERE table_name = 'orders' "
        "AND operation = 'UPDATE' AND row_id = $1", order["id"])
    assert audit["db_role"] == "manager_app" and audit["app_tg_id"] == manager.id

    # повтор той же кнопки (статус уже изменён) не должен ничего ломать
    await manager.press(f"mgr:{order['id']}:set:in_work:new")
    assert await admin.fetchval("SELECT status FROM shop.orders WHERE id = $1", order["id"]) == "in_work"

    # мои заявки -> повтор заказа заполняет корзину
    await client.say("Мои заявки")
    await client.press(next(iter(client.buttons().values())))
    assert "В работе" in client.last()
    await client.press(client.buttons()["Повторить заказ"])
    assert "7 250,00 ₽" in client.last()


async def test_registration_rejects_foreign_contact_and_bad_phone(env, connect):
    user = Tg(env, next(_ids))
    await user.say("/start")
    await user.press("consent:yes")
    await user.say("ООО Тест")
    await user.say("Пётр")
    await user.share_contact("+79990000000", user_id=user.id + 1)
    assert "свой номер" in user.last()
    await user.say("позвоните мне")
    assert "Не похоже на номер телефона" in user.last()
    await user.share_contact("+7 999 000 00 07")
    assert "Регистрация завершена" in user.last()


async def test_consent_refused_and_unregistered_access(env, connect):
    user = Tg(env, next(_ids))
    await user.say("/start")
    await user.press("consent:no")
    assert "невозможна" in user.last()
    await user.say("Каталог")
    assert "зарегистрироваться" in user.last()
    admin = await connect("postgres")
    assert await admin.fetchval("SELECT count(*) FROM shop.users WHERE tg_id = $1", user.id) == 0


async def test_search_injection_through_bot(env, connect):
    user = Tg(env, next(_ids))
    await register_via_bot(user, phone="+79990000011")
    await user.say("Поиск")
    await user.say("'; DROP TABLE shop.orders; --")
    assert "Ничего не найдено" in user.last()
    admin = await connect("postgres")
    assert await admin.fetchval("SELECT to_regclass('shop.orders') IS NOT NULL")
