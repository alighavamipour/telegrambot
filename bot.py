# =========================================================
# bot.py — SoundCloud Bot + VIP + Wallet + Referrals + Admin Panel
# =========================================================
#
# جداول لازم در Supabase (SQL Editor):
#
# ---------- قبلی‌ها ----------
# USERS
# CREATE TABLE IF NOT EXISTS users (
#     user_id BIGINT PRIMARY KEY,
#     created_at TIMESTAMP DEFAULT NOW()
# );
#
# SETTINGS
# CREATE TABLE IF NOT EXISTS settings (
#     user_id BIGINT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
#     quality TEXT DEFAULT 'best',
#     updated_at TIMESTAMP DEFAULT NOW()
# );
#
# HISTORY
# CREATE TABLE IF NOT EXISTS history (
#     id BIGSERIAL PRIMARY KEY,
#     user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
#     title TEXT NOT NULL,
#     source TEXT,
#     created_at TIMESTAMP DEFAULT NOW()
# );
#
# JOBS
# CREATE TABLE IF NOT EXISTS jobs (
#     job_id TEXT PRIMARY KEY,
#     user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
#     playlist_title TEXT NOT NULL,
#     source_url TEXT NOT NULL,
#     total_tracks INT NOT NULL,
#     status TEXT DEFAULT 'running',
#     created_at TIMESTAMP DEFAULT NOW(),
#     updated_at TIMESTAMP DEFAULT NOW()
# );
#
# JOB_TRACKS
# CREATE TABLE IF NOT EXISTS job_tracks (
#     job_id TEXT REFERENCES jobs(job_id) ON DELETE CASCADE,
#     track_index INT,
#     title TEXT NOT NULL,
#     status TEXT DEFAULT 'pending',
#     PRIMARY KEY (job_id, track_index)
# );
#
# ADMINS
# CREATE TABLE IF NOT EXISTS admins (
#     user_id BIGINT PRIMARY KEY,
#     role TEXT DEFAULT 'admin',
#     created_at TIMESTAMP DEFAULT NOW()
# );
#
# VIP
# CREATE TABLE IF NOT EXISTS vip_users (
#     user_id BIGINT PRIMARY KEY,
#     plan TEXT NOT NULL,
#     expires_at TIMESTAMP NOT NULL,
#     created_at TIMESTAMP DEFAULT NOW()
# );
#
# PAYMENTS
# CREATE TABLE IF NOT EXISTS payments (
#     id BIGSERIAL PRIMARY KEY,
#     user_id BIGINT NOT NULL,
#     plan TEXT NOT NULL,
#     amount INT NOT NULL,
#     created_at TIMESTAMP DEFAULT NOW()
# );
#
# USER_LIMITS
# CREATE TABLE IF NOT EXISTS user_limits (
#     id BIGSERIAL PRIMARY KEY,
#     max_daily_downloads INT DEFAULT 1,
#     max_playlist_tracks INT DEFAULT 0,
#     max_quality TEXT DEFAULT '192',
#     reset_hour INT DEFAULT 0,
#     updated_at TIMESTAMP DEFAULT NOW()
# );
#
# USER_DAILY_USAGE
# CREATE TABLE IF NOT EXISTS user_daily_usage (
#     user_id BIGINT,
#     date DATE,
#     downloads INT DEFAULT 0,
#     PRIMARY KEY (user_id, date)
# );
#
# ANALYTICS
# CREATE TABLE IF NOT EXISTS analytics (
#     id BIGSERIAL PRIMARY KEY,
#     user_id BIGINT,
#     action TEXT,
#     meta JSONB,
#     created_at TIMESTAMP DEFAULT NOW()
# );
#
# ---------- جدیدها ----------
# WALLETS
# CREATE TABLE IF NOT EXISTS wallets (
#     user_id BIGINT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
#     address TEXT UNIQUE NOT NULL,
#     balance BIGINT DEFAULT 0,
#     created_at TIMESTAMP DEFAULT NOW(),
#     updated_at TIMESTAMP DEFAULT NOW()
# );
#
# WALLET_TRANSACTIONS
# CREATE TABLE IF NOT EXISTS wallet_transactions (
#     id BIGSERIAL PRIMARY KEY,
#     from_user BIGINT,
#     to_user BIGINT,
#     amount BIGINT NOT NULL,
#     type TEXT NOT NULL,  -- invite_reward / admin_grant / vip_purchase / withdraw_request / transfer
#     meta JSONB,
#     created_at TIMESTAMP DEFAULT NOW()
# );
#
# REFERRALS
# CREATE TABLE IF NOT EXISTS referrals (
#     id BIGSERIAL PRIMARY KEY,
#     inviter_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
#     invited_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
#     created_at TIMESTAMP DEFAULT NOW(),
#     UNIQUE (invited_id)
# );

import os
import re
import json
import httpx
import logging
import asyncio
import secrets
import string
from uuid import uuid4
from datetime import datetime, date, timedelta

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.error import BadRequest

# ================= ENV & CONSTANTS =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME")  # برای لینک رفرال: مثلاً mybot
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME")
CHANNEL_ID = os.getenv("CHANNEL_ID")
BASE_URL = os.getenv("BASE_URL")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

OWNER_ID = int(os.getenv("OWNER_ID", "0"))

DOWNLOAD_DIR = "downloads"
COVER_PATH = "cover.jpg"

MAX_AUDIO_DL_LIMIT = 20 * 1024 * 1024
MAX_FILE_SIZE = 50 * 1024 * 1024

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

SC_REGEX = re.compile(r"https?://(?:on\.)?soundcloud\.com/[^\s]+")
REF_START_REGEX = re.compile(r"^/start\s+ref_(\d+)$")

# قیمت سکه‌ای VIP (بعداً می‌تونی از DB بخونی)
VIP_COIN_PRICES = {
    "monthly": 30,
    "quarterly": 80,
    "yearly": 250,
}

INVITE_REWARD_COINS = 1  # هر دعوت = 1 سکه

# =========================================================
# =============== SUPABASE REST API CLIENT ================
# =========================================================

class SupabaseDB:
    def __init__(self, url, key):
        self.url = url.rstrip("/")
        self.key = key
        self.base_headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

    def _headers(self, prefer_return_representation: bool = False, prefer_upsert: bool = False):
        headers = self.base_headers.copy()
        prefers = []
        if prefer_return_representation:
            prefers.append("return=representation")
        if prefer_upsert:
            prefers.append("resolution=merge-duplicates")
        if prefers:
            headers["Prefer"] = ",".join(prefers)
        return headers

    async def insert(self, table, data):
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self.url}/rest/v1/{table}",
                headers=self._headers(prefer_return_representation=True),
                json=data,
            )
            r.raise_for_status()
            return r.json()

    async def select(self, table, filters=None, limit=None, order=None):
        params = {}
        if filters:
            for k, v in filters.items():
                params[k] = f"eq.{v}"
        if limit:
            params["limit"] = limit
        if order:
            params["order"] = order

        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{self.url}/rest/v1/{table}",
                headers=self._headers(prefer_return_representation=True),
                params=params,
            )
            r.raise_for_status()
            return r.json()

    async def update(self, table, filters, data):
        params = {k: f"eq.{v}" for k, v in filters.items()}
        async with httpx.AsyncClient() as client:
            r = await client.patch(
                f"{self.url}/rest/v1/{table}",
                headers=self._headers(prefer_return_representation=True),
                params=params,
                json=data,
            )
            r.raise_for_status()
            return r.json()

    async def delete(self, table, filters):
        params = {k: f"eq.{v}" for k, v in filters.items()}
        async with httpx.AsyncClient() as client:
            r = await client.delete(
                f"{self.url}/rest/v1/{table}",
                headers=self._headers(prefer_return_representation=True),
                params=params,
            )
            r.raise_for_status()
            return r.json()

    async def upsert(self, table, data, on_conflict: str):
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{self.url}/rest/v1/{table}",
                headers=self._headers(
                    prefer_return_representation=True,
                    prefer_upsert=True
                ),
                params={"on_conflict": on_conflict},
                json=data,
            )
            r.raise_for_status()
            return r.json()


db = SupabaseDB(SUPABASE_URL, SUPABASE_KEY)

# =========================================================
# ==================== DATABASE FUNCTIONS =================
# =========================================================

# ---------- USERS ----------
async def save_user(uid: int):
    try:
        await db.insert("users", {"user_id": uid})
    except Exception:
        pass

# ---------- SETTINGS ----------
async def set_user_quality(uid: int, quality: str):
    await db.upsert(
        "settings",
        {
            "user_id": uid,
            "quality": quality,
            "updated_at": datetime.utcnow().isoformat(),
        },
        on_conflict="user_id",
    )

async def get_user_quality(uid: int) -> str:
    rows = await db.select("settings", {"user_id": uid}, limit=1)
    if rows:
        return rows[0].get("quality", "best")
    return "best"

# ---------- HISTORY ----------
async def add_history(uid: int, title: str, source: str):
    await db.insert(
        "history",
        {
            "user_id": uid,
            "title": title,
            "source": source,
            "created_at": datetime.utcnow().isoformat(),
        },
    )

async def get_history(uid: int, limit: int = 10):
    rows = await db.select(
        "history",
        {"user_id": uid},
        limit=limit,
        order="id.desc",
    )
    result = []
    for r in rows:
        result.append(
            (
                r.get("title", ""),
                r.get("source", ""),
                r.get("created_at", ""),
            )
        )
    return result

# ---------- JOBS / RESUME ----------
async def create_job(job_id, user_id, playlist_title, url, total_tracks):
    await db.insert(
        "jobs",
        {
            "job_id": job_id,
            "user_id": user_id,
            "playlist_title": playlist_title,
            "source_url": url,
            "total_tracks": total_tracks,
            "status": "running",
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
        },
    )

async def create_job_tracks(job_id, tracks):
    rows = []
    for idx, t in enumerate(tracks):
        rows.append(
            {
                "job_id": job_id,
                "track_index": idx,
                "title": t["title"],
                "status": "pending",
            }
        )
    await db.insert("job_tracks", rows)

