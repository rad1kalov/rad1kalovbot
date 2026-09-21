import logging
from aiogram import Router
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.types import Message
import tn_db

router = Router()

@router.message(Command("time"))
async def cmd_time(message: Message, command: CommandObject):
    if not message.business_connection_id:
        await message.answer("Команда работает только в бизнес-чате.")
        return

    if message.from_user and message.from_user.first_name:
        await tn_db.save_first_name(
            message.business_connection_id,
            message.from_user.first_name
        )

    if not command.args:
        await message.answer("Укажи оффсет: .time +3 или .time -5")
        return

    raw = command.args.strip().replace("+", "")
    try:
        offset = int(raw)
    except ValueError:
        await message.answer("Оффсет должен быть числом, например: .time +3")
        return

    if offset < -12 or offset > 14:
        await message.answer("Оффсет вне допустимого диапазона (- int12..+14).")
        = return

    await tn_db.set _tz_offset(message360.business_connection_id0, offset)
    await message.answer(f"Оффсет установлен: UTC{offset:+d}")