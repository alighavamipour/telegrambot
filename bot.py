# =========================================================
# bot.py - SOUNDLOUD PRO BOT (Supabase + Async + Resume)
# =========================================================

import os
import re
import logging
import asyncio
import json
from uuid import uuid4
from datetime import datetime

import requests
from supabase import AsyncClient, create_client

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

# ================= ENV =================
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

# ================= LOGGING =================
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ================= SUPABASE CLIENT =================
supabase: AsyncClient = None

async def init_supabase():
    global supabase
    supabase = await AsyncClient.create(SUPABASE_URL, SUPABASE_KEY)
    logging.info("Supabase client initialized.")
# =========================================================
# ===============  SUPABASE DATABASE HELPERS  =============
# =========================================================

# ---------------- USERS ----------------
async def save_user(uid: int):
    await supabase.table("users").upsert({"user_id": uid}).execute()

# ---------------- SETTINGS ----------------
async def set_user_quality(uid: int, quality: str):
    await supabase.table("settings").upsert({
        "user_id": uid,
        "quality": quality,
        "updated_at": datetime.utcnow().isoformat()
    }).execute()

async def get_user_quality(uid: int) -> str:
    res = await supabase.table("settings").select("quality").eq("user_id", uid).execute()
    if res.data:
        return res.data[0]["quality"]
    return "best"

# ---------------- HISTORY ----------------
async def add_history(uid: int, title: str, source: str):
    await supabase.table("history").insert({
        "user_id": uid,
        "title": title,
        "source": source,
        "created_at": datetime.utcnow().isoformat()
    }).execute()

async def get_history(uid: int, limit: int = 10):
    res = await supabase.table("history") \
        .select("*") \
        .eq("user_id", uid) \
        .order("id", desc=True) \
        .limit(limit) \
        .execute()
    return res.data or []

# ---------------- JOBS (Resume System) ----------------
async def create_job(job_id, user_id, playlist_title, url, total_tracks):
    await supabase.table("jobs").upsert({
        "job_id": job_id,
        "user_id": user_id,
        "playlist_title": playlist_title,
        "source_url": url,
        "total_tracks": total_tracks,
        "status": "running",
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat()
    }).execute()

async def create_job_tracks(job_id, tracks):
    rows = []
    for idx, t in enumerate(tracks):
        rows.append({
            "job_id": job_id,
            "track_index": idx,
            "title": t["title"],
            "status": "pending"
        })
    await supabase.table("job_tracks").upsert(rows).execute()

async def get_incomplete_job(user_id, url):
    res = await supabase.table("jobs") \
        .select("job_id, playlist_title, total_tracks") \
        .eq("user_id", user_id) \
        .eq("source_url", url) \
        .eq("status", "running") \
        .execute()
    return res.data[0] if res.data else None

async def get_pending_indices_for_job(job_id):
    res = await supabase.table("job_tracks") \
        .select("track_index, title") \
        .eq("job_id", job_id) \
        .neq("status", "sent") \
        .order("track_index") \
        .execute()
    return [(r["track_index"], r["title"]) for r in res.data]

async def mark_track_sent(job_id, index):
    await supabase.table("job_tracks") \
        .update({"status": "sent"}) \
        .eq("job_id", job_id) \
        .eq("track_index", index) \
        .execute()

    await supabase.table("jobs") \
        .update({"updated_at": datetime.utcnow().isoformat()}) \
        .eq("job_id", job_id) \
        .execute()

async def finish_job(job_id):
    await supabase.table("jobs") \
        .update({"status": "finished", "updated_at": datetime.utcnow().isoformat()}) \
        .eq("job_id", job_id) \
        .execute()

async def reset_job(job_id):
    await supabase.table("job_tracks").delete().eq("job_id", job_id).execute()
    await supabase.table("jobs").delete().eq("job_id", job_id).execute()

# =========================================================
# ======================== UTILS ==========================
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
        return r.url
    except:
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

# =========================================================
# ======================== QUEUE ==========================
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

async def start_workers(app: Application):
    asyncio.create_task(init_supabase())
    for _ in range(CONCURRENCY):
        asyncio.create_task(worker())
    logging.info("Workers started.")
