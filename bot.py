# =========================================================
# bot.py - SOUNDLOUD POWERED BOT (PLAYLIST + QUALITY + HISTORY)
# =========================================================

import os, re, sqlite3, logging, asyncio, requests
from uuid import uuid4

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
MAX_FILE_SIZE = 50 * 1024 * 1024        # محدودیت sendAudio تلگرام (sendDocument تا 2GB اوکی است)

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ================= LOGGING =================
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ================= DATABASE =================
conn = sqlite3.connect("users.db", check_same_thread=False)
cur = conn.cursor()

# users
cur.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)")
# history: آخرین ترک‌های پردازش‌شده برای هر کاربر
cur.execute("""
    CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        title TEXT,
        source TEXT,
        created_at TEXT
    )
""")
# settings: تنظیمات کاربر (مثلاً کیفیت)
cur.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        user_id INTEGER PRIMARY KEY,
        quality TEXT
    )
""")
conn.commit()

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
    from datetime import datetime
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
    """
    تبدیل هر ورودی به mp3 با کاور و تگ کانال.
    """
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
    """
    quality:
      - best
      - 128
      - 192
      - 320
    """
    if q == "128":
        return "bestaudio[abr<=128]/bestaudio"
    if q == "192":
        return "bestaudio[abr<=192]/bestaudio"
    if q == "320":
        # سعی می‌کنیم بالاتر از 256 یا 192 پیدا کنیم
        return "bestaudio[abr>=256]/bestaudio[abr>=192]/bestaudio"
    return "bestaudio/best"

# ================= QUEUE =================
queue: asyncio.Queue = asyncio.Queue()
CONCURRENCY = 3

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
    await update.message.reply_text(
        "🔔 برای استفاده از ربات ابتدا عضو کانال شوید.",
        reply_markup=kb
    )

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
    await q.answer()

    if data == "check_join":
        if await is_member(q.from_user.id, context):
            await q.edit_message_text("✅ عضویت تأیید شد. حالا فایل یا لینک ارسال کنید.")
        else:
            await q.answer("❌ هنوز عضو کانال نیستید.", show_alert=True)
    elif data.startswith("q_"):
        # تغییر کیفیت
        q_val = data[2:]
        if q_val not in ("best", "128", "192", "320"):
            return
        set_user_quality(q.from_user.id, q_val)
        text_map = {
            "best": "بهترین کیفیت موجود",
            "128": "۱۲۸ kbps",
            "192": "۱۹۲ kbps",
            "320": "۳۲۰ kbps",
        }
        await q.edit_message_text(
            f"🎚 کیفیت پیش‌فرض شما روی «{text_map[q_val]}» تنظیم شد.\n"
            "از این به بعد لینک‌های SoundCloud با این کیفیت دانلود می‌شوند."
        )

# ================= START & COMMANDS =================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

# ================= FORWARDED / UPLOADED AUDIO =================
async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.from_user.id
    save_user(uid)

    if not await is_member(uid, context):
        return await force_join(update, context)

    audio = update.message.audio or update.message.document
    name = clean_filename(getattr(audio, "file_name", "") or "music")
    ext = guess_ext(audio)

    # محدودیت Telegram برای Audio بالای 20MB
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
            await msg.edit_text("❌ خطایی در پردازش فایل رخ داد.")
        finally:
            for p in (raw, final):
                if os.path.exists(p):
                    os.remove(p)

    await queue.put(task)

# ================= SOUNDLOUD LINKS (SINGLE + PLAYLIST + SET) =================
SC_REGEX = re.compile(r"https?://(?:on\.)?soundcloud\.com/[^\s]+")

async def handle_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    uid = update.message.from_user.id
    save_user(uid)

    if not await is_member(uid, context):
        return await force_join(update, context)

    url_match = SC_REGEX.search(text)
    if not url_match:
        return await update.message.reply_text("⚠️ فقط لینک‌های SoundCloud پشتیبانی می‌شوند.")

    raw_url = url_match.group(0)
    url = resolve_soundcloud_url(raw_url)

    user_quality = get_user_quality(uid)
    fmt = get_format_for_quality(user_quality)

    msg = await update.message.reply_text(
        "🔍 در حال تحلیل لینک SoundCloud…\n"
        "اگر Playlist یا Set باشد، همه ترک‌ها پردازش می‌شوند."
    )

    uid_job = uuid4().hex
    # همه ورودی‌ها در این job با این prefix ذخیره می‌شوند
    pattern = os.path.join(DOWNLOAD_DIR, f"{uid_job}_in_%(playlist_index)03d_%(title)s.%(ext)s")
    final_pattern_prefix = os.path.join(DOWNLOAD_DIR, f"{uid_job}_out_")

    async def task():
        try:
            await msg.edit_text("⬇️ در حال دانلود ترک‌ها (تکی یا Playlist/Set)…\n"
                                f"🎚 کیفیت انتخابی: {user_quality}")

            # دانلود همه‌ی ترک‌ها (حتی اگر لینک تکی باشد)
            await run_cmd(
                "yt-dlp",
                "-f", fmt,
                "--yes-playlist",
                "-o", pattern,
                url
            )

            # پیدا کردن همه فایل‌های دانلود شده برای این job
            input_files = [
                f for f in os.listdir(DOWNLOAD_DIR)
                if f.startswith(f"{uid_job}_in_")
            ]
            if not input_files:
                await msg.edit_text("❌ دانلود ناموفق بود.")
                return

            # مرتب‌سازی تا ترک‌ها به ترتیب Playlist/Set ارسال شوند
            input_files.sort()

            await msg.edit_text(
                f"🎧 {len(input_files)} ترک پیدا شد.\n"
                "در حال تبدیل و افزودن کاور اختصاصی روی همه ترک‌ها…"
            )

            sent_count = 0
            for in_file in input_files:
                in_path = os.path.join(DOWNLOAD_DIR, in_file)
                # استخراج عنوان از اسم فایل (بعد از prefix و index)
                base = os.path.splitext(in_file)[0]  # uid_in_001_Title
                # حذف prefix
                base_title = base.split("_", 3)[-1] if "_" in base else base
                title = clean_filename(base_title)

                out_path = f"{final_pattern_prefix}{base_title}.mp3"

                try:
                    await tag_and_cover(in_path, out_path, title)
                except Exception as e:
                    logging.error(f"Error tag_and_cover for {in_path}: {e}")
                    continue

                size = os.path.getsize(out_path)
                caption = f"🎵 {title}\n🔗 @{CHANNEL_USERNAME}"

                with open(out_path, "rb") as f:
                    if size <= MAX_FILE_SIZE:
                        await context.bot.send_audio(CHANNEL_ID, f, filename=title + ".mp3", caption=caption)
                    else:
                        await context.bot.send_document(CHANNEL_ID, f, filename=title + ".mp3", caption=caption)

                add_history(uid, title, url)
                sent_count += 1

                # پاک کردن فایل خروجی بعد از ارسال برای کاهش فضای دیسک
                try:
                    if os.path.exists(out_path):
                        os.remove(out_path)
                except:
                    pass

            await msg.edit_text(
                f"✅ عملیات تمام شد.\n"
                f"{sent_count} ترک از SoundCloud در کانال منتشر شد."
            )
        except Exception as e:
            logging.error(f"Error processing SoundCloud link: {e}")
            await msg.edit_text("❌ خطایی در دانلود یا پردازش لینک SoundCloud رخ داد.")
        finally:
            # پاک کردن ورودی‌ها
            for f in os.listdir(DOWNLOAD_DIR):
                if f.startswith(f"{uid_job}_in_"):
                    try:
                        os.remove(os.path.join(DOWNLOAD_DIR, f))
                    except:
                        pass

    await queue.put(task)

# ================= MAIN =================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("history", history_cmd))
    app.add_handler(CommandHandler("quality", quality_cmd))

    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.AUDIO | filters.Document.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_links))

    app.post_init = start_workers

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        webhook_url=BASE_URL
    )

if __name__ == "__main__":
    main()
