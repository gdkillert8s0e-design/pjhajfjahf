import logging
from aiogram import Bot, Dispatcher, types
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor

# ================== НАСТРОЙКИ ==================

BOT_TOKEN = "8120789440:AAG6OC71xLVURNAxjYXdgZrfNeTtUuc9IHU"
ADMIN_ID = 5883796026
WEBAPP_URL = "https://pjhajfjahf.vercel.app/"

# ===============================================

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot)


@dp.message_handler(commands=["start"])
async def start_handler(message: types.Message):
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton(
            text="🚀 Открыть мини апп",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )
    )

    await message.answer(
        "Добро пожаловать в игру 👑\n\n"
        "Нажми кнопку ниже чтобы открыть мини апп.",
        reply_markup=kb
    )


@dp.message_handler(commands=["admin"])
async def admin_check(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer("✅ Ты админ.")
    else:
        await message.answer("❌ У тебя нет прав.")


if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