async def get_incomplete_job(user_id, url):
    rows = await db.select(
        "jobs",
        {"user_id": user_id, "source_url": url, "status": "running"},
        limit=1,
    )
    if not rows:
        return None
    row = rows[0]
    return (
        row["job_id"],
        row["playlist_title"],
        row["total_tracks"],
    )

async def get_pending_indices_for_job(job_id):
    rows = await db.select(
        "job_tracks",
        {"job_id": job_id},
        order="track_index.asc",
    )
    return [
        (r["track_index"], r["title"])
        for r in rows
        if r.get("status") != "sent"
    ]

async def mark_track_sent(job_id, index):
    await db.update(
        "job_tracks",
        {"job_id": job_id, "track_index": index},
        {"status": "sent"},
    )
    await db.update(
        "jobs",
        {"job_id": job_id},
        {"updated_at": datetime.utcnow().isoformat()},
    )

async def finish_job(job_id):
    await db.update(
        "jobs",
        {"job_id": job_id},
        {"status": "finished", "updated_at": datetime.utcnow().isoformat()},
    )

async def reset_job(job_id):
    await db.delete("job_tracks", {"job_id": job_id})
    await db.delete("jobs", {"job_id": job_id})

# ---------- ADMINS ----------
async def ensure_owner_admin():
    if not OWNER_ID:
        logging.warning("OWNER_ID is not set; owner admin cannot be ensured.")
        return
    try:
        rows = await db.select("admins", {"user_id": OWNER_ID}, limit=1)
        if rows:
            if rows[0].get("role") != "owner":
                await db.update("admins", {"user_id": OWNER_ID}, {"role": "owner"})
        else:
            await db.insert(
                "admins",
                {"user_id": OWNER_ID, "role": "owner"}
            )
        logging.info(f"Owner admin ensured for user_id={OWNER_ID}")
    except Exception as e:
        logging.error(f"ensure_owner_admin error: {e}")

async def is_admin(uid: int) -> bool:
    try:
        rows = await db.select("admins", {"user_id": uid}, limit=1)
        return bool(rows)
    except Exception as e:
        logging.error(f"is_admin error: {e}")
        return False

async def is_owner(uid: int) -> bool:
    return uid == OWNER_ID

async def add_admin(uid: int):
    try:
        await db.upsert(
            "admins",
            {"user_id": uid, "role": "admin"},
            on_conflict="user_id"
        )
    except Exception as e:
        logging.error(f"add_admin error: {e}")

async def remove_admin(uid: int):
    try:
        await db.delete("admins", {"user_id": uid})
    except Exception as e:
        logging.error(f"remove_admin error: {e}")

async def list_admins():
    try:
        rows = await db.select("admins")
        return rows
    except Exception as e:
        logging.error(f"list_admins error: {e}")
        return []

# ---------- VIP ----------
async def set_vip(uid: int, plan: str, days: int):
    now = datetime.utcnow()
    rows = await db.select("vip_users", {"user_id": uid}, limit=1)
    if rows:
        old_exp = datetime.fromisoformat(rows[0]["expires_at"].replace("Z", ""))
        base = old_exp if old_exp > now else now
    else:
        base = now
    new_exp = base + timedelta(days=days)
    await db.upsert(
        "vip_users",
        {
            "user_id": uid,
            "plan": plan,
            "expires_at": new_exp.isoformat(),
        },
        on_conflict="user_id"
    )

async def get_vip_info(uid: int):
    rows = await db.select("vip_users", {"user_id": uid}, limit=1)
    if not rows:
        return None
    return rows[0]

async def is_vip(uid: int) -> bool:
    info = await get_vip_info(uid)
    if not info:
        return False
    try:
        exp = datetime.fromisoformat(info["expires_at"].replace("Z", ""))
    except Exception:
        return False
    return exp > datetime.utcnow()

# ---------- PAYMENTS ----------
async def add_payment(uid: int, plan: str, amount: int):
    try:
        await db.insert(
            "payments",
            {
                "user_id": uid,
                "plan": plan,
                "amount": amount,
                "created_at": datetime.utcnow().isoformat(),
            }
        )
    except Exception as e:
        logging.error(f"add_payment error: {e}")

# ---------- USER LIMITS ----------
async def get_user_limits():
    rows = await db.select("user_limits", limit=1)
    if rows:
        return rows[0]
    defaults = {
        "max_daily_downloads": 1,
        "max_playlist_tracks": 0,
        "max_quality": "192",
        "reset_hour": 0,
        "updated_at": datetime.utcnow().isoformat(),
    }
    await db.insert("user_limits", defaults)
    return defaults

async def update_user_limits(data: dict):
    rows = await db.select("user_limits", limit=1)
    if not rows:
        await db.insert("user_limits", data)
    else:
        row_id = rows[0]["id"]
        data["updated_at"] = datetime.utcnow().isoformat()
        await db.update("user_limits", {"id": row_id}, data)

# ---------- USER DAILY USAGE ----------
async def get_user_daily_usage(uid: int, d: date):
    rows = await db.select(
        "user_daily_usage",
        {"user_id": uid, "date": d.isoformat()},
        limit=1,
    )
    if rows:
        return rows[0]["downloads"]
    return 0

async def increment_user_daily_usage(uid: int, d: date):
    current = await get_user_daily_usage(uid, d)
    if current == 0:
        await db.insert(
            "user_daily_usage",
            {"user_id": uid, "date": d.isoformat(), "downloads": 1},
        )
    else:
        await db.update(
            "user_daily_usage",
            {"user_id": uid, "date": d.isoformat()},
            {"downloads": current + 1},
        )

# ---------- ANALYTICS ----------
async def log_analytics(uid: int, action: str, meta: dict = None):
    try:
        await db.insert(
            "analytics",
            {
                "user_id": uid,
                "action": action,
                "meta": meta or {},
                "created_at": datetime.utcnow().isoformat(),
            }
        )
    except Exception as e:
        logging.error(f"log_analytics error: {e}")

async def get_basic_stats():
    today_str = date.today().isoformat()
    stats = {
        "downloads_today": 0,
        "vip_count": 0,
        "users_count": 0,
    }
    try:
        rows = await db.select("analytics", {"action": "download"})
        stats["downloads_today"] = sum(
            1 for r in rows
            if r.get("created_at", "").startswith(today_str)
        )
    except Exception:
        pass

    try:
        rows = await db.select("vip_users")
        stats["vip_count"] = len(rows)
    except Exception:
        pass

    try:
        rows = await db.select("users")
        stats["users_count"] = len(rows)
    except Exception:
        pass

    return stats

# ---------- WALLETS ----------
def generate_wallet_address() -> str:
    prefix = "SC"
    body = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(30))
    return prefix + body

async def get_or_create_wallet(uid: int):
    rows = await db.select("wallets", {"user_id": uid}, limit=1)
    if rows:
        return rows[0]
    while True:
        addr = generate_wallet_address()
        existing = await db.select("wallets", {"address": addr}, limit=1)
        if not existing:
            break
    wallet = await db.insert("wallets", {
        "user_id": uid,
        "address": addr,
        "balance": 0,
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    })
    return wallet[0]

async def get_wallet_by_address(address: str):
    rows = await db.select("wallets", {"address": address}, limit=1)
    return rows[0] if rows else None

async def update_wallet_balance(uid: int, delta: int):
    rows = await db.select("wallets", {"user_id": uid}, limit=1)
    if not rows:
        w = await get_or_create_wallet(uid)
        rows = [w]
    w = rows[0]
    new_balance = max(0, (w.get("balance", 0) or 0) + delta)
    updated = await db.update(
        "wallets",
        {"user_id": uid},
        {"balance": new_balance, "updated_at": datetime.utcnow().isoformat()},
    )
    return updated[0]["balance"]

async def get_wallet(uid: int):
    rows = await db.select("wallets", {"user_id": uid}, limit=1)
    if rows:
        return rows[0]
    return await get_or_create_wallet(uid)

# ---------- WALLET TRANSACTIONS ----------
async def add_wallet_tx(from_user, to_user, amount: int, tx_type: str, meta: dict = None):
    await db.insert(
        "wallet_transactions",
        {
            "from_user": from_user,
            "to_user": to_user,
            "amount": amount,
            "type": tx_type,
            "meta": meta or {},
            "created_at": datetime.utcnow().isoformat(),
        }
    )

# ---------- REFERRALS ----------
async def add_referral(inviter_id: int, invited_id: int):
    try:
        await db.insert(
            "referrals",
            {
                "inviter_id": inviter_id,
                "invited_id": invited_id,
                "created_at": datetime.utcnow().isoformat(),
            }
        )
        return True
    except Exception:
        return False

async def count_referrals(inviter_id: int) -> int:
    rows = await db.select("referrals", {"inviter_id": inviter_id})
    return len(rows)

# ---- is posted to channel ----
async def get_vip_settings(uid: int):
    rows = await db.select("vip_settings", {"user_id": uid}, limit=1)
    if rows:
        return rows[0]
    return {"post_to_channel": False}

async def set_vip_post_mode(uid: int, mode: bool):
    await db.upsert(
        "vip_settings",
        {
            "user_id": uid,
            "post_to_channel": mode,
            "updated_at": datetime.utcnow().isoformat(),
        },
        on_conflict="user_id"
    )


# =========================================================
# =========================== UTILS ========================
# =========================================================

def clean_filename(name: str) -> str:
    name = re.sub(r'\.(mp3|m4a|wav|flac|ogg|opus)$', '', name, flags=re.I)
    return name.strip() or "music"

def guess_ext(audio_obj) -> str:
    if getattr(audio_obj, "file_name", None):
        fn = audio_obj.file_name
        if "." in fn:
            return fn.split(".")[-1].lower()

    mime = getattr(audio_obj, "mime_type", "") or ""
    mime = mime.lower()

    if "mpeg" in mime:
        return "mp3"
    if "wav" in mime:
        return "wav"
    if "flac" in mime:
        return "flac"
    if "ogg" in mime:
        return "ogg"
    if "opus" in mime:
        return "opus"
    if "m4a" in mime or "mp4" in mime:
        return "m4a"
    return "mp3"

