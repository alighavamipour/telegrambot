# =========================================================
# bot.py — SoundCloud Bot (Supabase REST API + Full Features)
# =========================================================

import os
import re
import json
import httpx
import logging
import asyncio
from uuid import uuid4
from datetime import datetime

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

# ================= ENV & CONSTANTS =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME")
CHANNEL_ID = os.getenv("CHANNEL_ID")
BASE_URL = os.getenv("BASE_URL")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

DOWNLOAD_DIR = "downloads"
COVER_PATH = "cover.jpg"

MAX_AUDIO_DL_LIMIT = 20 * 1024 * 1024
MAX_FILE_SIZE = 50 * 1024 * 1024

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# =========================================================
# ===============  SUPABASE REST API CLIENT  ==============
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
        # data می‌تونه dict یا list[dict] باشه
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
        # اگر وجود داشته باشد، خطا را نادیده می‌گیریم
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
    # برگرداندن به فرم (title, source, created_at)
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
    # برگرداندن به فرم (index, title) برای Resume
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

async def tag_and_cover(src: str, dst: str, title: str):
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
        "-metadata", f"comment=@{CHANNEL_USERNAME}",
        dst,
    )

async def resolve_soundcloud_url(url: str) -> str:
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
            r = await client.get(url)
            return str(r.url)
    except Exception:
        return url

def get_format_for_quality(q: str) -> str:
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
        "برای استفاده از ربات باید عضو کانال شوید:",
        reply_markup=kb
    )

# =========================================================
# ====================== GLOBAL STATE =====================
# =========================================================

SC_REGEX = re.compile(r"https?://(?:on\.)?soundcloud\.com/[^\s]+")
pending_playlists = {}  # uid -> {...}

# =========================================================
# ========================= COMMANDS ======================
# =========================================================

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user:
        return

    uid = update.message.from_user.id
    await save_user(uid)
    if not await is_member(uid, context):
        return await force_join(update, context)

    await update.message.reply_text(
        "🎵 خوش آمدی.\n"
        "فایل موسیقی یا لینک SoundCloud ارسال کن.\n"
        "برای دیدن تاریخچه: /history\n"
        "برای انتخاب کیفیت SoundCloud: /quality"
    )

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

