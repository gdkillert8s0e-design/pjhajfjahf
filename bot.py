import os
import sqlite3
import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
import aiohttp

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Токены
TELEGRAM_TOKEN = "8511592619:AAHPFOr6MBXq8PNFCdEfNe37J9YDIX8kQes"
GROQ_API_KEY = "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"

# Инициализация бота
bot = Bot(
    token=TELEGRAM_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

# Константы
BOT_NAME = "сирис"
MODEL_NAME = "llama-3.3-70b-versatile"

# ИСПОЛЬЗУЕМ АЛЬТЕРНАТИВНЫЕ API ENDPOINTS
GROQ_ENDPOINTS = [
    "https://api.groq.com/openai/v1/chat/completions",
    "https://groq.com/api/v1/chat/completions",  
]

# Инициализация БД
def init_db():
    try:
        conn = sqlite3.connect('bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                username TEXT,
                message_text TEXT NOT NULL,
                is_bot BOOLEAN NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()
        logger.info("✅ Database initialized")
    except Exception as e:
        logger.error(f"❌ Database error: {e}")

def save_message(user_id: int, chat_id: int, username: str, message_text: str, is_bot: bool):
    try:
        conn = sqlite3.connect('bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO messages (user_id, chat_id, username, message_text, is_bot)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, chat_id, username, message_text, is_bot))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Save error: {e}")

def get_chat_history(chat_id: int, user_id: int, limit: int = 10):
    try:
        conn = sqlite3.connect('bot.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT message_text, is_bot FROM messages
            WHERE chat_id = ? AND user_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (chat_id, user_id, limit))
        rows = cursor.fetchall()
        conn.close()
        
        history = []
        for text, is_bot in reversed(rows):
            role = "assistant" if is_bot else "user"
            history.append({"role": role, "content": text})
        return history
    except Exception as e:
        logger.error(f"❌ History error: {e}")
        return []

async def get_ai_response(messages: list) -> str:
    """Получение ответа от AI с множественными попытками"""
    
    # Простые ответы на базовые фразы (фолбэк)
    user_msg = messages[-1]["content"].lower() if messages else ""
    
    simple_responses = {
        "привет": "Привет! 👋 Как дела? Чем могу помочь?",
        "как дела": "У меня все отлично! 😊 Спасибо что спросил. А у тебя как?",
        "спасибо": "Пожалуйста! 😊 Рад помочь!",
        "пока": "Пока! 👋 Возвращайся если что!",
        "кто ты": "Я Сирис - AI-ассистент на базе Groq! 🤖 Помогаю отвечать на вопросы и общаюсь.",
    }
    
    for key, response in simple_responses.items():
        if key in user_msg:
            logger.info(f"✅ Использую простой ответ для '{key}'")
            return response
    
    # Пробуем Groq API
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {
                "role": "system",
                "content": "Ты умный AI-ассистент по имени Сирис. Отвечай дружелюбно, помогай пользователям. Используй emoji. Отвечай на русском языке. Будь кратким - 2-3 предложения."
            }
        ] + messages,
        "temperature": 0.7,
        "max_tokens": 512
    }
    
    logger.info(f"🔄 Пробую подключиться к Groq API...")
    
    # Пробуем разные endpoints
    for endpoint in GROQ_ENDPOINTS:
        try:
            logger.info(f"📡 Попытка: {endpoint}")
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    endpoint,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                    ssl=False  # Отключаем SSL проверку
                ) as response:
                    
                    logger.info(f"📊 HTTP {response.status}")
                    
                    if response.status == 200:
                        data = await response.json()
                        ai_response = data['choices'][0]['message']['content']
                        logger.info(f"✅ Groq API работает!")
                        return ai_response
                    else:
                        error = await response.text()
                        logger.warning(f"⚠️ {endpoint}: {response.status} - {error[:100]}")
                        
        except Exception as e:
            logger.warning(f"⚠️ {endpoint} failed: {e}")
            continue
    
    # Если все API не работают - используем умный фолбэк
    logger.warning("⚠️ Все Groq endpoints недоступны, использую фолбэк")
    
    # Умные ответы на основе ключевых слов
    fallback_responses = {
        ("что", "как", "почему", "зачем", "когда", "где"): 
            "Хороший вопрос! 🤔 К сожалению, сейчас у меня проблемы с подключением к AI серверу. Попробуй спросить по-другому или позже!",
        ("помоги", "помощь", "нужна"):
            "С удовольствием помогу! 😊 Но сейчас у меня временные проблемы с AI сервером. Попробуй чуть позже!",
        ("расскажи", "объясни"):
            "Я бы с радостью рассказал! 📚 Но сейчас AI сервер недоступен. Попробуй позже!",
    }
    
    for keywords, response in fallback_responses.items():
        if any(word in user_msg for word in keywords):
            return response
    
    # Дефолтный ответ
    return f"Извини, сейчас у меня проблемы с подключением к AI серверу (ошибка 403) 😔\n\nНо я все равно здесь! Попробуй:\n• Задать простой вопрос\n• Написать позже\n• Использовать мини-приложение (там работает!)"

