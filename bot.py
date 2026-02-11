import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.types import Message, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

# ================= НАСТРОЙКИ =================

BOT_TOKEN = "8120789440:AAG6OC71xLVURNAxjYXdgZrfNeTtUuc9IHU"
ADMIN_ID = 5883796026
WEBAPP_URL = "https://pjhajfjahf.vercel.app/"

# ============================================

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


@dp.message(Command("start"))
async def start_handler(message: Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🚀 Открыть мини апп",
                web_app=WebAppInfo(url=WEBAPP_URL)
            )
        ]
    ])

    await message.answer(
        "Добро пожаловать 👑\n\nНажми кнопку ниже чтобы открыть мини апп.",
        reply_markup=keyboard
    )


@dp.message(Command("admin"))
async def admin_handler(message: Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer("✅ Ты админ.")
    else:
        await message.answer("❌ У тебя нет прав.")


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
