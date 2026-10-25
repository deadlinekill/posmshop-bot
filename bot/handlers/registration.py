"""Регистрация клиента: согласие на обработку ПДн, организация, контакт, телефон."""
from __future__ import annotations

import re

import asyncpg
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import keyboards as kb
from .. import repo, texts
from ..config import Config
from ..db import Database
from ..states import Registration

router = Router(name="registration")


def normalize_phone(raw: str) -> str | None:
    """Приводит номер к виду +7XXXXXXXXXX. Возвращает None, если это не номер."""
    if re.search(r"[^\d\s()+\-]", raw):
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    if not 11 <= len(digits) <= 15:
        return None
    return "+" + digits


@router.callback_query(Registration.consent, F.data == "consent:yes")
async def consent_yes(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.set_state(Registration.organization)
    await callback.message.answer("Спасибо! Введите название вашей организации:")
    await callback.answer()


@router.callback_query(Registration.consent, F.data == "consent:no")
async def consent_no(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_reply_markup(reply_markup=None)
    await state.set_state(None)
    await callback.message.answer(
        "Без согласия на обработку персональных данных регистрация невозможна. "
        "Если передумаете, нажмите /start. Также вы можете связаться с нами через сайт posmshop.ru")
    await callback.answer()


@router.message(Registration.consent)
async def consent_wait(message: Message) -> None:
    await message.answer("Пожалуйста, нажмите кнопку «Согласен» или «Не согласен».",
                         reply_markup=kb.consent())


@router.message(Registration.organization, F.text)
async def got_organization(message: Message, state: FSMContext) -> None:
    org = message.text.strip()
    if not 2 <= len(org) <= 200:
        await message.answer("Название должно быть от 2 до 200 символов. Введите ещё раз:")
        return
    await state.update_data(organization=org)
    await state.set_state(Registration.contact)
    await message.answer("Как к вам обращаться? Введите имя контактного лица:")


@router.message(Registration.contact, F.text)
async def got_contact(message: Message, state: FSMContext) -> None:
    name = message.text.strip()
    if not 2 <= len(name) <= 100:
        await message.answer("Имя должно быть от 2 до 100 символов. Введите ещё раз:")
        return
    await state.update_data(contact_name=name)
    await state.set_state(Registration.phone)
    await message.answer("Отправьте номер телефона кнопкой ниже или введите его вручную:",
                         reply_markup=kb.share_phone())


@router.message(Registration.phone, F.contact | F.text)
async def got_phone(message: Message, state: FSMContext, db: Database, cfg: Config) -> None:
    if message.contact:
        # Принимаем только собственный контакт пользователя, а не чужой
        if message.contact.user_id != message.from_user.id:
            await message.answer("Пожалуйста, отправьте свой номер, а не чужой контакт.")
            return
        phone = normalize_phone(message.contact.phone_number)
    else:
        phone = normalize_phone(message.text)
    if phone is None:
        await message.answer("Не похоже на номер телефона. Пример: +7 999 123-45-67. Введите ещё раз:")
        return

    data = await state.get_data()
    try:
        async with db.client(message.from_user.id) as conn:
            await repo.register_user(conn, message.from_user.id, data["organization"],
                                     data["contact_name"], phone, cfg.phone_key)
    except asyncpg.UniqueViolationError:
        pass  # уже зарегистрирован (например, нажал кнопку дважды)
    await state.set_state(None)
    await state.update_data(organization=None, contact_name=None)
    await message.answer(
        f"Регистрация завершена, {texts.h(data['contact_name'])}! "
        "Теперь можно открыть каталог и оформить заявку.",
        reply_markup=kb.main_menu())