async def should_respond(message: Message) -> bool:
    try:
        if message.chat.type == 'private':
            return True
        
        text_lower = message.text.lower() if message.text else ""
        
        if message.reply_to_message and message.reply_to_message.from_user.id == bot.id:
            return True
        
        if BOT_NAME in text_lower:
            return True
        
        if message.entities:
            for entity in message.entities:
                if entity.type == "mention":
                    bot_info = await bot.get_me()
                    mention = message.text[entity.offset:entity.offset + entity.length]
                    if mention.lower().replace('@', '') == bot_info.username.lower():
                        return True
        
        return False
    except Exception as e:
        logger.error(f"❌ should_respond error: {e}")
        return False

@dp.message(CommandStart())
async def cmd_start(message: Message):
    try:
        user_name = message.from_user.first_name
        welcome_text = f"""
<b>👋 Привет, {user_name}!</b>

Я <b>Сирис</b> - AI-ассистент 🤖

<b>⚠️ ВАЖНО:</b>
На этом хостинге заблокирован Groq API (ошибка 403).
Для полноценной работы используй <b>мини-приложение</b>!

<b>Что умею здесь:</b>
• Отвечаю на простые фразы
• Помню контекст разговора
• Работаю в группах

<b>Команды:</b>
/start - Это сообщение
/clear - Очистить историю
/help - Справка
/webapp - Ссылка на мини-приложение

Готов общаться! 💬
"""
        await message.answer(welcome_text)
        save_message(message.from_user.id, message.chat.id, 
                    message.from_user.username or message.from_user.first_name, "/start", False)
        logger.info(f"✅ User {message.from_user.id} started")
    except Exception as e:
        logger.error(f"❌ cmd_start error: {e}")

@dp.message(Command("webapp"))
async def cmd_webapp(message: Message):
    """Информация о мини-приложении"""
    try:
        webapp_text = """
<b>📱 Мини-приложение Сирис</b>

В мини-приложении <b>полностью работает</b> AI! 🚀

<b>Там доступно:</b>
✅ Полноценный Groq AI
✅ Память разговора
✅ 5 тем оформления
✅ Анимированные эффекты
✅ Быстрые ответы

<b>Как открыть:</b>
Нажми кнопку "☰ Menu" → найди кнопку с приложением

Или попроси администратора добавить кнопку через @BotFather!
"""
        await message.answer(webapp_text)
    except Exception as e:
        logger.error(f"❌ cmd_webapp error: {e}")

@dp.message(Command("clear"))
async def cmd_clear(message: Message):
    try:
        conn = sqlite3.connect('bot.db')
        cursor = conn.cursor()
        cursor.execute('DELETE FROM messages WHERE chat_id = ? AND user_id = ?', 
                      (message.chat.id, message.from_user.id))
        conn.commit()
        conn.close()
        await message.answer("<b>✅ История очищена!</b>")
        logger.info(f"✅ User {message.from_user.id} cleared history")
    except Exception as e:
        logger.error(f"❌ cmd_clear error: {e}")

