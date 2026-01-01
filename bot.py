# =========================================================
# bot.py - SOUNDLOUD PRO BOT (PLAYLIST + SET + QUALITY + HISTORY + RESUME)
# =========================================================

import os
import re
import sqlite3
import logging
import asyncio
import requests
import json
from uuid import uuid4
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ================= ENV =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME")
CHANNEL_ID = os.getenv("CHANNEL_ID")
BASE_URL = os.getenv("BASE_URL")

DOWNLOAD_DIR = "downloads"
COVER_PATH = "cover.jpg"

MAX_AUDIO_DL_LIMIT = 20 * 1024 * 1024   # محدودیت دانلود Audio در Telegram
MAX_FILE_SIZE = 50 * 1024 * 1024        # محدودیت sendAudio (sendDocument تا 2GB اوکی است)

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ================= LOGGING =================
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ================= DATABASE =================
conn = sqlite3.connect("users.db", check_same_thread=False)
cur = conn.cursor()

cur.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)")

cur.execute("""
    CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        title TEXT,
        source TEXT,
        created_at TEXT
    )
""")

cur.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        user_id INTEGER PRIMARY KEY,
        quality TEXT
    )
""")

# برای Resume پلی‌لیست / ست
cur.execute("""
    CREATE TABLE IF NOT EXISTS jobs (
        job_id TEXT PRIMARY KEY,
        user_id INTEGER,
        playlist_title TEXT,
        source_url TEXT,
        total_tracks INTEGER,
        status TEXT,
        created_at TEXT,
        updated_at TEXT
    )
""")

cur.execute("""
    CREATE TABLE IF NOT EXISTS job_tracks (
        job_id TEXT,
        track_index INTEGER,
        title TEXT,
        status TEXT,
        PRIMARY KEY (job_id, track_index)
    )
