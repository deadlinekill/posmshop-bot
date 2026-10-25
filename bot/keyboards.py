"""Клавиатуры и данные inline-кнопок."""
from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import (InlineKeyboardMarkup, KeyboardButton,
                           ReplyKeyboardMarkup, ReplyKeyboardRemove)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .texts import STATUS_LABELS, money

BTN_CATALOG = "Каталог"
BTN_SEARCH = "Поиск"
BTN_CART = "Корзина"
BTN_ORDERS = "Мои заявки"
BTN_HELP = "Помощь"
BTN_MANAGER = "Заявки клиентов"

PAGE_SIZE = 8
QTY_PRESETS = (10, 50, 100, 500)


class CatCb(CallbackData, prefix="cat"):
    id: int          # 0 = корень каталога
    page: int = 0


class ProdCb(CallbackData, prefix="prod"):
    id: int


class QtyCb(CallbackData, prefix="qty"):
    id: int
    qty: int         # 0 = ввести количество вручную


class CartCb(CallbackData, prefix="cart"):
    action: str      # checkout / clear / nocomment / confirm / cancel


class OrderCb(CallbackData, prefix="ord"):
    id: int
    action: str      # show / repeat


class MgrCb(CallbackData, prefix="mgr"):
    id: int
    action: str      # show / set
    new: str = ""
    old: str = ""


def main_menu(is_manager: bool = False) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=BTN_CATALOG), KeyboardButton(text=BTN_SEARCH)],
            [KeyboardButton(text=BTN_CART), KeyboardButton(text=BTN_ORDERS)],
            [KeyboardButton(text=BTN_HELP)]]
    if is_manager:
        rows.append([KeyboardButton(text=BTN_MANAGER)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def consent() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Согласен", callback_data="consent:yes")
    kb.button(text="Не согласен", callback_data="consent:no")
    return kb.as_markup()


def share_phone() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Отправить мой номер", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True)


def remove() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


def categories(rows, parent_id: int | None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for c in rows:
        kb.button(text=c["name"], callback_data=CatCb(id=c["id"]))
    if parent_id is not None:
        kb.button(text="Назад", callback_data=CatCb(id=parent_id))
    kb.adjust(1)
    return kb.as_markup()


def products(rows, category_id: int, parent_id: int | None, page: int, total: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for p in rows:
        kb.button(text=f"{p['name']} - {money(p['price'])}", callback_data=ProdCb(id=p["id"]))
    nav = 0
    if page > 0:
        kb.button(text="< Назад", callback_data=CatCb(id=category_id, page=page - 1)); nav += 1
    if (page + 1) * PAGE_SIZE < total:
        kb.button(text="Вперёд >", callback_data=CatCb(id=category_id, page=page + 1)); nav += 1
    kb.button(text="К категориям", callback_data=CatCb(id=parent_id or 0))
    kb.adjust(*([1] * len(rows)), *([nav] if nav else []), 1)
    return kb.as_markup()


def search_results(rows) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for p in rows:
        kb.button(text=f"{p['sku']} {p['name']}", callback_data=ProdCb(id=p["id"]))
    kb.adjust(1)
    return kb.as_markup()


def quantity(product_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for q in QTY_PRESETS:
        kb.button(text=f"{q} шт.", callback_data=QtyCb(id=product_id, qty=q))
    kb.button(text="Другое количество", callback_data=QtyCb(id=product_id, qty=0))
    kb.adjust(4, 1)
    return kb.as_markup()


def cart(has_items: bool) -> InlineKeyboardMarkup | None:
    if not has_items:
        return None
    kb = InlineKeyboardBuilder()
    kb.button(text="Оформить заявку", callback_data=CartCb(action="checkout"))
    kb.button(text="Очистить корзину", callback_data=CartCb(action="clear"))
    kb.adjust(1)
    return kb.as_markup()


def no_comment() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Без комментария", callback_data=CartCb(action="nocomment"))
    return kb.as_markup()


def confirm() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Подтвердить заявку", callback_data=CartCb(action="confirm"))
    kb.button(text="Отменить", callback_data=CartCb(action="cancel"))
    kb.adjust(1)
    return kb.as_markup()


def my_orders(rows) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for o in rows:
        kb.button(text=f"№{o['id']} от {o['created_at']:%d.%m.%Y}, {STATUS_LABELS[o['status']]}",
                  callback_data=OrderCb(id=o["id"], action="show"))
    kb.adjust(1)
    return kb.as_markup()


def order_actions(order_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Повторить заказ", callback_data=OrderCb(id=order_id, action="repeat"))
    return kb.as_markup()


def manager_orders(rows) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for o in rows:
        kb.button(text=f"№{o['id']} {STATUS_LABELS[o['status']]}: {o['organization']}",
                  callback_data=MgrCb(id=o["id"], action="show"))
    kb.adjust(1)
    return kb.as_markup()


def manager_order(order_id: int, status: str, next_statuses: list[str]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for s in next_statuses:
        kb.button(text=f"-> {STATUS_LABELS[s]}",
                  callback_data=MgrCb(id=order_id, action="set", new=s, old=status))
    kb.adjust(1)
    return kb.as_markup()


def open_for_manager(order_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Открыть заявку", callback_data=MgrCb(id=order_id, action="show"))
    return kb.as_markup()