async def run_cmd(*cmd):
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise Exception(stderr.decode() or stdout.decode())

async def tag_and_cover(src: str, dst: str, title: str, vip: bool = False):
    comment = f"@{CHANNEL_USERNAME}"
    if vip:
        comment += " | VIP Download"
    await run_cmd(
        "ffmpeg",
        "-y",
        "-i", src,
        "-i", COVER_PATH,
        "-map", "0:a:0",
        "-map", "1:v:0",
        "-map_metadata", "-1",
        "-c:a", "libmp3lame",
        "-q:a", "2",
        "-c:v", "mjpeg",
        "-disposition:v", "attached_pic",
        "-id3v2_version", "3",
        "-metadata", f"title={title}",
        "-metadata", f"artist=@{CHANNEL_USERNAME}",
        "-metadata", f"album=@{CHANNEL_USERNAME}",
        "-metadata", f"comment={comment}",
        dst,
    )

async def resolve_soundcloud_url(url: str) -> str:
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
            r = await client.get(url)
            return str(r.url)
    except Exception:
        return url

def get_format_for_quality(q: str, vip: bool = False) -> str:
    if vip:
        return "bestaudio/best"
    if q in ("best", "بهترین"):
        return "bestaudio/best"
    if q == "320":
        return "bestaudio[abr>=256]/bestaudio[abr>=192]/bestaudio"
    if q == "192":
        return "bestaudio[abr<=192]/bestaudio"
    if q == "128":
        return "bestaudio[abr<=128]/bestaudio"
    return "bestaudio/best"

def make_playlist_hashtag(title: str) -> str:
    cleaned = re.sub(r'[^\w\u0600-\u06FF\s]+', '', title)
    cleaned = re.sub(r'\s+', '_', cleaned).strip('_')
    parts = cleaned.split('_')
    if len(parts) > 4:
        cleaned = '_'.join(parts[:4])
    if not cleaned:
        cleaned = "playlist"
    return f"#{cleaned}"

def parse_selection(text: str, max_n: int):
    result = set()
    parts = text.replace(" ", "").split(",")
    for p in parts:
        if "-" in p:
            try:
                a, b = p.split("-")
                a, b = int(a), int(b)
                if a > b:
                    a, b = b, a
                for i in range(a, b + 1):
                    if 1 <= i <= max_n:
                        result.add(i - 1)
            except Exception:
                continue
        else:
            if not p:
                continue
            try:
                i = int(p)
                if 1 <= i <= max_n:
                    result.add(i - 1)
            except Exception:
                continue
    return sorted(result)

# =========================================================
# =========================== QUEUE ========================
# =========================================================

queue: asyncio.Queue = asyncio.Queue()
CONCURRENCY = 2

async def worker():
    try:
        while True:
            task = await queue.get()
            try:
                await task()
            except Exception as e:
                logging.error(f"Worker error: {e}")
            finally:
                queue.task_done()
    except asyncio.CancelledError:
        logging.info("Worker stopped.")

async def start_workers(app):
    for _ in range(CONCURRENCY):
        asyncio.create_task(worker())
    logging.info("Workers started.")

# =========================================================
# ======================= FORCE JOIN ======================
# =========================================================

async def is_member(uid, context):
    try:
        m = await context.bot.get_chat_member(CHANNEL_ID, uid)
        return m.status in ("member", "administrator", "creator")
    except Exception:
        return False

async def force_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 عضویت در کانال", url=f"https://t.me/{CHANNEL_USERNAME}")],
        [InlineKeyboardButton("✔️ بررسی عضویت", callback_data="check_join")]
    ])
    await update.message.reply_text(
        "برای استفاده از ربات باید عضو کانال شوی:",
        reply_markup=kb
    )

# =========================================================
# ====================== GLOBAL STATE =====================
# =========================================================

pending_playlists = {}  # uid -> {...}
admin_flows = {}        # uid -> {"mode": str, "data": dict}
wallet_flows = {}       # uid -> {"mode": str, "data": dict}

# =========================================================
# ========================= HELP TEXT =====================
# =========================================================

HELP_TEXT = (
    "📖 راهنمای ربات:\n\n"
    "دستورات اصلی:\n"
    "• /start - شروع کار با ربات\n"
    "• /help - همین راهنما\n"
    "• /history - نمایش آخرین موزیک‌های پردازش‌شده\n"
    "• /quality - تنظیم کیفیت دانلود SoundCloud\n"
    "• /vip - مشاهده وضعیت VIP و مزایا\n"
    "• /wallet - مشاهده کیف پول سکه، آدرس، موجودی و امکانات\n\n"
    "نحوه استفاده:\n"
    "• لینک SoundCloud یا فایل صوتی بفرست تا ربات آن را تبدیل و ارسال کند.\n"
    "• لینک پلی‌لیست/ست ارسال کن تا ترک‌ها جداگانه برایت آماده شود (برای VIP).\n"
    "• با دعوت دوستان از طریق لینک اختصاصی‌ات سکه بگیر و با سکه VIP بخر.\n"
)

# =========================================================
# ========================= COMMANDS ======================
# =========================================================

async def handle_referral_if_any(update: Update, uid: int):
    text = update.message.text or ""
    m = REF_START_REGEX.match(text.strip())
    if not m:
        return
    inviter_id = int(m.group(1))
    if inviter_id == uid:
        return
    # آیا قبلاً به عنوان invited ثبت شده؟
    rows = await db.select("referrals", {"invited_id": uid}, limit=1)
    if rows:
        return
    ok = await add_referral(inviter_id, uid)
    if not ok:
        return
    # سکه جایزه به inviter
    new_balance = await update_wallet_balance(inviter_id, INVITE_REWARD_COINS)
    await add_wallet_tx(
        from_user=None,
        to_user=inviter_id,
        amount=INVITE_REWARD_COINS,
        type="invite_reward",
        meta={"invited_id": uid},
    )
    # پیام تبریک
    try:
        await update.get_bot().send_message(
            inviter_id,
            f"🎉 دعوت موفق جدید!\n\n"
            f"کاربر {uid} با لینک تو وارد شد.\n"
            f"+{INVITE_REWARD_COINS} سکه به کیف پولت اضافه شد.\n"
            f"موجودی فعلی: {new_balance} سکه."
        )
    except Exception as e:
        logging.warning(f"Failed to send invite reward message to {inviter_id}: {e}")

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user:
        return

    uid = update.message.from_user.id
    await save_user(uid)
    await get_or_create_wallet(uid)
    await handle_referral_if_any(update, uid)

    if not await is_member(uid, context):
        return await force_join(update, context)

    # منوی شیک شروع
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎵 دانلود موزیک", callback_data="menu:download"),
            InlineKeyboardButton("👑 VIP", callback_data="menu:vip"),
        ],
        [
            InlineKeyboardButton("💰 کیف پول", callback_data="menu:wallet"),
            InlineKeyboardButton("👥 دعوت دوستان", callback_data="menu:referral"),
        ],
        [
            InlineKeyboardButton("⚙️ تنظیم کیفیت", callback_data="menu:quality"),
            InlineKeyboardButton("📂 تاریخچه", callback_data="menu:history"),
        ],
        [
            InlineKeyboardButton("📖 راهنما", callback_data="menu:help"),
        ]
    ])

    await update.message.reply_text(
        "🎵 خوش آمدی به لوکس‌ترین SoundCloud Bot.\n\n"
        "فایل صوتی یا لینک SoundCloud بفرست، یا از منوی زیر استفاده کن:",
        reply_markup=kb
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user:
        return
    uid = update.message.from_user.id
    await save_user(uid)
    rows = await get_history(uid, 10)
    if not rows:
        return await update.message.reply_text("📂 هنوز هیچ موزیکی با ربات پردازش نکردی.")
    lines = []
    for title, source, created_at in rows:
        src = source if source != "forwarded" else "فایل فورواردی / آپلود"
        lines.append(f"• {title}\n  ↳ {src}")
    await update.message.reply_text("🕘 آخرین موزیک‌های پردازش‌شده:\n\n" + "\n\n".join(lines))

async def quality_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user:
        return

    uid = update.message.from_user.id
    await save_user(uid)
    current = await get_user_quality(uid)
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎧 بهترین", callback_data="q_best"),
            InlineKeyboardButton("🎚 320kbps", callback_data="q_320"),
        ],
        [
            InlineKeyboardButton("🎚 192kbps", callback_data="q_192"),
            InlineKeyboardButton("🎚 128kbps", callback_data="q_128"),
        ]
    ])
    await update.message.reply_text(
        f"🎚 کیفیت فعلی: {current}\n"
        "یکی از گزینه‌های زیر را انتخاب کن:",
        reply_markup=kb
    )