""")

conn.commit()

# ================= BASIC DB HELPERS =================
def save_user(uid: int):
    cur.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))
    conn.commit()

def set_user_quality(uid: int, quality: str):
    cur.execute(
        "INSERT INTO settings (user_id, quality) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET quality=excluded.quality",
        (uid, quality),
    )
    conn.commit()

def get_user_quality(uid: int) -> str:
    cur.execute("SELECT quality FROM settings WHERE user_id=?", (uid,))
    row = cur.fetchone()
    return row[0] if row and row[0] else "best"

def add_history(uid: int, title: str, source: str):
    cur.execute(
        "INSERT INTO history (user_id, title, source, created_at) VALUES (?, ?, ?, ?)",
        (uid, title, source, datetime.utcnow().isoformat()),
    )
    conn.commit()

def get_history(uid: int, limit: int = 10):
    cur.execute(
        "SELECT title, source, created_at FROM history WHERE user_id=? ORDER BY id DESC LIMIT ?",
        (uid, limit),
    )
    return cur.fetchall()

# ================= RESUME HELPERS =================
def create_job(job_id, user_id, playlist_title, url, total_tracks):
    cur.execute("""
        INSERT OR REPLACE INTO jobs 
        (job_id, user_id, playlist_title, source_url, total_tracks, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'running', datetime('now'), datetime('now'))
    """, (job_id, user_id, playlist_title, url, total_tracks))
    conn.commit()

def create_job_tracks(job_id, tracks):
    for idx, t in enumerate(tracks):
        cur.execute("""
            INSERT OR REPLACE INTO job_tracks (job_id, track_index, title, status)
            VALUES (?, ?, ?, COALESCE(
                (SELECT status FROM job_tracks WHERE job_id=? AND track_index=?),
                'pending'
            ))
        """, (job_id, idx, t["title"], job_id, idx))
    conn.commit()

def get_incomplete_job(user_id, url):
    cur.execute("""
        SELECT job_id, playlist_title, total_tracks 
        FROM jobs 
        WHERE user_id=? AND source_url=? AND status='running'
    """, (user_id, url))
    return cur.fetchone()

def get_pending_indices_for_job(job_id):
    cur.execute("""
        SELECT track_index, title 
        FROM job_tracks 
        WHERE job_id=? AND status!='sent'
        ORDER BY track_index ASC
    """, (job_id,))
    rows = cur.fetchall()
    return [(r[0], r[1]) for r in rows]

def mark_track_sent(job_id, index):
    cur.execute("""
        UPDATE job_tracks 
        SET status='sent' 
        WHERE job_id=? AND track_index=?
    """, (job_id, index))
    cur.execute("""
        UPDATE jobs 
        SET updated_at=datetime('now')
        WHERE job_id=?
    """, (job_id,))
    conn.commit()

def finish_job(job_id):
    cur.execute("""
        UPDATE jobs 
        SET status='finished', updated_at=datetime('now')
        WHERE job_id=?
    """, (job_id,))
    conn.commit()

def reset_job(job_id):
    cur.execute("DELETE FROM job_tracks WHERE job_id=?", (job_id,))
    cur.execute("DELETE FROM jobs WHERE job_id=?", (job_id,))
    conn.commit()

# ================= UTILS =================
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
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise Exception(stderr.decode() or stdout.decode())

async def tag_and_cover(src: str, dst: str, title: str):
    await run_cmd(
        "ffmpeg", "-y",
        "-i", src,
        "-i", COVER_PATH,
        "-map", "0:a:0", "-map", "1:v:0",
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
        dst
    )

def resolve_soundcloud_url(url: str) -> str:
    try:
        r = requests.get(url, allow_redirects=True, timeout=10)
        final_url = r.url
        logging.info(f"[SoundCloud Redirect] {url}  -->  {final_url}")
        return final_url
    except Exception as e:
        logging.warning(f"resolve_soundcloud_url failed: {e}")
        return url

def get_format_for_quality(q: str) -> str:
    if q == "128":
        return "bestaudio[abr<=128]/bestaudio"
    if q == "192":
        return "bestaudio[abr<=192]/bestaudio"
    if q == "320":
        return "bestaudio[abr>=256]/bestaudio[abr>=192]/bestaudio"
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
            except:
                continue
        else:
            if not p:
                continue
            try:
                i = int(p)
                if 1 <= i <= max_n:
                    result.add(i - 1)
            except:
                continue
    return sorted(result)

# ================= QUEUE =================
queue: asyncio.Queue = asyncio.Queue()
CONCURRENCY = 2  # کمی کمتر برای فشار کمتر روی Render

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

async def start_workers(app: Application):
    for _ in range(CONCURRENCY):
        asyncio.create_task(worker())
    logging.info("Workers started.")

# ================= FORCE JOIN =================
async def is_member(uid: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        m = await context.bot.get_chat_member(CHANNEL_ID, uid)
        return m.status in ("member", "administrator", "creator")
    except:
        return False

async def force_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔔 عضویت در کانال", url=f"https://t.me/{CHANNEL_USERNAME}")],
        [InlineKeyboardButton("✅ بررسی عضویت", callback_data="check_join")]
    ])
    if update.message:
        await update.message.reply_text(
            "🔔 برای استفاده از ربات ابتدا عضو کانال شوید.",
            reply_markup=kb
        )

# ================= STATE =================
pending_playlists = {}  # {user_id: {...}}

# ================= CALLBACK HANDLER =================
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return

    data = q.data or ""
    uid = q.from_user.id
    await q.answer()

    if data == "check_join":
        if await is_member(uid, context):
            try:
                await q.edit_message_text("✅ عضویت تأیید شد. حالا فایل یا لینک ارسال کنید.")
            except:
                pass
        else:
            await q.answer("❌ هنوز عضو کانال نیستید.", show_alert=True)
        return

    if data.startswith("q_"):
        q_val = data[2:]
        if q_val not in ("best", "128", "192", "320"):
            return
        set_user_quality(uid, q_val)
        text_map = {
            "best": "بهترین کیفیت موجود",
            "128": "۱۲۸ kbps",
            "192": "۱۹۲ kbps",
            "320": "۳۲۰ kbps",
        }
        try:
            await q.edit_message_text(
                f"🎚 کیفیت پیش‌فرض شما روی «{text_map[q_val]}» تنظیم شد.\n"
                "از این به بعد لینک‌های SoundCloud با این کیفیت دانلود می‌شوند."
            )
        except:
            pass
        return

    # ادامه / ری‌استارت Job
    if data.startswith("resume:"):
        job_id = data.split(":", 1)[1]
        pending = get_pending_indices_for_job(job_id)
        if not pending:
            return await q.edit_message_text("❌ هیچ ترک ناتمامی برای این پلی‌لیست پیدا نشد.")

        await q.edit_message_text("▶️ ادامهٔ پردازش پلی‌لیست… در حال آماده‌سازی…")

        async def task():
            await process_playlist_job_resume(uid, context, job_id, pending)

        await queue.put(task)
        return

    if data.startswith("restart:"):
        job_id = data.split(":", 1)[1]
        reset_job(job_id)
        try:
            await q.edit_message_text("🔄 پردازش از اول شروع می‌شود.\nلطفاً دوباره لینک را ارسال کن.")
        except:
            pass
        return

    # انتخاب پلی‌لیست (همه / دستی)
    if data.startswith("pl_all:") or data.startswith("pl_select:"):
        if uid not in pending_playlists:
            try:
                await q.edit_message_text("⛔ اطلاعات پلی‌لیست پیدا نشد. دوباره لینک را بفرست.")
            except:
                pass
            return

        job_id = data.split(":", 1)[1]
        pl = pending_playlists.get(uid)
        if not pl or pl["job_id"] != job_id:
            try:
                await q.edit_message_text("⛔ این درخواست منقضی شده است. دوباره لینک را بفرست.")
            except:
                pass
            return

        if data.startswith("pl_all:"):
            pl["await_selection"] = False
            pending_playlists[uid] = pl
            try:
                await q.edit_message_text("✅ همهٔ ترک‌ها انتخاب شدند.\nدر حال شروع دانلود و پردازش هستم…")
            except:
                pass

            msg = await context.bot.send_message(
                chat_id=pl["chat_id"],
                text="🔄 در حال آماده‌سازی دانلود پلی‌لیست…"
            )
            pl["status_msg_id"] = msg.message_id
            pending_playlists[uid] = pl

            async def task():
                await process_playlist(uid, context, pl, list(range(len(pl["tracks"]))))

            await queue.put(task)

        elif data.startswith("pl_select:"):
            pl["await_selection"] = True
            pending_playlists[uid] = pl
            try:
                await q.edit_message_text(
                    "✏️ شماره‌ی ترک‌هایی که می‌خواهی را بفرست:\n"
                    "مثال: 1,3,5-10,22"
                )
            except:
                pass

# ================= COMMANDS =================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user:
        return

    uid = update.message.from_user.id
    save_user(uid)
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
    save_user(uid)
    rows = get_history(uid, 10)
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
    save_user(uid)
    current = get_user_quality(uid)
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

# ================= AUDIO HANDLER =================
async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user:
        return

    uid = update.message.from_user.id
    save_user(uid)

    if not await is_member(uid, context):
        return await force_join(update, context)

    audio = update.message.audio or update.message.document
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

            add_history(uid, name, "forwarded")
            await msg.edit_text("✅ فایل با موفقیت پردازش و ارسال شد.")
        except Exception as e:
            logging.error(f"Error processing audio: {e}")
            try:
                await msg.edit_text("❌ خطایی در پردازش فایل رخ داد.")
            except:
                pass
        finally:
            for p in (raw, final):
                if os.path.exists(p):
                    os.remove(p)

    await queue.put(task)

# ================= SOUNDLOUD PLAYLIST / SET =================
SC_REGEX = re.compile(r"https?://(?:on\.)?soundcloud\.com/[^\s]+")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user:
        return

    uid = update.message.from_user.id
    text = update.message.text or ""
    save_user(uid)

    if not await is_member(uid, context):
        return await force_join(update, context)

    # اگر در حالت انتخاب دستی پلی‌لیست هست
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
    url = resolve_soundcloud_url(raw_url)
    user_quality = get_user_quality(uid)

    info_msg = await update.message.reply_text("🔍 در حال تحلیل لینک SoundCloud…")

    # اگر Job ناتمام وجود دارد
    existing = get_incomplete_job(uid, url)
    if existing:
        job_id, pl_title, total_tracks = existing
        cur.execute("""
            SELECT COUNT(*) FROM job_tracks WHERE job_id=? AND status='sent'
        """, (job_id,))
        done = cur.fetchone()[0]
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

    # ساخت Job جدید برای Resume
    job_id = uuid4().hex
    create_job(job_id, uid, playlist_title, url, total)
    create_job_tracks(job_id, tracks)

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

# ================= PLAYLIST PROCESSING =================
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
                    except:
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
                    add_history(uid, title, playlist_title)
                    mark_track_sent(job_id, idx)
                except Exception as e:
                    logging.error(f"[Playlist] Send error for {title}: {e}")
                finally:
                    try:
                        if os.path.exists(final):
                            os.remove(final)
                    except:
                        pass

            await update_status(pos, "اتمام ترک فعلی", title)

        finish_job(job_id)
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
        except:
            pass
    finally:
        if uid in pending_playlists:
            del pending_playlists[uid]

async def process_playlist_job_resume(uid: int, context: ContextTypes.DEFAULT_TYPE, job_id: str, pending_indices_with_titles):
    cur.execute("SELECT playlist_title, source_url, total_tracks FROM jobs WHERE job_id=?", (job_id,))
    row = cur.fetchone()
    if not row:
        return
    playlist_title, url, total_tracks = row
    playlist_hashtag = make_playlist_hashtag(playlist_title)

    chat_id = uid
    msg = await context.bot.send_message(chat_id, "🔄 ادامهٔ پردازش پلی‌لیست…")

    quality = get_user_quality(uid)
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

            mark_track_sent(job_id, idx)
            add_history(uid, title, playlist_title)
            sent += 1
        except Exception as e:
            logging.error(f"[Resume] Error for track {title}: {e}")
        finally:
            for p in (raw, final):
                if os.path.exists(p):
                    os.remove(p)

    finish_job(job_id)
    await msg.edit_text(
        f"✅ ادامهٔ پردازش پلی‌لیست با موفقیت انجام شد.\n"
        f"📀 {playlist_title}\n"
        f"🎧 {sent}/{total_pending} ترک باقی‌مانده ارسال شد."
    )
    logging.info(f"[Resume] Job {job_id} resume finished. Sent {sent}/{total_pending} tracks.")

# ================= MAIN =================
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