# =========================================================
# ======================= AUDIO HANDLER ===================
# =========================================================

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user:
        return

    uid = update.message.from_user.id
    await save_user(uid)

    if not await is_member(uid, context):
        return await force_join(update, context)

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

            await msg.edit_text("🎧 در حال تبدیل و افزودن کاور…")
            await tag_and_cover(raw, final, name)

            size = os.path.getsize(final)
            caption = f"🎵 {name}\n🔗 @{CHANNEL_USERNAME}"

            await msg.edit_text("📡 در حال ارسال به کانال…")

            with open(final, "rb") as f:
                if size <= MAX_FILE_SIZE:
                    await context.bot.send_audio(CHANNEL_ID, f, filename=name + ".mp3", caption=caption)
                else:
                    await context.bot.send_document(CHANNEL_ID, f, filename=name + ".mp3", caption=caption)

            await add_history(uid, name, "forwarded")
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

    # کیفیت
    if data.startswith("q_"):
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

    # بررسی عضویت
    if data == "check_join":
        if await is_member(uid, context):
            return await q.edit_message_text("✅ عضویت شما تایید شد. حالا فایل یا لینک بفرست.")
        else:
            return await q.edit_message_text("❌ هنوز عضو کانال نیستی.")

    # دانلود همه
    if data.startswith("pl_all:"):
        job_id = data.split(":", 1)[1]
        pl = pending_playlists.get(uid)
        if not pl or pl["job_id"] != job_id:
            return await q.edit_message_text("❌ اطلاعات پلی‌لیست پیدا نشد.")

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

    # انتخاب دستی
    if data.startswith("pl_select:"):
        job_id = data.split(":", 1)[1]
        pl = pending_playlists.get(uid)
        if not pl or pl["job_id"] != job_id:
            return await q.edit_message_text("❌ اطلاعات پلی‌لیست پیدا نشد.")

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

    # Resume
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

    # Restart
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

    info_msg = await update.message.reply_text("🔍 در حال تحلیل لینک SoundCloud…")

    # Job ناتمام؟
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

    # تحلیل اولیه پلی‌لیست/ست/تک ترک
    try:
        # مثل نسخهٔ قبلی: yt-dlp -J
        json_raw = os.popen(f'yt-dlp -J "{url}"').read()
        data = json.loads(json_raw)
    except Exception as e:
        logging.error(f"yt-dlp -J error: {e}")
        return await info_msg.edit_text("❌ خطا در تحلیل لینک SoundCloud.")

    tracks = []
    playlist_title = data.get("title") or "SoundCloud"
    if "entries" in data and data["entries"]:
        for entry in data["entries"]:
            t_title = entry.get("title") or "Track"
            t_url = entry.get("webpage_url") or entry.get("url") or url
            tracks.append({"title": t_title, "url": t_url})
    else:
        t_title = data.get("title") or "Track"
        tracks.append({"title": t_title, "url": url})

    total = len(tracks)
    logging.info(f"[Playlist] User {uid} - {total} tracks detected from SoundCloud.")

    # Job جدید برای Resume با Supabase
    job_id = uuid4().hex
    await create_job(job_id, uid, playlist_title, url, total)
    await create_job_tracks(job_id, tracks)

    # پیش‌نمایش ترک‌ها
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

    fmt = get_format_for_quality(quality)
    playlist_hashtag = make_playlist_hashtag(playlist_title)

    logging.info(f"[Playlist] Start job {job_id} for user {uid}: {total} tracks.")

    downloaded = 0
    sent = 0

    async def update_status(current_idx=None, phase="", current_title=""):
        text = (
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
                await tag_and_cover(raw, final, title)
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
            caption = (
                f"{playlist_hashtag}\n"
                f"#playlist\n"
                f"📀 {playlist_title}\n"
                f"🎵 {title}\n"
                f"🔗 @{CHANNEL_USERNAME}"
            )

            await update_status(pos, "ارسال به کانال", title)
            logging.info(f"[Playlist] ({pos+1}/{total}) Sending: {title}")

            with open(final, "rb") as f:
                try:
                    if size <= MAX_FILE_SIZE:
                        await context.bot.send_audio(CHANNEL_ID, f, filename=title + ".mp3", caption=caption)
                    else:
                        await context.bot.send_document(CHANNEL_ID, f, filename=title + ".mp3", caption=caption)
                    sent += 1
                    await add_history(uid, title, playlist_title)
                    await mark_track_sent(job_id, idx)
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
    # گرفتن اطلاعات job از Supabase
    rows = await db.select("jobs", {"job_id": job_id}, limit=1)
    if not rows:
        return
    row = rows[0]
    playlist_title = row["playlist_title"]
    url = row["source_url"]
    playlist_hashtag = make_playlist_hashtag(playlist_title)

    chat_id = uid
    msg = await context.bot.send_message(chat_id, "🔄 ادامهٔ پردازش پلی‌لیست…")

    quality = await get_user_quality(uid)
    fmt = get_format_for_quality(quality)

    # دوباره info کامل پلی‌لیست را می‌گیریم تا URLهای تکی ترک‌ها را داشته باشیم
    json_raw = os.popen(f'yt-dlp -J "{url}"').read()
    data = json.loads(json_raw)
    all_tracks = []
    if "entries" in data and data["entries"]:
        for entry in data["entries"]:
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
            f"▶️ ادامهٔ پردازش پلی‌لیست\n\n"
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
                f"▶️ ادامهٔ پردازش پلی‌لیست\n\n"
                f"📀 {playlist_title}\n"
                f"{playlist_hashtag} #playlist\n\n"
                f"🎵 {title}\n"
                f"🎧 در حال تبدیل و افزودن کاور…"
            )
            await tag_and_cover(raw, final, title)

            caption = (
                f"{playlist_hashtag}\n"
                f"#playlist\n"
                f"📀 {playlist_title}\n"
                f"🎵 {title}\n"
                f"🔗 @{CHANNEL_USERNAME}"
            )
            size = os.path.getsize(final)

            with open(final, "rb") as f:
                if size <= MAX_FILE_SIZE:
                    await context.bot.send_audio(CHANNEL_ID, f, filename=title + ".mp3", caption=caption)
                else:
                    await context.bot.send_document(CHANNEL_ID, f, filename=title + ".mp3", caption=caption)

            await mark_track_sent(job_id, idx)
            await add_history(uid, title, playlist_title)
            sent += 1
        except Exception as e:
            logging.error(f"[Resume] Error for track {title}: {e}")
        finally:
            for p in (raw, final):
                if os.path.exists(p):
                    os.remove(p)

    await finish_job(job_id)
    await msg.edit_text(
        f"✅ ادامهٔ پردازش پلی‌لیست با موفقیت انجام شد.\n"
        f"📀 {playlist_title}\n"
        f"🎧 {sent}/{total_pending} ترک باقی‌مانده ارسال شد."
    )
    logging.info(f"[Resume] Job {job_id} resume finished. Sent {sent}/{total_pending} tracks.")

# =========================================================
# ============================ MAIN ========================
# =========================================================

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("history", history_cmd))
    app.add_handler(CommandHandler("quality", quality_cmd))

    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.AUDIO | filters.Document.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.post_init = start_workers

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        webhook_url=BASE_URL
    )

if __name__ == "__main__":
    main()
