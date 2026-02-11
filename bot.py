import os
import hmac
import json
import time
import random
import hashlib
import sqlite3
import asyncio
from typing import Optional, Dict, Any

import aiohttp
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from urllib.parse import parse_qsl

from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import CommandStart


# =========================
# CONFIG
# =========================
BOT_TOKEN = os.getenv("8120789440:AAG6OC71xLVURNAxjYXdgZrfNeTtUuc9IHU", "").strip()
WEBAPP_URL = os.getenv("https://pjhajfjahf.vercel.app/", "").strip()  # публичный URL на /
ADMIN_ID = int(os.getenv("ADMIN_ID", "5883796026"))
DB_PATH = os.getenv("DB_PATH", "app.db")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан (ENV).")
if not WEBAPP_URL:
    raise RuntimeError("WEBAPP_URL не задан (ENV). Поставь ссылку на домен where this app runs, например https://your-app.example/")

# =========================
# DB
# =========================
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
        decided_at INTEGER
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

def add_balance(user_id: int, amount: float):
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()

def sub_balance(user_id: int, amount: float) -> bool:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    if not row or row["balance"] < amount:
        conn.close()
        return False
    cur.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()
    return True

def ensure_user_exists(user: dict):
    upsert_user(user)
    return get_user(int(user["id"]))


# =========================
# Telegram WebApp initData verify (robust)
# =========================
def verify_init_data(init_data: str) -> dict:
    # parse_qsl decodes percent-encoding correctly
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise HTTPException(401, "No hash in init_data")

    data_check_arr = [f"{k}={pairs[k]}" for k in sorted(pairs.keys())]
    data_check_string = "\n".join(data_check_arr)

    secret_key = hashlib.sha256(BOT_TOKEN.encode()).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        raise HTTPException(401, "init_data hash invalid")

    # basic freshness (up to 3 days)
    auth_date = int(pairs.get("auth_date", "0") or "0")
    if auth_date and (time.time() - auth_date) > 60 * 60 * 24 * 3:
        raise HTTPException(401, "init_data expired")

    user_json = pairs.get("user")
    if not user_json:
        raise HTTPException(401, "No user in init_data")

    user = json.loads(user_json)
    return user


# =========================
# FastAPI
# =========================
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # можно сузить позже
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def require_user(request: Request) -> dict:
    init_data = request.headers.get("X-TG-INITDATA")
    if not init_data:
        raise HTTPException(401, "Missing X-TG-INITDATA")
    user = verify_init_data(init_data)
    ensure_user_exists(user)
    return user

def require_admin(user: dict):
    if int(user["id"]) != ADMIN_ID:
        raise HTTPException(403, "Admin only")


@app.get("/", response_class=HTMLResponse)
async def root():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/api/me")
async def api_me(request: Request):
    user = require_user(request)
    row = get_user(int(user["id"]))
    return {
        "user_id": row["user_id"],
        "username": row["username"],
        "first_name": row["first_name"],
        "stars": row["stars"],
        "balance": float(row["balance"]),
        "is_admin": (row["user_id"] == ADMIN_ID),
    }


