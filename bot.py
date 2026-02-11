import os
import hmac
import json
import time
import base64
import hashlib
import sqlite3
import asyncio
from typing import Optional

import aiohttp
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import CommandStart

# =========================
# CONFIG
# =========================
BOT_TOKEN = os.getenv("8120789440:AAG6OC71xLVURNAxjYXdgZrfNeTtUuc9IHU", "").strip()
WEBAPP_URL = os.getenv("t.me/hafgahgfahjfghabot/Hdjdvsbs", "").strip()  # ссылка на index.html (публичная)
ADMIN_ID = int(os.getenv("ADMIN_ID", "5883796026"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан. Добавь в переменные окружения.")
if not WEBAPP_URL:
    raise RuntimeError("WEBAPP_URL не задан. Это публичная ссылка на mini app (index.html).")

# =========================
# DB (SQLite)
# =========================
DB_PATH = os.getenv("DB_PATH", "app.db")

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        stars INTEGER DEFAULT 0,
        balance REAL DEFAULT 0
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS deposits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        username TEXT,
        amount REAL,
        note TEXT,
        status TEXT DEFAULT 'pending', -- pending/approved/rejected
        created_at INTEGER,
        decided_at INTEGER,
        FOREIGN KEY(user_id) REFERENCES users(user_id)
    )
    """)

    conn.commit()
    conn.close()

def upsert_user(user: dict):
    user_id = int(user["id"])
    username = user.get("username")
    first_name = user.get("first_name")

    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    exists = cur.fetchone()

    if exists:
        cur.execute("""
            UPDATE users
            SET username = ?, first_name = ?
            WHERE user_id = ?
        """, (username, first_name, user_id))
    else:
        cur.execute("""
            INSERT INTO users (user_id, username, first_name, stars, balance)
            VALUES (?, ?, ?, 0, 0)
        """, (user_id, username, first_name))
    conn.commit()
    conn.close()

def get_user(user_id: int):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row

def ensure_user_exists(user: dict):
    upsert_user(user)
    row = get_user(int(user["id"]))
    return row

# =========================
# Telegram WebApp initData verify
# =========================
def verify_init_data(init_data: str) -> dict:
    """
    Проверка initData от Telegram WebApp (HMAC SHA-256).
    Возвращает dict user из initDataUnsafe если валидно.
    """
    try:
        parsed = dict([kv.split("=", 1) for kv in init_data.split("&")])
    except Exception:
        raise HTTPException(400, "Bad init_data format")

    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise HTTPException(401, "No hash in init_data")

    data_check_arr = []
    for k in sorted(parsed.keys()):
        data_check_arr.append(f"{k}={parsed[k]}")
    data_check_string = "\n".join(data_check_arr)

    secret_key = hashlib.sha256(BOT_TOKEN.encode()).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        raise HTTPException(401, "init_data hash invalid")

    # optional: check auth_date freshness (e.g., 1 day)
    auth_date = int(parsed.get("auth_date", "0"))
    if auth_date and (time.time() - auth_date) > 60 * 60 * 24 * 3:
        raise HTTPException(401, "init_data expired")

    user_json = parsed.get("user")
    if not user_json:
        raise HTTPException(401, "No user in init_data")

    user = json.loads(user_json)
    return user

# =========================
# Telegram sendMessage helper (for backend -> admin)
# =========================
async def tg_send_message(chat_id: int, text: str, reply_markup: Optional[dict] = None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"Telegram sendMessage failed: {resp.status} {body}")

# =========================
# FastAPI
# =========================
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # для начала так; потом можно ограничить
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/", response_class=HTMLResponse)
async def root():
    # отдаём mini app прямо с backend, чтобы WEBAPP_URL мог быть на этот домен
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/health")
async def health():
    return {"ok": True}

def require_user(request: Request) -> dict:
    init_data = request.headers.get("X-TG-INITDATA")
    if not init_data:
        raise HTTPException(401, "Missing X-TG-INITDATA header")
    user = verify_init_data(init_data)
    ensure_user_exists(user)
    return user

def require_admin(user: dict):
    if int(user["id"]) != ADMIN_ID:
        raise HTTPException(403, "Admin only")

@app.get("/api/me")
async def api_me(request: Request):
    user = require_user(request)
    row = get_user(int(user["id"]))
    return {
        "user_id": row["user_id"],
        "username": row["username"],
        "first_name": row["first_name"],
        "stars": row["stars"],
        "balance": row["balance"],
        "is_admin": (row["user_id"] == ADMIN_ID),
    }

@app.post("/api/deposit/request")
async def api_deposit_request(request: Request):
    user = require_user(request)
    body = await request.json()
    amount = float(body.get("amount", 0))
    note = (body.get("note") or "").strip()

    if amount <= 0:
        raise HTTPException(400, "Amount must be > 0")
    if amount > 1000000:
        raise HTTPException(400, "Too large")

    user_id = int(user["id"])
    username = user.get("username") or ""

    conn = db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO deposits (user_id, username, amount, note, status, created_at, decided_at)
        VALUES (?, ?, ?, ?, 'pending', ?, NULL)
    """, (user_id, username, amount, note, int(time.time())))
    deposit_id = cur.lastrowid
    conn.commit()
    conn.close()

    # сообщение админу: username + ссылка на user_id
    uname = f"@{username}" if username else "—"
    user_link = f"<a href='tg://user?id={user_id}'>Открыть профиль</a>"
    txt = (
        f"💰 <b>Заявка на депозит</b>\n"
        f"ID заявки: <code>{deposit_id}</code>\n"
        f"Пользователь: {uname}\n"
        f"User ID: <code>{user_id}</code> • {user_link}\n"
        f"Сумма: <b>{amount}</b>\n"
    )
    if note:
        txt += f"Комментарий: <i>{note}</i>\n"

    kb = {
        "inline_keyboard": [
            [
                {"text": "✅ Принять", "callback_data": f"dep_ok:{deposit_id}"},
                {"text": "❌ Отклонить", "callback_data": f"dep_no:{deposit_id}"},
            ]
        ]
    }
    await tg_send_message(ADMIN_ID, txt, kb)

    return {"ok": True, "deposit_id": deposit_id}

