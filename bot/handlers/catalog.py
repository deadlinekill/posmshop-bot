"""Каталог, поиск и добавление товаров в корзину."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import keyboards as kb
from .. import repo, texts
from ..db import Database
from ..middlewares import ProfileMiddleware
from ..states import Shop
from ..utils import safe_edit

router = Router(name="catalog")
router.message.middleware(ProfileMiddleware())
router.callback_query.middleware(ProfileMiddleware())

MAX_QTY = 100_000


async def add_to_cart(state: FSMContext, product_id: int, qty: int) -> int:
    data = await state.get_data()
    cart: dict[str, int] = dict(data.get("cart") or {})
    key = str(product_id)
    cart[key] = min(cart.get(key, 0) + qty, MAX_QTY)
    await state.update_data(cart=cart)
    return cart[key]


@router.message(F.text == kb.BTN_CATALOG)
async def open_catalog(message: Message, db: Database) -> None:
    async with db.client(message.from_user.id) as conn:
        rows = await repo.list_categories(conn, None)
    await message.answer("Выберите раздел каталога:", reply_markup=kb.categories(rows, None))


@router.callback_query(kb.CatCb.filter())
async def open_category(callback: CallbackQuery, callback_data: kb.CatCb, db: Database) -> None:
    async with db.client(callback.from_user.id) as conn:
        if callback_data.id == 0:
            rows = await repo.list_categories(conn, None)
            await safe_edit(callback.message, "Выберите раздел каталога:",
                            kb.categories(rows, None))
            await callback.answer()
            return
        category = await repo.get_category(conn, callback_data.id)
        if category is None:
            await callback.answer("Раздел не найден", show_alert=True)
            return
        children = await repo.list_categories(conn, category["id"])
        if children:
            await safe_edit(callback.message,
                            f"<b>{texts.h(category['name'])}</b>\nВыберите подраздел:",
                            kb.categories(children, category["parent_id"] or 0))
            await callback.answer()
            return
        page = max(callback_data.page, 0)
        rows, total = await repo.list_products(conn, category["id"], kb.PAGE_SIZE, page * kb.PAGE_SIZE)
    if not rows:
        await callback.answer("В этом разделе пока нет товаров", show_alert=True)
        return
    pages = (total + kb.PAGE_SIZE - 1) // kb.PAGE_SIZE
    await safe_edit(callback.message,
                    f"<b>{texts.h(category['name'])}</b> (стр. {page + 1} из {pages})\nВыберите товар:",
                    kb.products(rows, category["id"], category["parent_id"], page, total))
    await callback.answer()


@router.callback_query(kb.ProdCb.filter())
async def open_product(callback: CallbackQuery, callback_data: kb.ProdCb, db: Database) -> None:
    async with db.client(callback.from_user.id) as conn:
        p = await repo.get_product(conn, callback_data.id)
    if p is None:
        await callback.answer("Товар недоступен", show_alert=True)
        return
    text = (f"<b>{texts.h(p['name'])}</b>\nАртикул: {texts.h(p['sku'])}\n"
            f"Цена: {texts.money(p['price'])} за шт.\n")
    if p["description"]:
        text += f"\n{texts.h(p['description'])}\n"
    text += "\nСколько добавить в корзину?"
    await callback.message.answer(text, reply_markup=kb.quantity(p["id"]))
    await callback.answer()


@router.callback_query(kb.QtyCb.filter())
async def choose_qty(callback: CallbackQuery, callback_data: kb.QtyCb, state: FSMContext) -> None:
    if callback_data.qty == 0:
        await state.update_data(qty_product=callback_data.id)
        await state.set_state(Shop.quantity)
        await callback.message.answer(f"Введите количество (от 1 до {MAX_QTY}):")
        await callback.answer()
        return
    if not 1 <= callback_data.qty <= MAX_QTY:
        await callback.answer("Недопустимое количество", show_alert=True)
        return
    total = await add_to_cart(state, callback_data.id, callback_data.qty)
    await callback.answer(f"Добавлено. В корзине: {total} шт.")


@router.message(Shop.quantity, F.text)
async def typed_qty(message: Message, state: FSMContext) -> None:
    text = message.text.strip().replace(" ", "")
    if not text.isdigit() or not 1 <= int(text) <= MAX_QTY:
        await message.answer(f"Введите целое число от 1 до {MAX_QTY}:")
        return
    data = await state.get_data()
    product_id = data.get("qty_product")
    await state.set_state(None)
    if not product_id:
        await message.answer("Товар не выбран, откройте каталог заново.")
        return
    total = await add_to_cart(state, product_id, int(text))
    await message.answer(f"Добавлено. Этого товара в корзине: {total} шт. Откройте «Корзина», "
                         "чтобы оформить заявку.")


@router.message(F.text == kb.BTN_SEARCH)
async def ask_search(message: Message, state: FSMContext) -> None:
    await state.set_state(Shop.search)
    await message.answer("Введите название или артикул товара (не меньше 3 символов):")


@router.message(Shop.search, F.text)
async def do_search(message: Message, state: FSMContext, db: Database) -> None:
    query = message.text.strip()
    if len(query) < 3:
        await message.answer("Слишком короткий запрос. Введите хотя бы 3 символа:")
        return
    if len(query) > 100:
        query = query[:100]
    async with db.client(message.from_user.id) as conn:
        rows = await repo.search_products(conn, query)
    await state.set_state(None)
    if not rows:
        await message.answer("Ничего не найдено. Попробуйте другой запрос или откройте каталог.")
        return
    await message.answer(f"Найдено: {len(rows)}", reply_markup=kb.search_results(rows))