# =========================
# Telegram sendMessage helper (backend -> admin)
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
# Deposits (requests + admin approve/reject)
# =========================
@app.post("/api/deposit/request")
async def api_deposit_request(request: Request):
    user = require_user(request)
    body = await request.json()
    amount = float(body.get("amount", 0))
    note = (body.get("note") or "").strip()

    if amount <= 0:
        raise HTTPException(400, "Amount must be > 0")
    if amount > 10_000_000:
        raise HTTPException(400, "Too large")

    user_id = int(user["id"])
    username = user.get("username") or ""

    conn = db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO deposits (user_id, username, amount, note, status, created_at, decided_at)
        VALUES (?, ?, ?, ?, 'pending', ?, NULL)
    """, (user_id, username, amount, note, int(time.time())))
    dep_id = cur.lastrowid
    conn.commit()
    conn.close()

    uname = f"@{username}" if username else "—"
    user_link = f"<a href='tg://user?id={user_id}'>Открыть профиль</a>"
    txt = (
        f"💰 <b>Заявка на депозит</b>\n"
        f"ID: <code>{dep_id}</code>\n"
        f"Пользователь: {uname}\n"
        f"User ID: <code>{user_id}</code> • {user_link}\n"
        f"Сумма (Stars): <b>{amount}</b>\n"
    )
    if note:
        txt += f"Комментарий: <i>{note}</i>\n"

    kb = {
        "inline_keyboard": [[
            {"text": "✅ Принять", "callback_data": f"dep_ok:{dep_id}"},
            {"text": "❌ Отклонить", "callback_data": f"dep_no:{dep_id}"}
        ]]
    }
    await tg_send_message(ADMIN_ID, txt, kb)

    return {"ok": True, "deposit_id": dep_id}


@app.get("/api/admin/deposits")
async def api_admin_deposits(request: Request):
    user = require_user(request)
    require_admin(user)

    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM deposits ORDER BY id DESC LIMIT 200")
    rows = cur.fetchall()
    conn.close()

    return {"items": [dict(r) for r in rows]}


@app.post("/api/admin/deposits/{dep_id}/approve")
async def api_admin_approve(request: Request, dep_id: int):
    user = require_user(request)
    require_admin(user)

    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM deposits WHERE id = ?", (dep_id,))
    dep = cur.fetchone()
    if not dep:
        conn.close()
        raise HTTPException(404, "Not found")
    if dep["status"] != "pending":
        conn.close()
        return {"ok": True, "status": dep["status"]}

    cur.execute("UPDATE deposits SET status='approved', decided_at=? WHERE id=?", (int(time.time()), dep_id))
    cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (dep["amount"], dep["user_id"]))
    conn.commit()
    conn.close()
    return {"ok": True, "status": "approved"}


@app.post("/api/admin/deposits/{dep_id}/reject")
async def api_admin_reject(request: Request, dep_id: int):
    user = require_user(request)
    require_admin(user)

    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM deposits WHERE id = ?", (dep_id,))
    dep = cur.fetchone()
    if not dep:
        conn.close()
        raise HTTPException(404, "Not found")
    if dep["status"] != "pending":
        conn.close()
        return {"ok": True, "status": dep["status"]}

    cur.execute("UPDATE deposits SET status='rejected', decided_at=? WHERE id=?", (int(time.time()), dep_id))
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
    # stars never below 0
    cur.execute("SELECT stars FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "User not found")

    new_val = max(0, int(row["stars"]) + delta)
    cur.execute("UPDATE users SET stars = ? WHERE user_id = ?", (new_val, user_id))
    conn.commit()
    conn.close()
    return {"ok": True, "stars": new_val}


# =========================
# GAMES STATE (in-memory)
# =========================
MINES_GAMES: Dict[int, Dict[str, Any]] = {}
CRASH_GAMES: Dict[int, Dict[str, Any]] = {}

def calc_mines_multiplier(total_cells: int, mines: int, opened: int) -> float:
    safe = total_cells - mines
    if opened <= 0:
        return 1.0
    # smooth growth curve
    mult = (total_cells / safe) ** opened
    return round(mult, 4)

def pick_roulette_outcome() -> Dict[str, Any]:
    # probabilities: x0 40%, x2 45%, x5 15%
    r = random.random()
    if r < 0.40:
        return {"mult": 0, "label": "x0"}
    if r < 0.85:
        return {"mult": 2, "label": "x2"}
    return {"mult": 5, "label": "x5"}

def pick_crash_at() -> float:
    # skewed 1.15..10 (usually 1.2..3)
    r = random.random()
    base = 1.15 + (r ** 2.2) * 8.8
    return float(min(12.0, max(1.15, round(base, 2))))

def crash_multiplier(elapsed_s: float) -> float:
    # growth curve
    m = 1.0 + elapsed_s * 0.85 + (elapsed_s * elapsed_s) * 0.12
    return float(round(m, 2))


# =========================
# MINES API
# =========================
@app.post("/api/game/mines/start")
async def mines_start(request: Request):
    user = require_user(request)
    body = await request.json()

    size = int(body.get("size", 5))      # 3/5/10
    mines = int(body.get("mines", 1))    # 1..10
    bet = float(body.get("bet", 0))      # stars

    if size not in (3, 5, 10):
        raise HTTPException(400, "Bad size (3/5/10)")
    if mines < 1 or mines > 10:
        raise HTTPException(400, "Mines must be 1..10")

    total = size * size
    if mines >= total:
        raise HTTPException(400, "Too many mines")

    if bet <= 0 or bet > 1_000_000:
        raise HTTPException(400, "Bad bet")

    uid = int(user["id"])
    # stop previous
    MINES_GAMES.pop(uid, None)

    if not sub_balance(uid, bet):
        raise HTTPException(400, "Not enough balance")

    all_cells = list(range(total))
    mine_positions = set(random.sample(all_cells, mines))

    MINES_GAMES[uid] = {
        "size": size,
        "mines": mines,
        "bet": bet,
        "mine_positions": list(mine_positions),
        "opened": [],
        "active": True,
        "lost": False,
        "created_at": int(time.time())
    }

    return {"ok": True, "size": size, "mines": mines, "bet": bet}


@app.get("/api/game/mines/state")
async def mines_state(request: Request):
    user = require_user(request)
    uid = int(user["id"])
    g = MINES_GAMES.get(uid)
    if not g:
        return {"active": False}
    total = g["size"] * g["size"]
    opened = len(g["opened"])
    mult = calc_mines_multiplier(total, g["mines"], opened)
    return {
        "active": bool(g["active"]),
        "lost": bool(g["lost"]),
        "size": g["size"],
        "mines": g["mines"],
        "bet": g["bet"],
        "opened": g["opened"],
        "mult": mult,
        "potential": round(g["bet"] * mult, 2)
    }


@app.post("/api/game/mines/open")
async def mines_open(request: Request):
    user = require_user(request)
    uid = int(user["id"])
    g = MINES_GAMES.get(uid)
    if not g or not g["active"]:
        raise HTTPException(400, "No active game")

    body = await request.json()
    idx = int(body.get("index", -1))
    size = g["size"]
    total = size * size
    if idx < 0 or idx >= total:
        raise HTTPException(400, "Bad index")
    if idx in g["opened"]:
        return await mines_state(request)

    if idx in set(g["mine_positions"]):
        g["active"] = False
        g["lost"] = True
        # reveal mines on loss
        return {
            "boom": True,
            "mines": g["mine_positions"],
            **(await mines_state(request))
        }

    g["opened"].append(idx)
    return await mines_state(request)


@app.post("/api/game/mines/cashout")
async def mines_cashout(request: Request):
    user = require_user(request)
    uid = int(user["id"])
    g = MINES_GAMES.get(uid)
    if not g or not g["active"]:
        raise HTTPException(400, "No active game")
    if len(g["opened"]) <= 0:
        raise HTTPException(400, "Open at least 1 cell")

    total = g["size"] * g["size"]
    mult = calc_mines_multiplier(total, g["mines"], len(g["opened"]))
    win = round(g["bet"] * mult, 2)

    g["active"] = False
    add_balance(uid, win)
    return {"ok": True, "win": win, "mult": mult}


# =========================
# CRASH API
# =========================
@app.post("/api/game/crash/start")
async def crash_start(request: Request):
    user = require_user(request)
    body = await request.json()
    bet = float(body.get("bet", 0))
    auto = float(body.get("auto", 0))

    if bet <= 0 or bet > 1_000_000:
        raise HTTPException(400, "Bad bet")
    if auto != 0 and auto < 1.05:
        raise HTTPException(400, "Auto must be 0 or >= 1.05")
    if auto > 100:
        raise HTTPException(400, "Auto too high")

    uid = int(user["id"])
    CRASH_GAMES.pop(uid, None)

    if not sub_balance(uid, bet):
        raise HTTPException(400, "Not enough balance")

    CRASH_GAMES[uid] = {
        "bet": bet,
        "auto": auto,
        "started_at": time.time(),
        "crash_at": pick_crash_at(),
        "active": True,
        "cashed": False,
        "cash_mult": 0.0
    }
    return {"ok": True}


@app.get("/api/game/crash/state")
async def crash_state(request: Request):
    user = require_user(request)
    uid = int(user["id"])
    g = CRASH_GAMES.get(uid)
    if not g:
        return {"active": False}

    if not g["active"]:
        return {
            "active": False,
            "crashed": True,
            "crash_at": g["crash_at"],
            "cashed": g["cashed"],
            "cash_mult": g["cash_mult"]
        }

    elapsed = time.time() - g["started_at"]
    mult = crash_multiplier(elapsed)

    # crash?
    if mult >= g["crash_at"]:
        g["active"] = False
        return {
            "active": False,
            "crashed": True,
            "crash_at": g["crash_at"],
            "cashed": g["cashed"],
            "cash_mult": g["cash_mult"]
        }

    # auto cashout
    if g["auto"] and (not g["cashed"]) and mult >= g["auto"]:
        g["cashed"] = True
        g["cash_mult"] = float(g["auto"])
        win = round(g["bet"] * g["cash_mult"], 2)
        add_balance(uid, win)

    return {
        "active": True,
        "crashed": False,
        "mult": mult,
        "crash_at": g["crash_at"],
        "cashed": g["cashed"],
        "cash_mult": g["cash_mult"]
    }


@app.post("/api/game/crash/cashout")
async def crash_cashout(request: Request):
    user = require_user(request)
    uid = int(user["id"])
    g = CRASH_GAMES.get(uid)
    if not g:
        raise HTTPException(400, "No game")
    if not g["active"]:
        raise HTTPException(400, "Already crashed/ended")
    if g["cashed"]:
        return {"ok": True, "already": True, "mult": g["cash_mult"]}

    elapsed = time.time() - g["started_at"]
    mult = crash_multiplier(elapsed)
    if mult >= g["crash_at"]:
        g["active"] = False
        raise HTTPException(400, "Crashed")

    g["cashed"] = True
    g["cash_mult"] = mult
    win = round(g["bet"] * mult, 2)
    add_balance(uid, win)
    return {"ok": True, "win": win, "mult": mult}


# =========================
# ROULETTE API (instant settle)
# =========================
@app.post("/api/game/roulette/spin")
async def roulette_spin(request: Request):
    user = require_user(request)
    body = await request.json()
    bet = float(body.get("bet", 0))
    if bet <= 0 or bet > 1_000_000:
        raise HTTPException(400, "Bad bet")

    uid = int(user["id"])
    if not sub_balance(uid, bet):
        raise HTTPException(400, "Not enough balance")

    out = pick_roulette_outcome()
    mult = out["mult"]
    win = round(bet * mult, 2)

    # for animation: choose sector index (8 sectors)
    if mult == 0:
        sector_choices = [2, 6]
    elif mult == 5:
        sector_choices = [4]
    else:
        sector_choices = [0, 1, 3, 5, 7]
    sector = random.choice(sector_choices)

    if win > 0:
        add_balance(uid, win)

    return {
        "ok": True,
        "mult": mult,
        "label": out["label"],
        "win": win,
        "sector": sector
    }


# =========================
# Aiogram bot
# =========================
bot = Bot(BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()

@dp.message(CommandStart())
async def start_cmd(message: Message):
    kb = {
        "inline_keyboard": [
            [{"text": "🎮 Открыть мини-апп", "web_app": {"url": WEBAPP_URL}}],
        ]
    }
    await message.answer("✅ Открывай мини-аппку кнопкой ниже.", reply_markup=kb)

@dp.callback_query()
async def callbacks(call):
    if call.from_user.id != ADMIN_ID:
        await call.answer("Нет доступа", show_alert=True)
        return

    data = call.data or ""
    if data.startswith("dep_ok:") or data.startswith("dep_no:"):
        dep_id = int(data.split(":", 1)[1])

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
    asyncio.create_task(run_bot_polling())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