# =========================================================
# ===================== CALLBACK HANDLER ==================
# =========================================================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return

    data = q.data or ""
    uid = q.from_user.id
    await q.answer()

    # ---------------- CHECK JOIN ----------------
    if data == "check_join":
        if await is_member(uid, context):
            try:
                await q.edit_message_text("✅ عضویت تأیید شد. حالا فایل یا لینک ارسال کنید.")
            except:
                pass
        else:
            await q.answer("❌ هنوز عضو کانال نیستید.", show_alert=True)
        return

    # ---------------- QUALITY ----------------
    if data.startswith("q_"):
        q_val = data[2:]
        if q_val not in ("best", "128", "192", "320"):
            return

        await set_user_quality(uid, q_val)

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

    # ---------------- RESUME JOB ----------------
    if data.startswith("resume:"):
        job_id = data.split(":", 1)[1]
        pending = await get_pending_indices_for_job(job_id)

        if not pending:
            return await q.edit_message_text("❌ هیچ ترک ناتمامی برای این پلی‌لیست پیدا نشد.")

        await q.edit_message_text("▶️ ادامهٔ پردازش پلی‌لیست… در حال آماده‌سازی…")

        async def task():
            await process_playlist_job_resume(uid, context, job_id, pending)

        await queue.put(task)
        return

    # ---------------- RESTART JOB ----------------
    if data.startswith("restart:"):
        job_id = data.split(":", 1)[1]
        await reset_job(job_id)

        try:
            await q.edit_message_text("🔄 پردازش از اول شروع می‌شود.\nلطفاً دوباره لینک را ارسال کن.")
        except:
            pass
        return

    # ---------------- PLAYLIST SELECTION ----------------
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

        # ---- ALL TRACKS ----
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
            return

        # ---- MANUAL SELECTION ----
        if data.startswith("pl_select:"):
            pl["await_selection"] = True
            pending_playlists[uid] = pl

            try:
                await q.edit_message_text(
                    "✏️ شماره‌ی ترک‌هایی که می‌خواهی را بفرست:\n"
                    "مثال: 1,3,5-10,22"
                )
            except:
                pass
            return


# =========================================================
# ========================= COMMANDS ======================
# =========================================================

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
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
    if not update.message:
        return

    uid = update.message.from_user.id
    rows = await get_history(uid, 10)

    if not rows:
        return await update.message.reply_text("📂 هنوز هیچ موزیکی با ربات پردازش نکردی.")

    lines = []
    for r in rows:
        src = r["source"] if r["source"] != "forwarded" else "فایل فورواردی / آپلود"
        lines.append(f"• {r['title']}\n  ↳ {src}")

    await update.message.reply_text("🕘 آخرین موزیک‌های پردازش‌شده:\n\n" + "\n\n".join(lines))


async def quality_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    uid = update.message.from_user.id
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
# ======================== AUDIO HANDLER ==================
# =========================================================

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    uid = update.message.from_user.id
    await save_user(uid)

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

            await add_history(uid, name, "forwarded")
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
# =========================================================
# ===================== TEXT HANDLER ======================
# =========================================================

pending_playlists = {}  # uid -> {job_id, tracks, ...}

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    uid = update.message.from_user.id
    text = update.message.text.strip()

    await save_user(uid)

    if not await is_member(uid, context):
        return await force_join(update, context)

    # اگر کاربر در حالت انتخاب ترک باشد
    if uid in pending_playlists and pending_playlists[uid].get("await_selection"):
        pl = pending_playlists[uid]
        max_n = len(pl["tracks"])
        selected = parse_selection(text, max_n)

        if not selected:
            return await update.message.reply_text("❌ انتخاب نامعتبر. دوباره امتحان کن.")

        pl["await_selection"] = False
        pending_playlists[uid] = pl

        msg = await update.message.reply_text("🔄 در حال شروع پردازش انتخاب‌های شما…")
        pl["status_msg_id"] = msg.message_id

        async def task():
            await process_playlist(uid, context, pl, selected)

        await queue.put(task)
        return

    # لینک SoundCloud
    if "soundcloud.com" in text.lower():
        url = resolve_soundcloud_url(text)
        return await handle_soundcloud_link(update, context, uid, url)

    await update.message.reply_text("❗ لطفاً لینک SoundCloud یا فایل موسیقی ارسال کن.")


