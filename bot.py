import asyncio
import logging
import sqlite3
from datetime import datetime

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
)

# ================== НАСТРОЙКИ ==================

BOT_TOKEN = "8120789440:AAG6OC71xLVURNAxjYXdgZrfNeTtUuc9IHU"
ADMIN_ID = 5883796026
BASE_URL = "https://pjhajfjahf.vercel.app/"  # ← ЗАМЕНИ НА СВОЙ ДОМЕН

# ===============================================

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ================== БАЗА ==================

conn = sqlite3.connect("database.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    stars INTEGER DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS deposits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT,
    amount INTEGER,
    status TEXT,
    created_at TEXT
)
""")

conn.commit()

# ================== BOT ==================

@dp.message(Command("start"))
async def start(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🚀 Открыть мини-апп",
            web_app=WebAppInfo(url=f"{BASE_URL}/")
        )]
    ])

    await message.answer(
        "Добро пожаловать 👑\n\nОткрой мини-апп кнопкой ниже.",
        reply_markup=kb
    )


# ================== MINI APP ==================

@app.get("/", response_class=HTMLResponse)
async def miniapp():
    return """
    <!doctype html>
    <html>
    <head>
        <meta charset="utf-8">
        <script src="https://telegram.org/js/telegram-web-app.js"></script>
        <style>
            body { background:#0f111a; color:white; font-family:sans-serif; text-align:center; padding:20px; }
            button { padding:15px; margin:10px; border-radius:12px; border:none; background:#6f7bff; color:white; font-size:16px; }
            input { padding:10px; border-radius:10px; border:none; margin:10px; }
        </style>
    </head>
    <body>

    <h2>Siris Game</h2>
    <div id="balance"></div>

    <input type="number" id="amount" placeholder="Сумма депозита">
    <br>
    <button onclick="deposit()">Создать заявку</button>

    <div id="admin"></div>

    <script>
        const tg = window.Telegram.WebApp;
        tg.expand();

        async function load() {
            let res = await fetch("/profile", {
                method:"POST",
                headers:{"Content-Type":"application/json"},
                body: JSON.stringify({user: tg.initDataUnsafe.user})
            });
            let data = await res.json();

            document.getElementById("balance").innerText = "Баланс: " + data.stars;

            if (data.is_admin) {
                document.getElementById("admin").innerHTML =
                    '<button onclick="admin()">Админ панель</button>';
            }
        }

        async function deposit() {
            let amount = document.getElementById("amount").value;
            await fetch("/deposit", {
                method:"POST",
                headers:{"Content-Type":"application/json"},
                body: JSON.stringify({
                    user: tg.initDataUnsafe.user,
                    amount: amount
                })
            });
            alert("Заявка отправлена админу");
        }

        async function admin() {
            let res = await fetch("/admin/list");
            let data = await res.json();
            alert(JSON.stringify(data));
        }

        load();
    </script>

    </body>
    </html>
    """


@app.post("/profile")
async def profile(data: dict):
    user = data["user"]
    user_id = user["id"]
    username = user.get("username", "unknown")

    cursor.execute("INSERT OR IGNORE INTO users(user_id, username) VALUES(?,?)",
                   (user_id, username))
    conn.commit()

    cursor.execute("SELECT stars FROM users WHERE user_id=?",(user_id,))
    stars = cursor.fetchone()[0]

    return {
        "stars": stars,
        "is_admin": user_id == ADMIN_ID
    }


@app.post("/deposit")
async def create_deposit(data: dict):
    user = data["user"]
    amount = int(data["amount"])
    user_id = user["id"]
    username = user.get("username","unknown")

    cursor.execute("""
    INSERT INTO deposits(user_id, username, amount, status, created_at)
    VALUES(?,?,?,?,?)
    """, (user_id, username, amount, "pending", datetime.now().isoformat()))
    conn.commit()

    await bot.send_message(
        ADMIN_ID,
        f"Новая заявка на депозит\n@{username}\nID:{user_id}\nСумма:{amount}"
    )

    return {"ok":True}


@app.get("/admin/list")
async def admin_list():
    rows = cursor.execute("SELECT * FROM deposits WHERE status='pending'").fetchall()
    return {"pending": rows}


# ================== ЗАПУСК ==================

async def start_bot():
    await dp.start_polling(bot)

def start_api():
    uvicorn.run(app, host="0.0.0.0", port=8000)

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(start_bot())
    start_api()
