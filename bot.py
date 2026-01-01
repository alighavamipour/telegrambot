# =========================================================
# bot.py - FINAL STABLE WITH FULL COVER/TAG + BIG FILE FIX
# =========================================================

import os, re, sqlite3, logging, asyncio, requests
from uuid import uuid4
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters

# ================= ENV =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME")
CHANNEL_ID = os.getenv("CHANNEL_ID")
BASE_URL = os.getenv("BASE_URL")

DOWNLOAD_DIR = "downloads"
COVER_PATH = "cover.jpg"
MAX_AUDIO_LIMIT = 20 * 1024 * 1024   # محدودیت دانلود Audio در Telegram
MAX_FILE_SIZE = 50 * 1024 * 1024     # محدودیت sendAudio
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ================= LOGGING =================
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ================= DATABASE =================
conn = sqlite3.connect("users.db", check_same_thread=False)
cur = conn.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)")
conn.commit()

def save_user(uid):
    cur.execute("INSERT OR IGNORE INTO users VALUES (?)", (uid,))
    conn.commit()

# ================= UTILS =================
def clean_filename(name):
    name = re.sub(r'\.(mp3|m4a|wav|flac|ogg|opus)$', '', name, flags=re.I)
    return name.strip() or "music"

def guess_ext(audio_obj):
    if getattr(audio_obj, "file_name", None):
        fn = audio_obj.file_name
        if "." in fn:
            return fn.split(".")[-1].lower()

    mime = getattr(audio_obj, "mime_type", "") or ""
    mime = mime.lower()

    if "mpeg" in mime: return "mp3"
    if "wav" in mime: return "wav"
    if "flac" in mime: return "flac"
    if "ogg" in mime: return "ogg"
    if "opus" in mime: return "opus"
    if "m4a" in mime or "mp4" in mime: return "m4a"

    return "mp3"

async def run_cmd(*cmd):
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise Exception(stderr.decode() or stdout.decode())

async def tag_and_cover(src, dst, title):
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

# ================= QUEUE =================
queue = asyncio.Queue()
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

async def start_workers(app):
    for _ in range(CONCURRENCY):
        asyncio.create_task(worker())
    logging.info("Workers started.")

# ================= FORCE JOIN =================
async def is_member(uid, context):
    try:
        m = await context.bot.get_chat_member(CHANNEL_ID, uid)
        return m.status in ("member", "administrator", "creator")
    except:
        return False

async def force_join(update, context):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔔 عضویت در کانال", url=f"https://t.me/{CHANNEL_USERNAME}")],
        [InlineKeyboardButton("✅ بررسی عضویت", callback_data="check")]
    ])
    await update.message.reply_text(
        "🔔 برای استفاده از ربات ابتدا عضو کانال شوید.",
        reply_markup=kb
    )

async def check_join(update, context):
    q = update.callback_query
    await q.answer()
    if await is_member(q.from_user.id, context):
        await q.edit_message_text("✅ عضویت تأیید شد. فایل یا لینک ارسال کنید.")
    else:
        await q.answer("❌ هنوز عضو نیستید.", show_alert=True)

# ================= START =================
async def start(update, context):
    save_user(update.message.from_user.id)
    if not await is_member(update.message.from_user.id, context):
        return await force_join(update, context)
    await update.message.reply_text("🎵 فایل یا لینک SoundCloud ارسال کنید.")

# ================= PROCESS AUDIO =================
async def handle_audio(update, context):
    user = update.message.from_user.id
    save_user(user)

    if not await is_member(user, context):
        return await force_join(update, context)

    audio = update.message.audio or update.message.document
    name = clean_filename(getattr(audio, "file_name", "") or "music")
    ext = guess_ext(audio)

    # 🔥 مهم: اگر Audio بالای 20MB باشد → Telegram اجازه دانلود نمی‌دهد
    if update.message.audio and audio.file_size > MAX_AUDIO_LIMIT:
        return await update.message.reply_text(
            "⚠️ این فایل به‌صورت *Audio* ارسال شده و حجم آن بالای 20MB است.\n"
            "لطفاً فایل را به‌صورت *Document* ارسال کنید تا بتوانم پردازش کنم."
        )

    msg = await update.message.reply_text("⬇️ در حال دریافت فایل…")

    uid = uuid4().hex
    raw = f"{DOWNLOAD_DIR}/{uid}_in.{ext}"
    final = f"{DOWNLOAD_DIR}/{uid}_out.mp3"

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
                    await context.bot.send_audio(CHANNEL_ID, f, filename=name+".mp3", caption=caption)
                else:
                    await context.bot.send_document(CHANNEL_ID, f, filename=name+".mp3", caption=caption)

            await msg.edit_text("✅ فایل با موفقیت پردازش و ارسال شد.")
        except Exception as e:
            logging.error(e)
            await msg.edit_text("❌ خطایی در پردازش فایل رخ داد.")
        finally:
            for p in (raw, final):
                if os.path.exists(p):
                    os.remove(p)

    await queue.put(task)

# ================= LINKS =================
SC_REGEX = re.compile(r"https?://(?:on\.)?soundcloud\.com/[^\s]+")

async def handle_links(update, context):
    text = update.message.text or ""
    user = update.message.from_user.id
    save_user(user)

    if not await is_member(user, context):
        return await force_join(update, context)

    url_match = SC_REGEX.search(text)
    if not url_match:
        return await update.message.reply_text("⚠️ لینک SoundCloud معتبر نیست.")

    url = url_match.group(0)
    msg = await update.message.reply_text("🔍 در حال تحلیل لینک…")

    uid = uuid4().hex
    raw = f"{DOWNLOAD_DIR}/{uid}_in.raw"
    final = f"{DOWNLOAD_DIR}/{uid}_out.mp3"

    async def task():
        try:
            await msg.edit_text("⬇️ در حال دانلود…")
            await run_cmd("yt-dlp", "-f", "bestaudio", "-o", raw, url)

            title = clean_filename(os.popen(f'yt-dlp --print "%(title)s" "{url}"').read().strip() or "music")

            await msg.edit_text("🎧 در حال تبدیل و افزودن کاور…")
            await tag_and_cover(raw, final, title)

            size = os.path.getsize(final)
            caption = f"🎵 {title}\n🔗 @{CHANNEL_USERNAME}"

            await msg.edit_text("📡 در حال ارسال…")

            with open(final, "rb") as f:
                if size <= MAX_FILE_SIZE:
                    await context.bot.send_audio(CHANNEL_ID, f, filename=title+".mp3", caption=caption)
                else:
                    await context.bot.send_document(CHANNEL_ID, f, filename=title+".mp3", caption=caption)

            await msg.edit_text("✅ فایل در کانال قرار گرفت.")
        except Exception as e:
            logging.error(e)
            await msg.edit_text("❌ خطا در پردازش لینک.")
        finally:
            for p in (raw, final):
                if os.path.exists(p):
                    os.remove(p)

    await queue.put(task)

# ================= MAIN =================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(check_join))
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
