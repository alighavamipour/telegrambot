# =========================================================
# bot.py - FINAL STABLE & OPTIMIZED VERSION
# =========================================================

import os, re, sqlite3, logging, asyncio
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
    name = re.sub(r'\.(mp3|m4a|wav|flac)$', '', name, flags=re.I)
    return name.strip()

async def run_cmd(*cmd):
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise Exception(err.decode())

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
    await update.message.reply_text("برای استفاده از ربات عضو کانال شوید 👇", reply_markup=kb)

async def check_join(update, context):
    q = update.callback_query
    await q.answer()
    if await is_member(q.from_user.id, context):
        await q.edit_message_text("✅ تایید شد، موزیک بفرست 🎧")
    else:
        await q.answer("❌ هنوز عضو نیستی", show_alert=True)

# ================= START =================
async def start(update, context):
    save_user(update.message.from_user.id)
    if not await is_member(update.message.from_user.id, context):
        return await force_join(update, context)
    await update.message.reply_text("🎵 موزیک فوروارد کن یا لینک SoundCloud بفرست")

# ================= QUEUE =================
queue = asyncio.Queue()

async def worker():
    while True:
        task = await queue.get()
        try:
            await task()
        finally:
            queue.task_done()

# ================= PROCESS AUDIO =================
async def tag_and_cover(src, dst, title):
    await run_cmd(
        "ffmpeg", "-y",
        "-i", src,
        "-i", COVER_PATH,
        "-map", "0:a", "-map", "1:v",
        "-c:a", "copy",
        "-c:v", "mjpeg",
        "-metadata", f"title={title}",
        "-metadata", f"artist=@{CHANNEL_USERNAME}",
        "-metadata", f"album=@{CHANNEL_USERNAME}",
        dst
    )

# ================= FORWARDED AUDIO =================
async def handle_audio(update, context):
    save_user(update.message.from_user.id)
    if not await is_member(update.message.from_user.id, context):
        return await force_join(update, context)

    audio = update.message.audio or update.message.document
    name = clean_filename(audio.file_name or "music")
    msg = await update.message.reply_text(f"✅ فایل «{name}» دریافت شد")

    uid = uuid4().hex
    raw = f"{DOWNLOAD_DIR}/{uid}.raw"
    final = f"{DOWNLOAD_DIR}/{uid}.mp3"

    async def task():
        await msg.edit_text("⬇️ در حال دانلود فایل…")
        file = await audio.get_file()
        await file.download_to_drive(raw)

        await msg.edit_text("⚙️ در حال آماده‌سازی…")
        await tag_and_cover(raw, final, name)

        await msg.edit_text("⬆️ در حال آپلود در کانال…")
        size = os.path.getsize(final)

        with open(final, "rb") as f:
            if size <= MAX_FILE_SIZE:
                await context.bot.send_audio(CHANNEL_ID, f, title=name)
            else:
                await context.bot.send_document(CHANNEL_ID, f)

        await msg.edit_text("🎉 با موفقیت در کانال منتشر شد")

    await queue.put(task)

# ================= SOUNDCLOUD =================
SC_REGEX = re.compile(r"soundcloud\.com")

async def handle_soundcloud(update, context):
    if not SC_REGEX.search(update.message.text or ""):
        return

    save_user(update.message.from_user.id)
    if not await is_member(update.message.from_user.id, context):
        return await force_join(update, context)

    url = update.message.text.strip()
    msg = await update.message.reply_text("🔍 دریافت اطلاعات از SoundCloud…")

    uid = uuid4().hex
    raw = f"{DOWNLOAD_DIR}/{uid}.raw"
    final = f"{DOWNLOAD_DIR}/{uid}.mp3"

    async def task():
        title = os.popen(f'yt-dlp --print "%(title)s" "{url}"').read().strip()
        await msg.edit_text(f"⬇️ در حال دانلود «{title}»")

        await run_cmd("yt-dlp", "-f", "bestaudio", "-o", raw, url)

        await msg.edit_text("⚙️ آماده‌سازی نهایی…")
        await tag_and_cover(raw, final, title)

        await msg.edit_text("⬆️ در حال آپلود در کانال…")
        size = os.path.getsize(final)

        with open(final, "rb") as f:
            if size <= MAX_FILE_SIZE:
                await context.bot.send_audio(CHANNEL_ID, f, title=title)
            else:
                await context.bot.send_document(CHANNEL_ID, f)

        await msg.edit_text("🎉 منتشر شد")

    await queue.put(task)

# ================= MAIN =================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(check_join))
    app.add_handler(MessageHandler(filters.AUDIO | filters.Document.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_soundcloud))

    loop = asyncio.get_event_loop()
    loop.create_task(worker())

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        webhook_url=BASE_URL
    )

if __name__ == "__main__":
    main()
