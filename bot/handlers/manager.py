"""Менеджерская часть: список заявок и смена статуса.

Работает через отдельный пул под ролью manager_app. У этой роли есть право
менять в заявке только статус, ответственного и время изменения.
Каждое изменение записывается триггером в журнал аудита.
"""
from __future__ import annotations

import logging

from aiogram import Bot, Router, F
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from .. import keyboards as kb
from .. import repo, texts
from ..config import Config
from ..db import Database
from ..middlewares import ManagerOnlyMiddleware, ProfileMiddleware

log = logging.getLogger(__name__)
router = Router(name="manager")
for observer in (router.message, router.callback_query):
    observer.middleware(ProfileMiddleware())
    observer.middleware(ManagerOnlyMiddleware())


@router.message(Command("orders"))
@router.message(F.text == kb.BTN_MANAGER)
async def active_orders(message: Message, db: Database) -> None:
    async with db.manager(message.from_user.id) as conn:
        rows = await repo.list_active_orders(conn)
    if not rows:
        await message.answer("Активных заявок нет.")
        return
    await message.answer(f"Активные заявки ({len(rows)}):", reply_markup=kb.manager_orders(rows))


def order_card(order, items) -> str:
    lines = [f"<b>Заявка №{order['id']}</b> от {order['created_at']:%d.%m.%Y %H:%M}",
             f"Статус: {texts.STATUS_LABELS[order['status']]}",
             f"Клиент: {texts.h(order['organization'])}, {texts.h(order['contact_name'])}",
             f"Телефон: {texts.h(order['phone'])}"]
    if order["manager_name"]:
        lines.append(f"Ответственный: {texts.h(order['manager_name'])}")
    lines.append("")
    for it in items:
        lines.append(f"• {texts.h(it['sku'])} {texts.h(it['name'])}: {it['quantity']} шт. x "
                     f"{texts.money(it['unit_price'])}")
    lines.append(f"Сумма: <b>{texts.money(order['total'])}</b>")
    if order["comment"]:
        lines.append(f"Комментарий клиента: {texts.h(order['comment'])}")
    return "\n".join(lines)


@router.callback_query(kb.MgrCb.filter(F.action == "show"))
async def show_order(callback: CallbackQuery, callback_data: kb.MgrCb, db: Database, cfg: Config) -> None:
    async with db.manager(callback.from_user.id) as conn:
        order = await repo.get_order_for_manager(conn, callback_data.id, cfg.phone_key)
        items = await repo.get_order_items(conn, callback_data.id) if order else []
    if order is None:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    # Карточка с телефоном отправляется менеджеру лично, а не в общий чат
    await bot_send_private(callback, order_card(order, items),
                           kb.manager_order(order["id"], order["status"], texts.NEXT_STATUSES[order["status"]]))


async def bot_send_private(callback: CallbackQuery, text: str, markup) -> None:
    try:
        await callback.bot.send_message(callback.from_user.id, text, reply_markup=markup)
        await callback.answer()
    except TelegramAPIError:
        await callback.answer("Напишите боту в личные сообщения /start, чтобы получать карточки заявок",
                              show_alert=True)


@router.callback_query(kb.MgrCb.filter(F.action == "set"))
async def change_status(callback: CallbackQuery, callback_data: kb.MgrCb, db: Database, bot: Bot) -> None:
    old, new = callback_data.old, callback_data.new
    if new not in texts.NEXT_STATUSES.get(old, []):
        await callback.answer("Такой переход статуса недопустим", show_alert=True)
        return
    async with db.manager(callback.from_user.id) as conn:
        client_tg_id = await repo.set_order_status(conn, callback_data.id, old, new, callback.from_user.id)
    if client_tg_id is None:
        await callback.answer("Статус заявки уже изменён другим менеджером. Откройте её заново.",
                              show_alert=True)
        return
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(f"Заявка №{callback_data.id}: статус «{texts.STATUS_LABELS[new]}».",
                                  reply_markup=kb.manager_order(callback_data.id, new, texts.NEXT_STATUSES[new]))
    await callback.answer("Статус изменён")
    try:
        await bot.send_message(client_tg_id, texts.CLIENT_NOTIFY[new].format(id=callback_data.id))
    except TelegramAPIError:
        # например, клиент заблокировал бота; статус при этом уже сохранён
        log.warning("не удалось уведомить клиента %s о заявке %s", client_tg_id, callback_data.id)
