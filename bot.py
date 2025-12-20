# =========================================================
# bot.py - FINAL STABLE & FULL FEATURED WITH SOUNDLOUD SHORT URL SUPPORT
# =========================================================

import os, re, sqlite3, logging, asyncio, requests
from uuid import uuid4
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters

# ================= ENV =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME")
CHANNEL_ID = os.getenv("CHANNEL_ID")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
BASE_URL = os.getenv("BASE_URL")

DOWNLOAD_DIR = "downloads"
COVER_PATH = "cover.jpg"
MAX_FILE_SIZE = 50 * 1024 * 1024
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

async def run_cmd(*cmd):
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise Exception(stderr.decode() or stdout.decode())

def resolve_soundcloud_url(url):
    """
    دنبال کردن ریدایرکت لینک کوتاه SoundCloud و برگرداندن لینک واقعی
    """
    try:
        r = requests.head(url, allow_redirects=True, timeout=10)
        final_url = r.url
        return final_url
    except:
        return url

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
    await update.message.reply_text("برای استفاده از ربات، لطفاً عضو کانال شوید 👇", reply_markup=kb)

async def check_join(update, context):
    q = update.callback_query
    await q.answer()
    if await is_member(q.from_user.id, context):
        await q.edit_message_text("✅ عضویت تایید شد! حالا می‌توانید موزیک بفرستید 🎧")
    else:
        await q.answer("❌ هنوز عضو کانال نیستید!", show_alert=True)

# ================= START =================
async def start(update, context):
    save_user(update.message.from_user.id)
    if not await is_member(update.message.from_user.id, context):
        return await force_join(update, context)
    await update.message.reply_text("🎵 خوش آمدید! موزیک بفرستید یا لینک SoundCloud / لینک مستقیم ارسال کنید")

# ================= QUEUE =================
queue = asyncio.Queue()
CONCURRENCY = 3

async def worker():
    while True:
        task = await queue.get()
        try:
            await task()
        finally:
            queue.task_done()

# ================= PROCESS AUDIO WITH COVER =================
async def tag_and_cover(src, dst, title):
    await run_cmd(
        "ffmpeg", "-y",
        "-i", src,
        "-i", COVER_PATH,
        "-map", "0:a", "-map", "1:v",
        "-c:a", "libmp3lame",
        "-q:a", "2",
        "-c:v", "mjpeg",
        "-id3v2_version", "3",
        "-metadata", f"title={title}",
        "-metadata", f"artist=@{CHANNEL_USERNAME}",
        "-metadata", f"album=@{CHANNEL_USERNAME}",
        "-metadata", f"comment=@{CHANNEL_USERNAME}",
        dst
    )

# ================= RETRY HELPER =================
async def retry_task(task_func, retries=2):
    for attempt in range(1, retries + 1):
        try:
            await task_func()
            return True
        except Exception as e:
            logging.warning(f"Task failed, attempt {attempt}/{retries}: {e}")
            if attempt == retries:
                return False
        await asyncio.sleep(1)

# ================= FORWARDED AUDIO =================
async def handle_audio(update, context):
    save_user(update.message.from_user.id)
    if not await is_member(update.message.from_user.id, context):
        return await force_join(update, context)

    audio = update.message.audio or update.message.document
    name = clean_filename(audio.file_name or "music")
    ext = (audio.file_name or "").split(".")[-1].lower()
    msg = await update.message.reply_text(f"✅ فایل «{name}.{ext}» دریافت شد 🎵", reply_to_message_id=update.message.message_id)

    uid = uuid4().hex
    raw = f"{DOWNLOAD_DIR}/{uid}.{ext}"
    final = f"{DOWNLOAD_DIR}/{uid}.mp3"

    async def task():
        await msg.edit_text("⬇️ در حال دانلود فایل…")
        file = await audio.get_file()
        await file.download_to_drive(raw)

        if ext != "mp3":
            await msg.edit_text("⚙️ در حال تبدیل به MP3 و افزودن کاور…")
            success = await retry_task(lambda: tag_and_cover(raw, final, name))
            if not success:
                await msg.edit_text("❌ پردازش فایل ناموفق بود (تلاش 2/2)")
                return
        else:
            final = raw

        await msg.edit_text("⬆️ در حال آپلود در کانال…")
        size = os.path.getsize(final)
        caption = f"🎵 {name}\n🔗 @{CHANNEL_USERNAME}"
        with open(final, "rb") as f:
            if size <= MAX_FILE_SIZE:
                await context.bot.send_audio(CHANNEL_ID, f, filename=name, caption=caption)
            else:
                await context.bot.send_document(CHANNEL_ID, f, caption=caption)

        await msg.edit_text("🎉 فایل با موفقیت در کانال منتشر شد!")

    await queue.put(task)

# ================= LINKS / SOUNDCLOUD =================
SC_REGEX = re.compile(r"(soundcloud\.com|on\.soundcloud\.com)")
URL_REGEX = re.compile(r"https?://[^\s]+")

async def handle_links(update, context):
    text = update.message.text or ""
    save_user(update.message.from_user.id)
    if not await is_member(update.message.from_user.id, context):
        return await force_join(update, context)

    url_match = SC_REGEX.search(text) or URL_REGEX.search(text)
    if not url_match:
        await update.message.reply_text("❌ لینک معتبر نیست!")
        return

    url = resolve_soundcloud_url(url_match.group(0))
    msg = await update.message.reply_text(f"🔍 در حال بررسی اطلاعات از SoundCloud…", reply_to_message_id=update.message.message_id)

    uid = uuid4().hex
    raw = f"{DOWNLOAD_DIR}/{uid}.raw"
    final = f"{DOWNLOAD_DIR}/{uid}.mp3"

    async def task():
        try:
            await msg.edit_text("⏳ استخراج اطلاعات آهنگ…")
            title = os.popen(f'yt-dlp --print "%(title)s" "{url}"').read().strip() or "music"

            await msg.edit_text(f"⬇️ در حال دانلود آهنگ «{title}»…")
            success = await retry_task(lambda: run_cmd("yt-dlp", "-f", "bestaudio", "-o", raw, url))
            if not success:
                await msg.edit_text("❌ دانلود ناموفق بود (تلاش 2/2)")
                return

            await msg.edit_text("⚙️ در حال تبدیل به MP3 و افزودن کاور…")
            success = await retry_task(lambda: tag_and_cover(raw, final, title))
            if not success:
                await msg.edit_text("❌ پردازش فایل ناموفق بود (تلاش 2/2)")
                return

            await msg.edit_text("⬆️ در حال آپلود در کانال…")
            size = os.path.getsize(final)
            caption = f"🎵 {title}\n🔗 @{CHANNEL_USERNAME}"
            with open(final, "rb") as f:
                if size <= MAX_FILE_SIZE:
                    await context.bot.send_audio(CHANNEL_ID, f, filename=title, caption=caption)
                else:
                    await context.bot.send_document(CHANNEL_ID, f, caption=caption)

            await msg.edit_text("🎉 فایل با موفقیت در کانال منتشر شد!")
        except Exception as e:
            logging.error(f"Error processing link: {e}")
            await msg.edit_text("❌ مشکلی در دانلود یا پردازش فایل رخ داد.")

    await queue.put(task)

# ================= MAIN =================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(check_join))
    app.add_handler(MessageHandler(filters.AUDIO | filters.Document.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_links))

    loop = asyncio.get_event_loop()
    for _ in range(CONCURRENCY):
        loop.create_task(worker())

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        webhook_url=BASE_URL
    )

if __name__ == "__main__":
    main()