async def vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user:
        return
    uid = update.message.from_user.id
    info = await get_vip_info(uid)
    wallet = await get_wallet(uid)
    ref_count = await count_referrals(uid)
    [InlineKeyboardButton("📤 ارسال در کانال (VIP)", callback_data="vip:post_mode")]
    if await is_vip(uid):
        exp = info["expires_at"]
        txt = (
            "👑 وضعیت VIP شما:\n\n"
            f"پلن: {info['plan']}\n"
            f"انقضا: {exp}\n\n"
            "مزایا:\n"
            "• دانلود نامحدود\n"
            "• پلی‌لیست و ست کامل\n"
            "• کیفیت بهترین\n"
            "• ارسال مستقیم در چت شما\n\n"
            f"💰 موجودی سکه: {wallet['balance']}\n"
            f"👥 تعداد دعوت‌های موفق: {ref_count}\n"
        )
    else:
        limits = await get_user_limits()
        txt = (
            "❌ شما VIP نیستید.\n\n"
            "کاربران معمولی:\n"
            f"• حداکثر {limits['max_daily_downloads']} دانلود در روز\n"
            f"• بدون دسترسی به پلی‌لیست (در صورت تنظیم)\n"
            f"• کیفیت تا {limits['max_quality']}kbps\n\n"
            "👑 VIP:\n"
            "• دانلود نامحدود\n"
            "• پلی‌لیست و ست کامل\n"
            "• کیفیت بهترین\n"
            "• ارسال مستقیم در چت شما\n\n"
            f"💰 موجودی سکه: {wallet['balance']}\n"
            f"👥 دعوت‌های موفق: {ref_count}\n"
            "می‌توانی با سکه هم VIP بخری."
        )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👑 خرید VIP با سکه", callback_data="wallet:buy_vip")],
        [InlineKeyboardButton("💰 کیف پول", callback_data="menu:wallet")],
        [InlineKeyboardButton("👥 دعوت دوستان", callback_data="menu:referral")],
    ])
    await update.message.reply_text(txt, reply_markup=kb)

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user:
        return
    uid = update.message.from_user.id
    if not await is_admin(uid):
        return await update.message.reply_text("⛔️ شما به پنل مدیریت دسترسی ندارید.")

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👑 مدیریت VIP", callback_data="admin:vip")],
        [InlineKeyboardButton("📢 تبلیغات", callback_data="admin:ads")],
        [InlineKeyboardButton("⚙️ محدودیت کاربران معمولی", callback_data="admin:limits")],
        [InlineKeyboardButton("💰 مدیریت سکه/کیف پول", callback_data="admin:wallet")],
        [InlineKeyboardButton("🛠 مدیریت ادمین‌ها", callback_data="admin:admins")],
        [InlineKeyboardButton("📊 آمار و آنالیتیکس", callback_data="admin:stats")],
    ])
    await update.message.reply_text("🛠 پنل مدیریت:", reply_markup=kb)

async def wallet_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user:
        return
    uid = update.message.from_user.id
    await save_user(uid)
    if not await is_member(uid, context):
        return await force_join(update, context)

    wallet = await get_wallet(uid)
    ref_count = await count_referrals(uid)
    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref_{uid}" if BOT_USERNAME else "—"

    txt = (
        "💰 کیف پول سکه:\n\n"
        f"📮 آدرس کیف پول شما:\n`{wallet['address']}`\n\n"
        f"💎 موجودی فعلی: {wallet['balance']} سکه\n"
        f"👥 دعوت‌های موفق: {ref_count}\n"
        f"🔗 لینک دعوت اختصاصی:\n{ref_link}\n\n"
        "می‌تونی با سکه VIP بخری، سکه انتقال بدی، یا درخواست نقد کنی."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👑 خرید VIP با سکه", callback_data="wallet:buy_vip")],
        [InlineKeyboardButton("💳 انتقال سکه", callback_data="wallet:transfer_start")],
        [InlineKeyboardButton("💸 درخواست نقد کردن سکه", callback_data="wallet:withdraw_start")],
    ])

    await update.message.reply_text(txt, reply_markup=kb)


# =========================================================
# ======================= AUDIO HANDLER ===================
# =========================================================

async def check_free_user_limit(uid: int) -> tuple[bool, str | None]:
    if await is_vip(uid):
        return True, None

    limits = await get_user_limits()
    max_daily = limits["max_daily_downloads"]
    today = date.today()
    used = await get_user_daily_usage(uid, today)
    if used >= max_daily:
        return False, (
            "⛔️ سهمیهٔ دانلود امروزت تمام شده.\n"
            "فردا دوباره می‌تونی دانلود کنی.\n\n"
            "برای دانلود نامحدود و دسترسی به پلی‌لیست، VIP شو."
        )
    return True, None

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user:
        return

    uid = update.message.from_user.id
    await save_user(uid)

    if not await is_member(uid, context):
        return await force_join(update, context)

    can_dl, msg_text = await check_free_user_limit(uid)
    if not can_dl:
        return await update.message.reply_text(msg_text)

    audio = update.message.audio or update.message.document
    if not audio:
        return

    name = clean_filename(getattr(audio, "file_name", "") or "music")
    ext = guess_ext(audio)

    if update.message.audio and audio.file_size > MAX_AUDIO_DL_LIMIT:
        return await update.message.reply_text(
            "⚠️ این فایل به‌صورت *Audio* ارسال شده و حجم آن بالای 20MB است.\n"
            "لطفاً همان فایل را به‌صورت *Document* بفرست تا بتوانم پردازش کنم."
        )

    msg = await update.message.reply_text("⬇️ در حال دریافت فایل…")

    uid_job = uuid4().hex
    raw = f"{DOWNLOAD_DIR}/{uid_job}_in.{ext}"
    final = f"{DOWNLOAD_DIR}/{uid_job}_out.mp3"

    async def task():
        try:
            file = await audio.get_file()
            await file.download_to_drive(raw)

            isvip = await is_vip(uid)
            await msg.edit_text("🎧 در حال تبدیل و افزودن کاور…")
            await tag_and_cover(raw, final, name, vip=isvip)

            size = os.path.getsize(final)
            prefix = "👑 VIP Download\n" if isvip else ""
            caption = f"{prefix}🎵 {name}\n🔗 @{CHANNEL_USERNAME}"

            await msg.edit_text("📡 در حال ارسال…")

            if isvip:
    isvip = await is_vip(uid)

if isvip:            # ارسال فایل با توجه به تنظیمات VIP
            isvip = await is_vip(uid)

            # تعیین مقصد ارسال
            if isvip:
                vip_settings = await get_vip_settings(uid)
                if vip_settings.get("post_to_channel"):
                    target_chats = [uid, CHANNEL_ID]
                else:
                    target_chats = [uid]
            else:
                target_chats = [CHANNEL_ID]

            # ارسال فایل به مقصدها
            for chat in target_chats:
                with open(final, "rb") as f:
                    if size <= MAX_FILE_SIZE:
                        await context.bot.send_audio(
                            chat,
                            f,
                            filename=name + ".mp3",
                            caption=caption
                        )
                    else:
                        await context.bot.send_document(
                            chat,
                            f,
                            filename=name + ".mp3",
                            caption=caption
                        )

    


            await add_history(uid, name, "forwarded")
            await increment_user_daily_usage(uid, date.today())
            await log_analytics(uid, "download", {"type": "file"})
            await msg.edit_text("✅ فایل با موفقیت پردازش و ارسال شد.")
        except Exception as e:
            logging.error(f"Error processing audio: {e}")
            try:
                await msg.edit_text("❌ خطایی در پردازش فایل رخ داد.")
            except Exception:
                pass
        finally:
            for p in (raw, final):
                if os.path.exists(p):
                    os.remove(p)

    await queue.put(task)

# =========================================================
# ====================== CALLBACK HANDLER =================
# =========================================================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    uid = q.from_user.id

    # ================= منوی اصلی =================
    if data.startswith("menu:"):
        action = data.split(":", 1)[1]

        # 🎵 دانلود موزیک
        if action == "download":
            return await context.bot.send_message(
                uid,
                "🎵 برای دانلود، فقط لینک SoundCloud یا فایل صوتی را ارسال کن."
            )

        # 👑 VIP
        if action == "vip":
            return await vip_cmd(
                Update(update.update_id, message=q.message),
                context
                if data == "vip:post_mode":
    settings = await get_vip_settings(uid)
    current = settings["post_to_channel"]

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("فقط برای من", callback_data="vip:post_off")],
        [InlineKeyboardButton("من + کانال", callback_data="vip:post_on")],
    ])

    return await context.bot.send_message(
        uid,
        f"📤 تنظیم ارسال VIP:\n\n"
        f"وضعیت فعلی: {'ارسال در کانال فعال است' if current else 'فقط برای خودت ارسال می‌شود'}",
        reply_markup=kb
    )


if data == "vip:post_on":
    await set_vip_post_mode(uid, True)
    return await context.bot.send_message(uid, "📤 ارسال در کانال فعال شد.")

