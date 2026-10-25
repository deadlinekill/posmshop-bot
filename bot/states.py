"""Состояния многошаговых диалогов (конечный автомат aiogram)."""
from aiogram.fsm.state import State, StatesGroup


class Registration(StatesGroup):
    consent = State()
    organization = State()
    contact = State()
    phone = State()


class Shop(StatesGroup):
    search = State()
    quantity = State()
    comment = State()


# Сообщения в этих состояниях содержат персональные данные,
# поэтому в журнал диалогов их текст не пишется.
PERSONAL_DATA_STATES = {Registration.organization.state,
                        Registration.contact.state,
                        Registration.phone.state}