@app.get("/api/admin/deposits")
async def api_admin_deposits(request: Request):
    user = require_user(request)
    require_admin(user)

    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM deposits ORDER BY id DESC LIMIT 100")
    rows = cur.fetchall()
    conn.close()

    return {"items": [dict(r) for r in rows]}

@app.post("/api/admin/deposits/{deposit_id}/approve")
async def api_admin_approve(request: Request, deposit_id: int):
    user = require_user(request)
    require_admin(user)

    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM deposits WHERE id = ?", (deposit_id,))
    dep = cur.fetchone()
    if not dep:
        conn.close()
        raise HTTPException(404, "Deposit not found")
    if dep["status"] != "pending":
        conn.close()
        return {"ok": True, "status": dep["status"]}

    # approve: add balance
    cur.execute("UPDATE deposits SET status='approved', decided_at=? WHERE id=?", (int(time.time()), deposit_id))
    cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (dep["amount"], dep["user_id"]))
    conn.commit()
    conn.close()

    return {"ok": True, "status": "approved"}

@app.post("/api/admin/deposits/{deposit_id}/reject")
async def api_admin_reject(request: Request, deposit_id: int):
    user = require_user(request)
    require_admin(user)

    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM deposits WHERE id = ?", (deposit_id,))
    dep = cur.fetchone()
    if not dep:
        conn.close()
        raise HTTPException(404, "Deposit not found")
    if dep["status"] != "pending":
        conn.close()
        return {"ok": True, "status": dep["status"]}

    cur.execute("UPDATE deposits SET status='rejected', decided_at=? WHERE id=?", (int(time.time()), deposit_id))
    conn.commit()
    conn.close()

    return {"ok": True, "status": "rejected"}

@app.post("/api/admin/users/{user_id}/stars")
async def api_admin_stars(request: Request, user_id: int):
    user = require_user(request)
    require_admin(user)

    body = await request.json()
    delta = int(body.get("delta", 0))
    if delta == 0:
        return {"ok": True}

    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET stars = MAX(0, stars + ?) WHERE user_id = ?", (delta, user_id))
    conn.commit()
    conn.close()
    return {"ok": True}

# =========================
# Aiogram bot (polling)
# =========================
bot = Bot(BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()

@dp.message(CommandStart())
async def start_cmd(message: Message):
    # кнопка открытия webapp
    # в WebApp URL должен быть публичный URL (тот же что WEBAPP_URL)
    # Telegram сам откроет mini app
    kb = {
        "inline_keyboard": [
            [{"text": "🎮 Открыть мини-апп", "web_app": {"url": WEBAPP_URL}}],
        ]
    }
    await message.answer(
        "✅ Готово.\nНажми кнопку ниже, чтобы открыть мини-аппку.",
        reply_markup=kb
    )

@dp.callback_query()
async def callbacks(call):
    # Админские кнопки approve/reject
    if call.from_user.id != ADMIN_ID:
        await call.answer("Нет доступа", show_alert=True)
        return

    data = call.data or ""
    if data.startswith("dep_ok:") or data.startswith("dep_no:"):
        dep_id = int(data.split(":", 1)[1])

        # меняем в БД
        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM deposits WHERE id=?", (dep_id,))
        dep = cur.fetchone()
        if not dep:
            conn.close()
            await call.answer("Заявка не найдена", show_alert=True)
            return

        if dep["status"] != "pending":
            conn.close()
            await call.answer(f"Уже обработано: {dep['status']}", show_alert=True)
            return

        if data.startswith("dep_ok:"):
            cur.execute("UPDATE deposits SET status='approved', decided_at=? WHERE id=?", (int(time.time()), dep_id))
            cur.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (dep["amount"], dep["user_id"]))
            conn.commit()
            conn.close()
            await call.message.edit_text(call.message.text + "\n\n✅ <b>Принято</b>")
            await call.answer("Принято ✅")
        else:
            cur.execute("UPDATE deposits SET status='rejected', decided_at=? WHERE id=?", (int(time.time()), dep_id))
            conn.commit()
            conn.close()
            await call.message.edit_text(call.message.text + "\n\n❌ <b>Отклонено</b>")
            await call.answer("Отклонено ❌")
        return

    await call.answer()

async def run_bot_polling():
    await dp.start_polling(bot)

# =========================
# Run
# =========================
init_db()

@app.on_event("startup")
async def on_startup():
    # запускаем polling бота параллельно с API
    asyncio.create_task(run_bot_polling())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