if data == "vip:post_off":
    await set_vip_post_mode(uid, False)
    return await context.bot.send_message(uid, "📥 فقط برای خودت ارسال می‌شود.")

            )

        # 💰 کیف پول
        if action == "wallet":
            return await wallet_cmd(
                Update(update.update_id, message=q.message),
                context
            )

        # 👥 دعوت دوستان
        if action == "referral":
            wallet = await get_wallet(uid)
            ref_count = await count_referrals(uid)
            ref_link = f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"

            txt = (
                "👥 سیستم دعوت دوستان:\n\n"
                f"🔗 لینک دعوت اختصاصی:\n{ref_link}\n\n"
                f"هر دعوت موفق = {INVITE_REWARD_COINS} سکه\n"
                f"دعوت‌های موفق: {ref_count}\n"
                f"موجودی فعلی: {wallet['balance']} سکه\n\n"
                "دوستانت را دعوت کن و سکه بگیر."
            )
            return await context.bot.send_message(uid, txt)

        # ⚙️ تنظیم کیفیت (فقط VIP)
        if action == "quality":
            if not await is_vip(uid):
                return await context.bot.send_message(
                    uid,
                    "⚠️ تنظیم کیفیت فقط برای کاربران VIP فعال است."
                )

            current = await get_user_quality(uid)
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🎧 بهترین", callback_data="q_best"),
                    InlineKeyboardButton("🎚 320kbps", callback_data="q_320"),
                ],
                [
                    InlineKeyboardButton("🎚 192kbps", callback_data="q_192"),
                    InlineKeyboardButton("🎚 128kbps", callback_data="q_128"),
                ]
            ])
            return await context.bot.send_message(
                uid,
                f"🎚 کیفیت فعلی: {current}\nیکی از گزینه‌ها را انتخاب کن:",
                reply_markup=kb
            )

        # 📂 تاریخچه
        if action == "history":
            rows = await get_history(uid, 10)
            if not rows:
                return await context.bot.send_message(uid, "📂 هنوز هیچ موزیکی پردازش نکردی.")
            lines = []
            for title, source, created_at in rows:
                src = source if source != "forwarded" else "فایل آپلودی"
                lines.append(f"• {title}\n  ↳ {src}")
            return await context.bot.send_message(uid, "🕘 تاریخچه:\n\n" + "\n\n".join(lines))

        # 📖 راهنما
        if action == "help":
            return await context.bot.send_message(uid, HELP_TEXT)

        return

    # ================= کیفیت =================
    if data.startswith("q_"):
        if not await is_vip(uid):
            return await q.edit_message_text("⚠️ تغییر کیفیت فقط برای VIP فعال است.")

        mapping = {
            "q_best": "best",
            "q_320": "320",
            "q_192": "192",
            "q_128": "128",
        }
        q_key = data
        if q_key in mapping:
            await set_user_quality(uid, mapping[q_key])
            return await q.edit_message_text(f"🎚 کیفیت روی {mapping[q_key]} تنظیم شد.")
        return

    # ================= بررسی عضویت =================
    if data == "check_join":
        if await is_member(uid, context):
            return await q.edit_message_text("✅ عضویت تایید شد. حالا لینک یا فایل بفرست.")
        else:
            return await q.edit_message_text("❌ هنوز عضو کانال نیستی.")

    # ================= Wallet / VIP با سکه =================
    if data.startswith("wallet:"):
        action = data.split(":", 1)[1]

        # خرید VIP با سکه
        if action == "buy_vip":
            wallet = await get_wallet(uid)
            txt = (
                "👑 خرید VIP با سکه:\n\n"
                f"موجودی فعلی: {wallet['balance']} سکه\n\n"
                f"ماهانه: {VIP_COIN_PRICES['monthly']} سکه\n"
                f"سه‌ماهه: {VIP_COIN_PRICES['quarterly']} سکه\n"
                f"سالانه: {VIP_COIN_PRICES['yearly']} سکه\n"
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("ماهانه", callback_data="wallet:buyvip_monthly")],
                [InlineKeyboardButton("سه‌ماهه", callback_data="wallet:buyvip_quarterly")],
                [InlineKeyboardButton("سالانه", callback_data="wallet:buyvip_yearly")],
            ])
            return await context.bot.send_message(uid, txt, reply_markup=kb)

        # خرید VIP پلن‌ها
        if action.startswith("buyvip_"):
            plan_key = action.split("_", 1)[1]
            price = VIP_COIN_PRICES[plan_key]
            wallet = await get_wallet(uid)

            if wallet["balance"] < price:
                return await context.bot.send_message(
                    uid,
                    "❌ موجودی کافی نیست.\nدوستانت را دعوت کن تا سکه بگیری."
                )

            new_balance = await update_wallet_balance(uid, -price)
            await add_wallet_tx(uid, None, price, "vip_purchase", {"plan": plan_key})

            days_map = {"monthly": 30, "quarterly": 90, "yearly": 365}
            await set_vip(uid, plan_key, days_map[plan_key])

            return await context.bot.send_message(
                uid,
                f"👑 VIP فعال شد!\nپلن: {plan_key}\nموجودی جدید: {new_balance} سکه"
            )

        # انتقال سکه
        if action == "transfer_start":
            wallet_flows[uid] = {"mode": "transfer_address", "data": {}}
            return await context.bot.send_message(uid, "آدرس کیف پول مقصد را ارسال کن.")

        # درخواست نقد
        if action == "withdraw_start":
            wallet_flows[uid] = {"mode": "withdraw_amount", "data": {}}
            return await context.bot.send_message(uid, "مقدار سکه برای برداشت را ارسال کن.")

        return

    # ================= ADMIN PANEL =================
    if data.startswith("admin:"):
        pass  # بدون تغییر

    # ================= Playlist callbacks =================
    # بدون تغییر


    # ================= ADMIN PANEL =================
    if data.startswith("admin:"):
        if not await is_admin(uid):
            return await q.edit_message_text("⛔️ شما به پنل مدیریت دسترسی ندارید.")
        action = data.split(":", 1)[1]

        # مدیریت VIP
        if action == "vip":
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ فعال‌سازی/تمدید VIP", callback_data="admin:vip_add")],
            ])
            return await q.edit_message_text("👑 مدیریت VIP:", reply_markup=kb)

        if action == "vip_add":
            admin_flows[uid] = {"mode": "vip_add", "data": {}}
            return await q.edit_message_text(
                "👑 فعال‌سازی/تمدید VIP\n\n"
                "آیدی عددی کاربر را ارسال کن (user_id)."
            )

        if action in ("vip_plan_monthly", "vip_plan_quarterly", "vip_plan_yearly"):
            flow = admin_flows.get(uid)
            if not flow or "data" not in flow or "target_id" not in flow["data"]:
                return await q.edit_message_text("❌ اطلاعات کاربر پیدا نشد. دوباره از ابتدا تلاش کن.")
            target_id = flow["data"]["target_id"]

            if action == "vip_plan_monthly":
                plan = "monthly"
                days = 30
            elif action == "vip_plan_quarterly":
                plan = "quarterly"
                days = 90
            else:
                plan = "yearly"
                days = 365

            await set_vip(target_id, plan, days)
            await add_payment(target_id, plan, 0)
            try:
                await context.bot.send_message(
                    target_id,
                    "👑 اشتراک VIP شما فعال شد!\n\n"
                    "از این لحظه:\n"
                    "• دانلود نامحدود\n"
                    "• دسترسی کامل به پلی‌لیست و ست\n"
                    "• کیفیت بالا\n"
                    "• ارسال مستقیم در چت خودتان\n\n"
                    "از ربات لذت ببرید."
                )
            except Exception as e:
                logging.warning(f"Could not send VIP welcome message to {target_id}: {e}")

            admin_flows.pop(uid, None)
            return await q.edit_message_text(f"✅ کاربر {target_id} با موفقیت VIP ({plan}) شد.")

        # تنظیمات محدودیت
        if action == "limits":
            limits = await get_user_limits()
            txt = (
                "⚙️ تنظیمات کاربران معمولی:\n\n"
                f"• حداکثر دانلود روزانه: {limits['max_daily_downloads']}\n"
                f"• حداکثر ترک پلی‌لیست: {limits['max_playlist_tracks']} (0 یعنی ممنوع)\n"
                f"• حداکثر کیفیت: {limits['max_quality']}kbps\n\n"
                "برای تغییر هرکدام، از گزینه‌های زیر استفاده کن."
            )
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("⬆️ افزایش دانلود/روز", callback_data="admin:limits_inc"),
                    InlineKeyboardButton("⬇️ کاهش دانلود/روز", callback_data="admin:limits_dec"),
                ],
                [
                    InlineKeyboardButton("📀 اجازه پلی‌لیست (تغییر)", callback_data="admin:limits_toggle_pl"),
                ]
            ])
            return await q.edit_message_text(txt, reply_markup=kb)

        if action == "limits_inc":
            limits = await get_user_limits()
            new_val = limits["max_daily_downloads"] + 1
            await update_user_limits({"max_daily_downloads": new_val})
            try:
                return await q.edit_message_text(f"✅ حداکثر دانلود روزانه روی {new_val} تنظیم شد.")
            except BadRequest as e:
                if "Message is not modified" in str(e):
                    return
                raise

        if action == "limits_dec":
            limits = await get_user_limits()
            new_val = max(0, limits["max_daily_downloads"] - 1)
            await update_user_limits({"max_daily_downloads": new_val})
            try:
                return await q.edit_message_text(f"✅ حداکثر دانلود روزانه روی {new_val} تنظیم شد.")
            except BadRequest as e:
                if "Message is not modified" in str(e):
                    return
                raise

        if action == "limits_toggle_pl":
            limits = await get_user_limits()
            current = limits["max_playlist_tracks"]
            new_val = 0 if current > 0 else 9999
            await update_user_limits({"max_playlist_tracks": new_val})
            state_txt = "❌ پلی‌لیست برای کاربران معمولی ممنوع شد." if new_val == 0 else "✅ پلی‌لیست برای کاربران معمولی فعال شد."
            try:
                return await q.edit_message_text(state_txt)
            except BadRequest as e:
                if "Message is not modified" in str(e):
                    return
                raise

        # سیستم تبلیغات
        if action == "ads":
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 به همه کاربران", callback_data="admin:ads_all")],
                [InlineKeyboardButton("👑 فقط VIP", callback_data="admin:ads_vip")],
                [InlineKeyboardButton("👤 فقط کاربران معمولی", callback_data="admin:ads_free")],
            ])
            return await q.edit_message_text("📢 سیستم تبلیغات:", reply_markup=kb)

        if action in ("ads_all", "ads_vip", "ads_free"):
            target = {
                "ads_all": "all",
                "ads_vip": "vip",
                "ads_free": "free",
            }[action]
            admin_flows[uid] = {"mode": "ads_text", "data": {"target": target}}
            return await q.edit_message_text(
                "📢 متن پیام تبلیغاتی را ارسال کن.\n"
                "فعلاً فقط متن پشتیبانی می‌شود."
            )

        # مدیریت ادمین‌ها
        if action == "admins":
            if not await is_owner(uid):
                return await q.edit_message_text("⛔️ فقط مالک ربات می‌تواند ادمین‌ها را مدیریت کند.")
            admins = await list_admins()
            lines = []
            for a in admins:
                role = a.get("role", "admin")
                lines.append(f"{a['user_id']} — {role}")
            txt = "🛠 مدیریت ادمین‌ها:\n\n" + ("\n".join(lines) if lines else "هنوز ادمینی ثبت نشده.")
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ اضافه کردن ادمین", callback_data="admin:admins_add")],
                [InlineKeyboardButton("➖ حذف ادمین", callback_data="admin:admins_remove")],
            ])
            return await q.edit_message_text(txt, reply_markup=kb)

        if action == "admins_add":
            if not await is_owner(uid):
                return await q.edit_message_text("⛔️ فقط مالک ربات می‌تواند ادمین اضافه کند.")
            admin_flows[uid] = {"mode": "admin_add", "data": {}}
            return await q.edit_message_text("آیدی عددی کسی که می‌خوای ادمین کنی رو بفرست.")

        if action == "admins_remove":
            if not await is_owner(uid):
                return await q.edit_message_text("⛔️ فقط مالک ربات می‌تواند ادمین حذف کند.")
            admin_flows[uid] = {"mode": "admin_remove", "data": {}}
            return await q.edit_message_text(
                "آیدی عددی ادمینی که می‌خوای حذف کنی رو بفرست.\n"
                "Owner (خودت) قابل حذف نیست."
            )

        # مدیریت کیف پول / سکه
        if action == "wallet":
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ اعطای سکه به کاربر", callback_data="admin:wallet_grant")],
            ])
            return await q.edit_message_text("💰 مدیریت کیف پول و سکه:", reply_markup=kb)

        if action == "wallet_grant":
            admin_flows[uid] = {"mode": "wallet_grant_user", "data": {}}
            return await q.edit_message_text(
                "اعطای سکه به کاربر:\n\n"
                "آیدی عددی کاربر را ارسال کن."
            )

        # آمار
        if action == "stats":
            stats = await get_basic_stats()
            txt = (
                "📊 آمار کلی:\n\n"
                f"• تعداد کل کاربران: {stats['users_count']}\n"
                f"• تعداد کاربران VIP: {stats['vip_count']}\n"
                f"• دانلودهای امروز: {stats['downloads_today']}\n"
            )
            return await q.edit_message_text(txt)

        return

    # ================= PLAYLIST CALLBACKS =================
    if data.startswith("pl_all:"):
        job_id = data.split(":", 1)[1]
        pl = pending_playlists.get(uid)
        if not pl or pl["job_id"] != job_id:
            return await q.edit_message_text("❌ اطلاعات پلی‌لیست پیدا نشد.")

        if not await is_vip(uid):
            limits = await get_user_limits()
            if limits["max_playlist_tracks"] == 0:
                return await q.edit_message_text(
                    "⛔️ دانلود پلی‌لیست فقط برای کاربران VIP فعال است.\n"
                    "برای فعال‌سازی VIP با ادمین تماس بگیر."
                )

        total = len(pl["tracks"])
        indices = list(range(total))
        pending_playlists[uid]["await_selection"] = False
        await q.edit_message_text(
            f"✅ {total} ترک انتخاب شد.\n"
            "در حال شروع دانلود و پردازش هستم…"
        )
        msg = await context.bot.send_message(
            chat_id=pl["chat_id"],
            text="🔄 در حال آماده‌سازی دانلود پلی‌لیست…"
        )
        pending_playlists[uid]["status_msg_id"] = msg.message_id
        pending_playlists[uid]["chat_id"] = msg.chat_id

        async def task():
            await process_playlist(uid, context, pending_playlists[uid], indices)

        await queue.put(task)
        return

    if data.startswith("pl_select:"):
        job_id = data.split(":", 1)[1]
        pl = pending_playlists.get(uid)
        if not pl or pl["job_id"] != job_id:
            return await q.edit_message_text("❌ اطلاعات پلی‌لیست پیدا نشد.")

        if not await is_vip(uid):
            limits = await get_user_limits()
            if limits["max_playlist_tracks"] == 0:
                return await q.edit_message_text(
                    "⛔️ انتخاب دستی و دانلود پلی‌لیست فقط برای کاربران VIP فعال است."
                )

        total = len(pl["tracks"])
        lines = []
        max_preview = min(total, 50)
        for i in range(max_preview):
            lines.append(f"{i+1}. {pl['tracks'][i]['title']}")
        if total > max_preview:
            lines.append(f"... و {total - max_preview} ترک دیگر")

        txt = (
            "🎯 انتخاب دستی ترک‌ها\n\n"
            "شماره ترک‌ها را به‌صورت زیر بفرست:\n"
            "مثال: 1,3,5-10\n\n"
            f"حداکثر: {total}\n\n"
            "لیست ترک‌ها:\n" + "\n".join(lines)
        )
        pending_playlists[uid]["await_selection"] = True
        return await q.edit_message_text(txt)

    if data.startswith("resume:"):
        job_id = data.split(":", 1)[1]
        pending = await get_pending_indices_for_job(job_id)
        if not pending:
            await finish_job(job_id)
            return await q.edit_message_text("✅ همهٔ ترک‌ها قبلاً ارسال شده‌اند.")

        await q.edit_message_text("▶️ ادامهٔ پردازش پلی‌لیست…")

        async def task():
            await process_playlist_job_resume(uid, context, job_id, pending)

        await queue.put(task)
        return

    if data.startswith("restart:"):
        job_id = data.split(":", 1)[1]
        await reset_job(job_id)
        return await q.edit_message_text("🔄 پردازش از اول شروع می‌شود. دوباره لینک را بفرست.")

