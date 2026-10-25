"""Корзина, оформление заявки, мои заявки и повтор заказа."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import keyboards as kb
from .. import repo, texts
from ..config import Config
from ..db import Database
from ..middlewares import ProfileMiddleware
from ..states import Shop
from ..utils import safe_edit

log = logging.getLogger(__name__)
router = Router(name="cart")
router.message.middleware(ProfileMiddleware())
router.callback_query.middleware(ProfileMiddleware())

# Одна заявка за раз на пользователя: защита от двойного нажатия
# «Подтвердить», когда два одинаковых события обрабатываются параллельно.
_checkout_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


def _cart_from_state(data: dict) -> dict[int, int]:
    return {int(k): int(v) for k, v in (data.get("cart") or {}).items() if int(v) > 0}


async def render_cart(db: Database, tg_id: int, cart: dict[int, int]) -> tuple[str, bool]:
    if not cart:
        return "Корзина пуста. Добавьте товары из каталога.", False
    async with db.client(tg_id) as conn:
        products = await repo.get_products(conn, list(cart))
    if not products:
        return "Товары из корзины больше недоступны. Добавьте товары заново.", False
    lines, total = ["<b>Корзина</b>"], 0
    for p in products:
        qty = cart[p["id"]]
        total += p["price"] * qty
        lines.append(f"• {texts.h(p['name'])} ({texts.h(p['sku'])}): {qty} шт. x "
                     f"{texts.money(p['price'])} = {texts.money(p['price'] * qty)}")
    lines.append(f"\nИтого: <b>{texts.money(total)}</b>")
    return "\n".join(lines), True


@router.message(F.text == kb.BTN_CART)
async def show_cart(message: Message, state: FSMContext, db: Database) -> None:
    text, has_items = await render_cart(db, message.from_user.id, _cart_from_state(await state.get_data()))
    await message.answer(text, reply_markup=kb.cart(has_items))


@router.callback_query(kb.CartCb.filter(F.action == "clear"))
async def clear_cart(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(cart={})
    await safe_edit(callback.message, "Корзина очищена.")
    await callback.answer()


@router.callback_query(kb.CartCb.filter(F.action == "checkout"))
async def checkout(callback: CallbackQuery, state: FSMContext) -> None:
    if not _cart_from_state(await state.get_data()):
        await callback.answer("Корзина пуста", show_alert=True)
        return
    await state.set_state(Shop.comment)
    await callback.message.answer("Добавьте комментарий к заявке (до 500 символов) "
                                  "или нажмите кнопку:", reply_markup=kb.no_comment())
    await callback.answer()


async def ask_confirm(message: Message, state: FSMContext, db: Database, tg_id: int) -> None:
    data = await state.get_data()
    text, has_items = await render_cart(db, tg_id, _cart_from_state(data))
    if not has_items:
        await state.set_state(None)
        await message.answer(text)
        return
    comment = data.get("comment")
    if comment:
        text += f"\nКомментарий: {texts.h(comment)}"
    await message.answer(text + "\n\nОтправить заявку?", reply_markup=kb.confirm())


@router.message(Shop.comment, F.text)
async def got_comment(message: Message, state: FSMContext, db: Database) -> None:
    comment = message.text.strip()
    if len(comment) > 500:
        await message.answer("Комментарий длиннее 500 символов, сократите его:")
        return
    await state.update_data(comment=comment)
    await ask_confirm(message, state, db, message.from_user.id)


@router.callback_query(Shop.comment, kb.CartCb.filter(F.action == "nocomment"))
async def without_comment(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.update_data(comment=None)
    await ask_confirm(callback.message, state, db, callback.from_user.id)
    await callback.answer()


@router.callback_query(kb.CartCb.filter(F.action == "cancel"))
async def cancel_checkout(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(None)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("Оформление отменено, корзина сохранена.")
    await callback.answer()


@router.callback_query(kb.CartCb.filter(F.action == "confirm"))
async def confirm_order(callback: CallbackQuery, state: FSMContext, db: Database,
                        cfg: Config, bot: Bot, profile) -> None:
    tg_id = callback.from_user.id
    async with _checkout_locks[tg_id]:
        # Сразу убираем кнопки: второе нажатие уже не на что нажимать
        await callback.message.edit_reply_markup(reply_markup=None)
        data = await state.get_data()
        cart = _cart_from_state(data)
        if not cart:
            await callback.answer("Эта заявка уже отправлена", show_alert=True)
            return
        comment = data.get("comment")
        await state.update_data(cart={}, comment=None)
        await state.set_state(None)
        try:
            async with db.client(tg_id) as conn:
                order_id, total = await repo.create_order(conn, cart, comment)
        except repo.EmptyOrderError:
            await callback.message.answer("Товары из корзины больше недоступны. Добавьте товары заново.")
            await callback.answer()
            return
        except Exception:
            await state.update_data(cart=data.get("cart"), comment=comment)  # корзина не теряется
            raise
    await callback.message.answer(
        f"Заявка №{order_id} на сумму {texts.money(total)} отправлена менеджеру. "
        "Статус можно посмотреть в разделе «Мои заявки».")
    await callback.answer()
    if cfg.managers_chat_id:
        try:
            await bot.send_message(
                cfg.managers_chat_id,
                f"Новая заявка №{order_id} от {texts.h(profile['organization'])} "
                f"на сумму {texts.money(total)}",
                reply_markup=kb.open_for_manager(order_id))
        except TelegramAPIError:
            log.exception("не удалось отправить уведомление менеджерам")


@router.message(F.text == kb.BTN_ORDERS)
async def my_orders(message: Message, db: Database) -> None:
    async with db.client(message.from_user.id) as conn:
        rows = await repo.list_my_orders(conn)
    if not rows:
        await message.answer("У вас пока нет заявок.")
        return
    await message.answer("Ваши последние заявки:", reply_markup=kb.my_orders(rows))


@router.callback_query(kb.OrderCb.filter(F.action == "show"))
async def order_details(callback: CallbackQuery, callback_data: kb.OrderCb, db: Database) -> None:
    async with db.client(callback.from_user.id) as conn:
        order = await repo.get_my_order(conn, callback_data.id)
        items = await repo.get_order_items(conn, callback_data.id) if order else []
    if order is None:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    lines = [f"<b>Заявка №{order['id']}</b> от {order['created_at']:%d.%m.%Y}",
             f"Статус: {texts.STATUS_LABELS[order['status']]}"]
    for it in items:
        lines.append(f"• {texts.h(it['name'])}: {it['quantity']} шт. x {texts.money(it['unit_price'])}")
    lines.append(f"Сумма: <b>{texts.money(order['total'])}</b>")
    if order["comment"]:
        lines.append(f"Комментарий: {texts.h(order['comment'])}")
    await callback.message.answer("\n".join(lines), reply_markup=kb.order_actions(order["id"]))
    await callback.answer()


@router.callback_query(kb.OrderCb.filter(F.action == "repeat"))
async def repeat_order(callback: CallbackQuery, callback_data: kb.OrderCb,
                       state: FSMContext, db: Database) -> None:
    async with db.client(callback.from_user.id) as conn:
        order = await repo.get_my_order(conn, callback_data.id)
        items = await repo.get_order_items(conn, callback_data.id) if order else []
    available = {it["product_id"]: it["quantity"] for it in items if it["is_active"]}
    if not available:
        await callback.answer("Товары из этой заявки сейчас недоступны", show_alert=True)
        return
    await state.update_data(cart={str(k): v for k, v in available.items()})
    text, has_items = await render_cart(db, callback.from_user.id, available)
    skipped = len(items) - len(available)
    if skipped:
        text += f"\n\nНе добавлено позиций, снятых с продажи: {skipped}"
    await callback.message.answer(text + "\n\nЦены указаны текущие.", reply_markup=kb.cart(has_items))
    await callback.answer()