# =========================================================
# =============== HANDLE SOUNDCLOUD LINK ==================
# =========================================================

async def handle_soundcloud_link(update, context, uid, url):
    msg = await update.message.reply_text("🔍 در حال بررسی لینک…")

    # بررسی Resume
    job = await get_incomplete_job(uid, url)
    if job:
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("▶️ ادامه", callback_data=f"resume:{job['job_id']}"),
                InlineKeyboardButton("🔄 از اول", callback_data=f"restart:{job['job_id']}")
            ]
        ])
        return await msg.edit_text(
            f"⏸ یک پردازش ناتمام برای این پلی‌لیست پیدا شد:\n\n"
            f"🎵 {job['playlist_title']}\n"
            f"تعداد ترک‌ها: {job['total_tracks']}\n\n"
            "می‌خواهی ادامه بدهم یا از اول شروع کنم؟",
            reply_markup=kb
        )

    # دریافت اطلاعات پلی‌لیست
    try:
        import yt_dlp
        ydl_opts = {"quiet": True, "extract_flat": True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        logging.error(e)
        return await msg.edit_text("❌ خطا در دریافت اطلاعات پلی‌لیست.")

    if "entries" not in info or not info["entries"]:
        return await msg.edit_text("❌ این لینک پلی‌لیست معتبر نیست.")

    tracks = []
    for e in info["entries"]:
        if not e:
            continue
        title = clean_filename(e.get("title") or "track")
        turl = e.get("url") or e.get("webpage_url")
        if turl:
            tracks.append({"title": title, "url": turl})

    if not tracks:
        return await msg.edit_text("❌ هیچ ترک معتبری پیدا نشد.")

    playlist_title = clean_filename(info.get("title") or "playlist")
    job_id = uuid4().hex

    pending_playlists[uid] = {
        "job_id": job_id,
        "playlist_title": playlist_title,
        "tracks": tracks,
        "chat_id": update.message.chat_id,
        "await_selection": False,
        "status_msg_id": None,
        "url": url
    }

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📥 دانلود همه", callback_data=f"pl_all:{job_id}"),
            InlineKeyboardButton("✏️ انتخاب دستی", callback_data=f"pl_select:{job_id}")
        ]
    ])

    await msg.edit_text(
        f"🎵 *{playlist_title}*\n"
        f"تعداد ترک‌ها: {len(tracks)}\n\n"
        "می‌خواهی همه را دانلود کنم یا خودت انتخاب می‌کنی؟",
        reply_markup=kb,
        parse_mode="Markdown"
    )


# =========================================================
# =============== PROCESS PLAYLIST (NEW JOB) ==============
# =========================================================

async def process_playlist(uid, context, pl, selected_indices):
    job_id = pl["job_id"]
    tracks = pl["tracks"]
    playlist_title = pl["playlist_title"]
    url = pl["url"]
    chat_id = pl["chat_id"]
    status_msg_id = pl["status_msg_id"]

    await create_job(job_id, uid, playlist_title, url, len(tracks))

    selected_tracks = [tracks[i] for i in selected_indices]
    await create_job_tracks(job_id, selected_tracks)

    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=status_msg_id,
        text=f"▶️ شروع پردازش پلی‌لیست…\n"
             f"🎵 {playlist_title}\n"
             f"تعداد انتخاب‌شده: {len(selected_tracks)}"
    )

    for idx, t in enumerate(selected_tracks):
        try:
            await process_single_track(uid, context, job_id, idx, t, chat_id, status_msg_id)
        except Exception as e:
            logging.error(f"Track error: {e}")

    await finish_job(job_id)

    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=status_msg_id,
        text=f"✅ پردازش پلی‌لیست تمام شد.\n🎵 {playlist_title}"
    )

    pending_playlists.pop(uid, None)


# =========================================================
# =============== PROCESS PLAYLIST (RESUME) ===============
# =========================================================

async def process_playlist_job_resume(uid, context, job_id, pending):
    # pending = [(index, title), ...]
    for index, title in pending:
        try:
            await process_single_track_resume(uid, context, job_id, index, title)
        except Exception as e:
            logging.error(f"Resume error: {e}")

    await finish_job(job_id)


# =========================================================
# =============== PROCESS SINGLE TRACK ====================
# =========================================================