# =========================================================
# ======================= TEXT HANDLER =====================
# =========================================================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user:
        return

    uid = update.message.from_user.id
    text = update.message.text or ""
    await save_user(uid)

    if not await is_member(uid, context):
        return await force_join(update, context)

    # جریان‌های چندمرحله‌ای کیف پول
    if uid in wallet_flows:
        flow = wallet_flows[uid]
        mode = flow["mode"]

        # انتقال - دریافت آدرس
        if mode == "transfer_address":
            address = text.strip()
            dest_wallet = await get_wallet_by_address(address)
            if not dest_wallet:
                return await update.message.reply_text("❌ آدرس کیف پول مقصد نامعتبر است. دوباره ارسال کن.")
            wallet_flows[uid] = {
                "mode": "transfer_amount",
                "data": {"to_user": dest_wallet["user_id"], "address": address},
            }
            return await update.message.reply_text(
                f"آدرس مقصد تایید شد (کاربر {dest_wallet['user_id']}).\n"
                "حالا تعداد سکه‌ای که می‌خواهی انتقال دهی را ارسال کن (عدد)."
            )

        # انتقال - دریافت مقدار
        if mode == "transfer_amount":
            try:
                amount = int(text.strip())
            except ValueError:
                return await update.message.reply_text("❌ مقدار نامعتبر است. فقط عدد بفرست.")
            if amount <= 0:
                return await update.message.reply_text("❌ مقدار باید بیشتر از صفر باشد.")
            to_user = flow["data"]["to_user"]
            from_wallet = await get_wallet(uid)
            if from_wallet["balance"] < amount:
                wallet_flows.pop(uid, None)
                return await update.message.reply_text("❌ موجودی سکه برای این انتقال کافی نیست.")
            new_balance_from = await update_wallet_balance(uid, -amount)
            new_balance_to = await update_wallet_balance(to_user, amount)
            await add_wallet_tx(
                from_user=uid,
                to_user=to_user,
                amount=amount,
                type="transfer",
                meta={},
            )
            wallet_flows.pop(uid, None)
            await update.message.reply_text(
                f"✅ {amount} سکه به کاربر {to_user} منتقل شد.\n"
                f"موجودی جدید تو: {new_balance_from} سکه."
            )
            try:
                await context.bot.send_message(
                    to_user,
                    f"💳 {amount} سکه از کاربر {uid} دریافت کردی.\n"
                    f"موجودی جدیدت: {new_balance_to} سکه."
                )
            except Exception:
                pass
            return

        # درخواست نقد - دریافت مقدار
        if mode == "withdraw_amount":
            try:
                amount = int(text.strip())
            except ValueError:
                return await update.message.reply_text("❌ مقدار نامعتبر است. فقط عدد بفرست.")
            if amount <= 0:
                return await update.message.reply_text("❌ مقدار باید بیشتر از صفر باشد.")
            wallet = await get_wallet(uid)
            if wallet["balance"] < amount:
                wallet_flows.pop(uid, None)
                return await update.message.reply_text("❌ موجودی سکه برای این مقدار کافی نیست.")
            # اینجا می‌تونیم فقط درخواست ثبت کنیم و موجودی را تغییر ندهیم (تو دستی مدیریت می‌کنی)
            await add_wallet_tx(
                from_user=uid,
                to_user=None,
                amount=amount,
                type="withdraw_request",
                meta={},
            )
            wallet_flows.pop(uid, None)
            await update.message.reply_text(
                "✅ درخواست برداشت ثبت شد.\n"
                "ادمین به‌زودی با تو تماس می‌گیرد."
            )
            # پیام به OWNER
            try:
                await context.bot.send_message(
                    OWNER_ID,
                    f"💸 درخواست برداشت:\n"
                    f"کاربر: {uid}\n"
                    f"مقدار: {amount} سکه"
                )
            except Exception:
                pass
            return

        wallet_flows.pop(uid, None)

    # جریان‌های ادمین
    if uid in admin_flows:
        flow = admin_flows[uid]
        mode = flow["mode"]

        if mode == "vip_add":
            try:
                target_id = int(text.strip())
            except ValueError:
                return await update.message.reply_text("آیدی نامعتبر است. دوباره بفرست.")
            admin_flows[uid] = {"mode": "vip_add_plan", "data": {"target_id": target_id}}
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("ماهانه (30 روز)", callback_data="admin:vip_plan_monthly")],
                [InlineKeyboardButton("سه‌ماهه (90 روز)", callback_data="admin:vip_plan_quarterly")],
                [InlineKeyboardButton("سالانه (365 روز)", callback_data="admin:vip_plan_yearly")],
            ])
            return await update.message.reply_text(
                f"کاربر {target_id} انتخاب شد.\n"
                "پلن VIP را انتخاب کن:",
                reply_markup=kb
            )

        if mode == "admin_add":
            if not await is_owner(uid):
                admin_flows.pop(uid, None)
                return await update.message.reply_text("⛔️ فقط مالک ربات می‌تواند ادمین اضافه کند.")
            try:
                new_admin_id = int(text.strip())
            except ValueError:
                return await update.message.reply_text("آیدی نامعتبر است. دوباره بفرست.")
            await add_admin(new_admin_id)
            admin_flows.pop(uid, None)
            return await update.message.reply_text(f"✅ {new_admin_id} به عنوان ادمین اضافه شد.")

        if mode == "admin_remove":
            if not await is_owner(uid):
                admin_flows.pop(uid, None)
                return await update.message.reply_text("⛔️ فقط مالک ربات می‌تواند ادمین حذف کند.")
            try:
                rm_admin_id = int(text.strip())
            except ValueError:
                return await update.message.reply_text("آیدی نامعتبر است. دوباره بفرست.")
            if rm_admin_id == OWNER_ID:
                admin_flows.pop(uid, None)
                return await update.message.reply_text("⛔️ نمی‌تونی Owner رو حذف کنی.")
            await remove_admin(rm_admin_id)
            admin_flows.pop(uid, None)
            return await update.message.reply_text(f"✅ ادمین {rm_admin_id} حذف شد.")

        if mode == "ads_text":
            target = flow["data"]["target"]
            admin_flows.pop(uid, None)
            await update.message.reply_text("📢 در حال ارسال پیام به کاربران…")
            await broadcast_message(context, text, target)
            return

        if mode == "wallet_grant_user":
            try:
                target_id = int(text.strip())
            except ValueError:
                return await update.message.reply_text("آیدی نامعتبر است. دوباره بفرست.")
            admin_flows[uid] = {"mode": "wallet_grant_amount", "data": {"target_id": target_id}}
            return await update.message.reply_text(
                f"کاربر {target_id} انتخاب شد.\n"
                "تعداد سکه‌ای که می‌خواهی به او بدهی را ارسال کن."
            )

        if mode == "wallet_grant_amount":
            try:
                amount = int(text.strip())
            except ValueError:
                return await update.message.reply_text("مقدار نامعتبر است. فقط عدد بفرست.")
            if amount <= 0:
                return await update.message.reply_text("مقدار باید بیشتر از صفر باشد.")
            target_id = flow["data"]["target_id"]
            new_balance = await update_wallet_balance(target_id, amount)
            await add_wallet_tx(
                from_user=None,
                to_user=target_id,
                amount=amount,
                type="admin_grant",
                meta={"by": uid},
            )
            admin_flows.pop(uid, None)
            await update.message.reply_text(
                f"✅ {amount} سکه به کاربر {target_id} داده شد.\n"
                f"موجودی جدید او: {new_balance} سکه."
            )
            try:
                await context.bot.send_message(
                    target_id,
                    f"🎁 {amount} سکه از سمت ادمین دریافت کردی!\n"
                    f"موجودی جدیدت: {new_balance} سکه."
                )
            except Exception:
                pass
            return

        admin_flows.pop(uid, None)

    # حالت انتخاب دستی پلی‌لیست
    if uid in pending_playlists and pending_playlists[uid].get("await_selection"):
        pl = pending_playlists[uid]
        total = len(pl["tracks"])
        indices = parse_selection(text, total)
        if not indices:
            return await update.message.reply_text(
                "⚠️ ورودی نامعتبر بود.\n"
                f"لطفاً مثل این مثال بفرست: 1,3,5-10 (حداکثر {total})"
            )

        pending_playlists[uid]["await_selection"] = False
        await update.message.reply_text(
            f"✅ {len(indices)} ترک انتخاب شد.\n"
            "در حال شروع دانلود و پردازش هستم…"
        )
        msg = await update.message.reply_text("🔄 در حال آماده‌سازی دانلود پلی‌لیست…")
        pending_playlists[uid]["status_msg_id"] = msg.message_id
        pending_playlists[uid]["chat_id"] = msg.chat_id

        async def task():
            await process_playlist(uid, context, pending_playlists[uid], indices)

        await queue.put(task)
        return

    # لینک SoundCloud
    m = SC_REGEX.search(text)
    if not m:
        return await update.message.reply_text("⚠️ فقط لینک‌های SoundCloud پشتیبانی می‌شوند.")

    raw_url = m.group(0)
    url = await resolve_soundcloud_url(raw_url)
    user_quality = await get_user_quality(uid)
    isvip = await is_vip(uid)

    info_msg = await update.message.reply_text("🔍 در حال تحلیل لینک SoundCloud…")

    existing = await get_incomplete_job(uid, url)
    if existing:
        job_id, pl_title, total_tracks = existing
        pending = await get_pending_indices_for_job(job_id)
        done = total_tracks - len(pending)

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"▶️ ادامه از ترک {done+1}", callback_data=f"resume:{job_id}"),
                InlineKeyboardButton("🔄 شروع از اول", callback_data=f"restart:{job_id}")
            ]
        ])
        return await info_msg.edit_text(
            f"⏸ یک پردازش ناتمام برای این پلی‌لیست وجود دارد.\n\n"
            f"📀 {pl_title}\n"
            f"✔️ انجام‌شده: {done}/{total_tracks}\n\n"
            "می‌خوای ادامه بدم یا از اول شروع کنم؟",
            reply_markup=kb
        )

    # تحلیل yt-dlp
    try:
        json_raw = os.popen(f'yt-dlp -J "{url}"').read()
        data = json.loads(json_raw)
    except Exception as e:
        logging.error(f"yt-dlp -J error: {e}")
        return await info_msg.edit_text("❌ خطا در تحلیل لینک SoundCloud.")

    tracks = []
    playlist_title = data.get("title") or "SoundCloud"

    entries = data.get("entries")
    if entries and len(entries) > 1:
        is_playlist = True
        for entry in entries:
            t_title = entry.get("title") or "Track"
            t_url = entry.get("webpage_url") or entry.get("url") or url
            tracks.append({"title": t_title, "url": t_url})
    else:
        is_playlist = False
        t_title = data.get("title") or "Track"
        tracks.append({"title": t_title, "url": url})

    total = len(tracks)
    logging.info(f"[SC] User {uid} - is_playlist={is_playlist}, total_tracks={total}")

    # تک‌ترک
    if not is_playlist:
        can_dl, msg_text = await check_free_user_limit(uid)
        if not can_dl:
            return await info_msg.edit_text(msg_text)

        track = tracks[0]
        title = clean_filename(track["title"])
        fmt = get_format_for_quality(user_quality, vip=isvip)

        uid_job = uuid4().hex
        raw_path = f"{DOWNLOAD_DIR}/{uid_job}_in.raw"
        final_path = f"{DOWNLOAD_DIR}/{uid_job}_out.mp3"

        await info_msg.edit_text("⬇️ در حال دانلود از SoundCloud…")

        try:
            await run_cmd("yt-dlp", "-f", fmt, "-o", raw_path, url)
        except Exception as e:
            logging.error(f"[Single] Download error: {e}")
            return await info_msg.edit_text("❌ خطا در دانلود ترک از SoundCloud.")

        await info_msg.edit_text("🎧 در حال تبدیل و افزودن کاور…")

        try:
            await tag_and_cover(raw_path, final_path, title, vip=isvip)
        except Exception as e:
            logging.error(f"[Single] tag_and_cover error: {e}")
            return await info_msg.edit_text("❌ خطا در تبدیل فایل.")
        finally:
            if os.path.exists(raw_path):
                try:
                    os.remove(raw_path)
                except Exception:
                    pass

        size = os.path.getsize(final_path)
        prefix = "👑 VIP Download\n" if isvip else ""
        caption = f"{prefix}🎵 {title}\n🔗 @{CHANNEL_USERNAME}"

        await info_msg.edit_text("📡 در حال ارسال…")

        for chat in target_chats:
    with open(final_path, "rb") as f:
        if size <= MAX_FILE_SIZE:
            await context.bot.send_audio(chat, f, filename=title + ".mp3", caption=caption)
        else:
            await context.bot.send_document(chat, f, filename=title + ".mp3", caption=caption)


        try:
            with open(final_path, "rb") as f:
                if size <= MAX_FILE_SIZE:
                    await context.bot.send_audio(target_chat, f, filename=title + ".mp3", caption=caption)
                else:
                    await context.bot.send_document(target_chat, f, filename=title + ".mp3", caption=caption)

            await add_history(uid, title, "SoundCloud")
            await increment_user_daily_usage(uid, date.today())
            await log_analytics(uid, "download", {"type": "single"})
            await info_msg.edit_text("✅ ترک با موفقیت دانلود و ارسال شد.")
        except Exception as e:
            logging.error(f"[Single] Send error: {e}")
            await info_msg.edit_text("❌ خطایی در ارسال فایل رخ داد.")
        finally:
            if os.path.exists(final_path):
                try:
                    os.remove(final_path)
                except Exception:
                    pass

        return

    # پلی‌لیست
    if is_playlist and not isvip:
        limits = await get_user_limits()
        if limits["max_playlist_tracks"] == 0:
            return await info_msg.edit_text(
                "⛔️ دانلود پلی‌لیست فقط برای کاربران VIP فعال است.\n"
                "برای فعال‌سازی VIP با ادمین تماس بگیر."
            )

    await log_analytics(uid, "playlist", {"total": total})

    job_id = uuid4().hex
    await create_job(job_id, uid, playlist_title, url, total)
    await create_job_tracks(job_id, tracks)

    lines = []
    max_preview = min(total, 50)
    for i in range(max_preview):
        lines.append(f"{i+1}. {tracks[i]['title']}")
    if total > max_preview:
        lines.append(f"... و {total - max_preview} ترک دیگر")

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📥 دانلود همه", callback_data=f"pl_all:{job_id}"),
            InlineKeyboardButton("🎯 انتخاب دستی", callback_data=f"pl_select:{job_id}")
        ]
    ])

    await info_msg.edit_text(
        f"📀 نام پلی‌لیست / ست: {playlist_title}\n"
        f"🎧 تعداد ترک‌ها: {total}\n"
        f"🎚 کیفیت انتخابی: {user_quality}\n\n"
        "🎵 لیست ترک‌ها:\n" +
        "\n".join(lines),
        reply_markup=kb
    )

    pending_playlists[uid] = {
        "job_id": job_id,
        "url": url,
        "playlist_title": playlist_title,
        "tracks": tracks,
        "quality": user_quality,
        "await_selection": False,
        "status_msg_id": None,
        "chat_id": update.message.chat_id,
    }

