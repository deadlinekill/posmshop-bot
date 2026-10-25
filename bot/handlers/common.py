"""/start, /help, /cancel и ответ на непонятные сообщения."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from .. import keyboards as kb
from .. import repo, texts
from ..db import Database
from ..states import Registration

router = Router(name="common")
fallback_router = Router(name="fallback")


async def show_menu(message: Message, db: Database, text: str) -> None:
    async with db.client(message.from_user.id) as conn:
        profile = await repo.get_user(conn, message.from_user.id)
    is_manager = bool(profile and profile["role"] in repo.MANAGER_ROLES)
    await message.answer(text, reply_markup=kb.main_menu(is_manager))


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, db: Database) -> None:
    await state.set_state(None)
    async with db.client(message.from_user.id) as conn:
        profile = await repo.get_user(conn, message.from_user.id)
    if profile is None:
        await state.set_state(Registration.consent)
        await message.answer(texts.CONSENT, reply_markup=kb.consent())
        return
    is_manager = profile["role"] in repo.MANAGER_ROLES
    await message.answer(
        f"Здравствуйте, {texts.h(profile['contact_name'])}! Выберите действие в меню.",
        reply_markup=kb.main_menu(is_manager))


@router.message(Command("cancel"))
@router.message(F.text.casefold() == "отмена")
async def cmd_cancel(message: Message, state: FSMContext, db: Database) -> None:
    current = await state.get_state()
    await state.set_state(None)
    if current and current.startswith("Registration"):
        await message.answer("Регистрация прервана. Чтобы начать заново, нажмите /start",
                             reply_markup=kb.remove())
        return
    await show_menu(message, db, "Действие отменено. Корзина сохранена.")


@router.message(Command("help"))
@router.message(F.text == kb.BTN_HELP)
async def cmd_help(message: Message) -> None:
    await message.answer(texts.HELP)


@fallback_router.message()
async def unknown(message: Message, db: Database) -> None:
    await show_menu(message, db, "Не понял сообщение. Воспользуйтесь кнопками меню или /help")