async def process_single_track(uid, context, job_id, index, track, chat_id, status_msg_id):
    title = track["title"]
    url = track["url"]

    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=status_msg_id,
        text=f"🎧 در حال دانلود:\n{title}"
    )

    import yt_dlp
    q = await get_user_quality(uid)
    fmt = get_format_for_quality(q)

    uid_job = uuid4().hex
    raw = f"{DOWNLOAD_DIR}/{uid_job}_in.mp3"
    final = f"{DOWNLOAD_DIR}/{uid_job}_out.mp3"

    try:
        ydl_opts = {
            "quiet": True,
            "format": fmt,
            "outtmpl": raw
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=status_msg_id,
            text=f"🎨 افزودن کاور:\n{title}"
        )

        await tag_and_cover(raw, final, title)

        size = os.path.getsize(final)
        caption = f"🎵 {title}\n{make_playlist_hashtag(track['title'])}\n🔗 @{CHANNEL_USERNAME}"

        with open(final, "rb") as f:
            if size <= MAX_FILE_SIZE:
                await context.bot.send_audio(CHANNEL_ID, f, filename=title + ".mp3", caption=caption)
            else:
                await context.bot.send_document(CHANNEL_ID, f, filename=title + ".mp3", caption=caption)

        await mark_track_sent(job_id, index)
        await add_history(uid, title, "soundcloud")

    except Exception as e:
        logging.error(e)

    finally:
        for p in (raw, final):
            if os.path.exists(p):
                os.remove(p)


# =========================================================
# =============== PROCESS SINGLE TRACK (RESUME) ===========
# =========================================================

async def process_single_track_resume(uid, context, job_id, index, title):
    # این نسخه فقط برای Resume است
    # چون URL در job_tracks ذخیره نشده، باید دوباره از Supabase بگیریم
    res = await supabase.table("job_tracks") \
        .select("title") \
        .eq("job_id", job_id) \
        .eq("track_index", index) \
        .execute()

    if not res.data:
        return

    # در Resume فقط عنوان داریم، URL را باید از jobs بگیریم
    job = await supabase.table("jobs").select("source_url").eq("job_id", job_id).execute()
    if not job.data:
        return

    url = job.data[0]["source_url"]

    # دانلود مجدد
    import yt_dlp
    q = await get_user_quality(uid)
    fmt = get_format_for_quality(q)

    uid_job = uuid4().hex
    raw = f"{DOWNLOAD_DIR}/{uid_job}_in.mp3"
    final = f"{DOWNLOAD_DIR}/{uid_job}_out.mp3"

    try:
        ydl_opts = {"quiet": True, "format": fmt, "outtmpl": raw}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        await tag_and_cover(raw, final, title)

        size = os.path.getsize(final)
        caption = f"🎵 {title}\n🔗 @{CHANNEL_USERNAME}"

        with open(final, "rb") as f:
            if size <= MAX_FILE_SIZE:
                await context.bot.send_audio(CHANNEL_ID, f, filename=title + ".mp3", caption=caption)
            else:
                await context.bot.send_document(CHANNEL_ID, f, filename=title + ".mp3", caption=caption)

        await mark_track_sent(job_id, index)
        await add_history(uid, title, "soundcloud")

    except Exception as e:
        logging.error(e)

    finally:
        for p in (raw, final):
            if os.path.exists(p):
                os.remove(p)


# =========================================================
# ======================== FORCE JOIN ======================
# =========================================================

async def is_member(uid, context):
    try:
        m = await context.bot.get_chat_member(CHANNEL_ID, uid)
        return m.status in ("member", "administrator", "creator")
    except:
        return False

async def force_join(update, context):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 عضویت در کانال", url=f"https://t.me/{CHANNEL_USERNAME}")],
        [InlineKeyboardButton("✔️ بررسی عضویت", callback_data="check_join")]
    ])
    await update.message.reply_text(
        "برای استفاده از ربات باید عضو کانال شوید:",
        reply_markup=kb
    )


# =========================================================
# =========================== MAIN =========================
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
        port=int(os.getenv("PORT", 8080)),
        url_path=BOT_TOKEN,
        webhook_url=f"{BASE_URL}/{BOT_TOKEN}"
    )


if __name__ == "__main__":
    main()