# =========================================================
# ================= PLAYLIST PROCESSING ===================
# =========================================================

async def process_playlist(uid: int, context: ContextTypes.DEFAULT_TYPE, pl: dict, indices):
    job_id = pl["job_id"]
    playlist_title = pl["playlist_title"]
    tracks = pl["tracks"]
    quality = pl["quality"]
    total = len(indices)
    status_msg_id = pl["status_msg_id"]
    chat_id = pl["chat_id"]

    isvip = await is_vip(uid)
    fmt = get_format_for_quality(quality, vip=isvip)
    playlist_hashtag = make_playlist_hashtag(playlist_title)

    logging.info(f"[Playlist] Start job {job_id} for user {uid}: {total} tracks.")

    downloaded = 0
    sent = 0

    async def update_status(current_idx=None, phase="", current_title=""):
        prefix = "👑 VIP Playlist\n\n" if isvip else ""
        text = (
            prefix +
            f"📀 پلی‌لیست: {playlist_title}\n"
            f"{playlist_hashtag}  #playlist\n\n"
            f"🎧 تعداد انتخاب‌شده: {total}\n"
            f"⬇️ دانلود شده: {downloaded}/{total}\n"
            f"📡 ارسال شده: {sent}/{total}\n"
        )
        if current_idx is not None:
            text += f"\n🔄 ترک فعلی: {current_idx+1}/{total}\n"
        if phase:
            text += f"📍 مرحله: {phase}\n"
        if current_title:
            text += f"🎵 {current_title}"
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg_id,
                text=text
            )
        except Exception as e:
            logging.warning(f"Status update failed: {e}")

    try:
        for pos, idx in enumerate(indices):
            track = tracks[idx]
            title = clean_filename(track["title"])
            t_url = track["url"]

            logging.info(f"[Playlist] ({pos+1}/{total}) Downloading: {title}")
            await update_status(pos, "دانلود از SoundCloud", title)

            uid_job = f"{job_id}_{idx}"
            raw = f"{DOWNLOAD_DIR}/{uid_job}_in.raw"
            final = f"{DOWNLOAD_DIR}/{uid_job}_out.mp3"

            try:
                await run_cmd("yt-dlp", "-f", fmt, "-o", raw, t_url)
            except Exception as e:
                logging.error(f"[Playlist] Download error for {title}: {e}")
                continue

            downloaded += 1
            await update_status(pos, "تبدیل و افزودن کاور", title)
            logging.info(f"[Playlist] ({pos+1}/{total}) Converting: {title}")

            try:
                await tag_and_cover(raw, final, title, vip=isvip)
            except Exception as e:
                logging.error(f"[Playlist] tag_and_cover error for {title}: {e}")
                continue
            finally:
                if os.path.exists(raw):
                    try:
                        os.remove(raw)
                    except Exception:
                        pass

            size = os.path.getsize(final)
            prefix = "👑 VIP Download\n" if isvip else ""
            caption = (
                f"{prefix}{playlist_hashtag}\n"
                f"#playlist\n"
                f"📀 {playlist_title}\n"
                f"🎵 {title}\n"
                f"🔗 @{CHANNEL_USERNAME}"
            )

            await update_status(pos, "ارسال", title)
            logging.info(f"[Playlist] ({pos+1}/{total}) Sending: {title}")

            target_chat = uid if isvip else CHANNEL_ID

            with open(final, "rb") as f:
                try:
                    if size <= MAX_FILE_SIZE:
                        await context.bot.send_audio(target_chat, f, filename=title + ".mp3", caption=caption)
                    else:
                        await context.bot.send_document(target_chat, f, filename=title + ".mp3", caption=caption)
                    sent += 1
                    await add_history(uid, title, playlist_title)
                    await mark_track_sent(job_id, idx)
                    await increment_user_daily_usage(uid, date.today())
                    await log_analytics(uid, "download", {"type": "playlist_track"})
                except Exception as e:
                    logging.error(f"[Playlist] Send error for {title}: {e}")
                finally:
                    try:
                        if os.path.exists(final):
                            os.remove(final)
                    except Exception:
                        pass

            await update_status(pos, "اتمام ترک فعلی", title)

        await finish_job(job_id)
        await update_status(None, "تمام شد", "")
        logging.info(f"[Playlist] Job {job_id} finished. Sent {sent}/{total} tracks.")
    except Exception as e:
        logging.error(f"[Playlist] Fatal error in process_playlist: {e}")
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_msg_id,
                text="❌ خطایی در پردازش پلی‌لیست رخ داد."
            )
        except Exception:
            pass
    finally:
        if uid in pending_playlists:
            del pending_playlists[uid]