@dp.message(Command("help"))
async def cmd_help(message: Message):
    try:
        help_text = """
<b>📖 Помощь по боту Сирис</b>

<b>⚠️ Ограничения:</b>
Хостинг блокирует Groq API (403).
Для AI используй мини-приложение!

<b>Что работает:</b>
• Простые ответы на фразы
• Память разговора
• Работа в группах

<b>Команды:</b>
/start - Приветствие
/clear - Очистить историю
/help - Эта справка
/webapp - Инфо о мини-приложении

<b>В группах отвечаю когда:</b>
1️⃣ Ответ на мое сообщение
2️⃣ Написано "сирис"
3️⃣ Упоминание через @

<i>Используй мини-приложение для AI! 🚀</i>
"""
        await message.answer(help_text)
    except Exception as e:
        logger.error(f"❌ cmd_help error: {e}")

@dp.message(F.text)
async def handle_message(message: Message):
    try:
        if not await should_respond(message):
            return
        
        logger.info(f"📨 Message from {message.from_user.id}: {message.text[:50]}")
        
        await bot.send_chat_action(message.chat.id, "typing")
        
        user_text = message.text
        user_id = message.from_user.id
        chat_id = message.chat.id
        username = message.from_user.username or message.from_user.first_name
        
        save_message(user_id, chat_id, username, user_text, False)
        
        history = get_chat_history(chat_id, user_id, limit=10)
        history.append({"role": "user", "content": user_text})
        
        ai_response = await get_ai_response(history)
        
        save_message(user_id, chat_id, "bot", ai_response, True)
        
        try:
            await message.answer(f"<b>🤖 Сирис:</b>\n\n{ai_response}")
            logger.info(f"✅ Response sent to {message.from_user.id}")
        except Exception as e:
            logger.error(f"❌ Send error: {e}")
            await message.answer(ai_response)
            
    except Exception as e:
        logger.error(f"❌ handle_message error: {e}")
        try:
            await message.answer("Ошибка обработки 😔")
        except:
            pass

@dp.message(F.new_chat_members)
async def new_member(message: Message):
    try:
        for member in message.new_chat_members:
            if member.id == bot.id:
                greeting = """
<b>👋 Привет всем!</b>

Я <b>Сирис</b> - AI-ассистент 🤖

⚠️ На этом хостинге ограничен AI.
Для полной версии используйте мини-приложение!

Отвечу когда:
• Ответите на мое сообщение
• Напишете "сирис"
• Упомянете через @

<i>Давайте общаться!</i> 💬
"""
                await message.answer(greeting)
                logger.info(f"✅ Bot added to group {message.chat.id}")
    except Exception as e:
        logger.error(f"❌ new_member error: {e}")

async def main():
    try:
        init_db()
        
        bot_info = await bot.get_me()
        logger.info(f"")
        logger.info(f"╔════════════════════════════════════════════════════════╗")
        logger.info(f"║  🤖 БОТ ЗАПУЩЕН (РЕЖИМ FALLBACK)                     ║")
        logger.info(f"║  Username: @{bot_info.username:<40} ║")
        logger.info(f"║                                                        ║")
        logger.info(f"║  ⚠️  GROQ API ЗАБЛОКИРОВАН ХОСТИНГОМ (403)            ║")
        logger.info(f"║  ✅ Работают простые ответы + фолбэк                  ║")
        logger.info(f"║  📱 Для AI используй мини-приложение!                 ║")
        logger.info(f"╚════════════════════════════════════════════════════════╝")
        logger.info(f"")
        
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
        
    except Exception as e:
        logger.error(f"❌ Main error: {e}")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("⏹ Stopped")
    except Exception as e:
        logger.error(f"❌ Crashed: {e}")