# =========================================================
# =============== PLAYLIST RESUME PROCESSING ==============
# =========================================================

async def process_playlist_job_resume(uid: int, context: ContextTypes.DEFAULT_TYPE, job_id: str, pending_indices_with_titles):
    rows = await db.select("jobs", {"job_id": job_id}, limit=1)
    if not rows:
        return
    row = rows[0]
    playlist_title = row["playlist_title"]
    url = row["source_url"]
    playlist_hashtag = make_playlist_hashtag(playlist_title)

    chat_id = uid
    isvip = await is_vip(uid)
    prefix = "👑 VIP Playlist Resume\n\n" if isvip else ""
    msg = await context.bot.send_message(chat_id, f"{prefix}🔄 ادامهٔ پردازش پلی‌لیست…")

    quality = await get_user_quality(uid)
    fmt = get_format_for_quality(quality, vip=isvip)

    json_raw = os.popen(f'yt-dlp -J "{url}"').read()
    data = json.loads(json_raw)
    all_tracks = []
    entries = data.get("entries")
    if entries:
        for entry in entries:
            t_title = entry.get("title") or "Track"
            t_url = entry.get("webpage_url") or entry.get("url") or url
            all_tracks.append({"title": t_title, "url": t_url})
    else:
        t_title = data.get("title") or "Track"
        all_tracks.append({"title": t_title, "url": url})

    total_pending = len(pending_indices_with_titles)
    sent = 0

    for i, (idx, title_from_db) in enumerate(pending_indices_with_titles):
        if idx >= len(all_tracks):
            continue
        track = all_tracks[idx]
        title = clean_filename(track["title"])
        t_url = track["url"]

        await msg.edit_text(
            f"{prefix}▶️ ادامهٔ پردازش پلی‌لیست\n\n"
            f"📀 {playlist_title}\n"
            f"{playlist_hashtag} #playlist\n\n"
            f"🔄 ترک {i+1}/{total_pending}\n"
            f"🎵 {title}\n"
            f"📡 در حال دانلود…"
        )

        uid_job = f"{job_id}_{idx}"
        raw = f"{DOWNLOAD_DIR}/{uid_job}_in.raw"
        final = f"{DOWNLOAD_DIR}/{uid_job}_out.mp3"

        try:
            await run_cmd("yt-dlp", "-f", fmt, "-o", raw, t_url)
            await msg.edit_text(
                f"{prefix}▶️ ادامهٔ پردازش پلی‌لیست\n\n"
                f"📀 {playlist_title}\n"
                f"{playlist_hashtag} #playlist\n\n"
                f"🎵 {title}\n"
                f"🎧 در حال تبدیل و افزودن کاور…"
            )
            await tag_and_cover(raw, final, title, vip=isvip)

            caption_prefix = "👑 VIP Download\n" if isvip else ""
            caption = (
                f"{caption_prefix}{playlist_hashtag}\n"
                f"#playlist\n"
                f"📀 {playlist_title}\n"
                f"🎵 {title}\n"
                f"🔗 @{CHANNEL_USERNAME}"
            )
            size = os.path.getsize(final)

            target_chat = uid if isvip else CHANNEL_ID

            with open(final, "rb") as f:
                if size <= MAX_FILE_SIZE:
                    await context.bot.send_audio(target_chat, f, filename=title + ".mp3", caption=caption)
                else:
                    await context.bot.send_document(target_chat, f, filename=title + ".mp3", caption=caption)

            await mark_track_sent(job_id, idx)
            await add_history(uid, title, playlist_title)
            await increment_user_daily_usage(uid, date.today())
            await log_analytics(uid, "download", {"type": "playlist_resume"})
            sent += 1
        except Exception as e:
            logging.error(f"[Resume] Error for track {title}: {e}")
        finally:
            for p in (raw, final):
                if os.path.exists(p):
                    os.remove(p)

    await finish_job(job_id)
    await msg.edit_text(
        f"{prefix}✅ ادامهٔ پردازش پلی‌لیست با موفقیت انجام شد.\n"
        f"📀 {playlist_title}\n"
        f"🎧 {sent}/{total_pending} ترک باقی‌مانده ارسال شد."
    )
    logging.info(f"[Resume] Job {job_id} resume finished. Sent {sent}/{total_pending} tracks.")

# =========================================================
# =================== BROADCAST (ADS) =====================
# =========================================================

async def get_all_user_ids():
    rows = await db.select("users")
    return [r["user_id"] for r in rows]

async def get_all_vip_user_ids():
    rows = await db.select("vip_users")
    return [r["user_id"] for r in rows]

async def broadcast_message(context: ContextTypes.DEFAULT_TYPE, text: str, target: str):
    all_ids = await get_all_user_ids()
    vip_ids = set(await get_all_vip_user_ids())

    if target == "all":
        ids = all_ids
    elif target == "vip":
        ids = [uid for uid in all_ids if uid in vip_ids]
        text = "👑 پیام مخصوص VIP:\n\n" + text
    else:
        ids = [uid for uid in all_ids if uid not in vip_ids]
        text = "👤 پیام مخصوص کاربران معمولی:\n\n" + text

    success = 0
    fail = 0
    for u in ids:
        try:
            await context.bot.send_message(u, text)
            success += 1
            await log_analytics(u, "broadcast_received", {"target": target})
        except Exception:
            fail += 1
        await asyncio.sleep(0.1)

    logging.info(f"Broadcast done: target={target}, success={success}, fail={fail}")

# =========================================================
# ============================ MAIN ========================
# =========================================================

async def post_init(app: Application):
    await start_workers(app)
    await ensure_owner_admin()
    logging.info("Post-init done (workers + owner admin).")

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("history", history_cmd))
    app.add_handler(CommandHandler("quality", quality_cmd))
    app.add_handler(CommandHandler("vip", vip_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("wallet", wallet_cmd))

    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.AUDIO | filters.Document.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.post_init = post_init

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        webhook_url=BASE_URL
    )

if __name__ == "__main__":
    main()
