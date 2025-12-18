# =========================================================
# 1. IMPORTS
# =========================================================
import os
import re
import sqlite3
import logging
import asyncio
from uuid import uuid4
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    CallbackQueryHandler,
    filters,
)

# =========================================================
# 2. ENV CONFIG
# =========================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME")
CHANNEL_ID = os.getenv("CHANNEL_ID")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
BASE_URL = os.getenv("BASE_URL")

DOWNLOAD_DIR = "downloads"
COVER_PATH = "cover.jpg"

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# =========================================================
# 3. LOGGING
# =========================================================
logging.basicConfig(
    filename="bot.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# =========================================================
# 4. DATABASE
# =========================================================
conn = sqlite3.connect("users.db", check_same_thread=False)
cur = conn.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)")
conn.commit()

def save_user(user_id: int):
    cur.execute("INSERT OR IGNORE INTO users VALUES (?)", (user_id,))
    conn.commit()

# =========================================================
# 5. UTILITIES
# =========================================================
def clean_filename(name: str) -> str:
    name = re.sub(r'@\w+', '', name)
    name = re.sub(r'\[.*?\]', '', name)
    name = re.sub(r'(www\.)?\w+\.(com|net|ir|org)', '', name, flags=re.I)
    return name.strip() or "music.mp3"

async def run_cmd(*cmd, progress_callback=None):
    """Run subprocess asynchronously with optional progress callback"""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    start_time = datetime.now()
    while True:
        line = await proc.stderr.readline()
        if not line:
            break
        decoded = line.decode(errors="ignore").strip()
        if progress_callback:
            await progress_callback(decoded, start_time)

    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise Exception(f"Command {cmd} failed: {stderr.decode()}")
    return stdout.decode(), stderr.decode()

# =========================================================
# 6. FORCE JOIN
# =========================================================
async def is_member(user_id, context):
    try:
        member = await context.bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ["member", "administrator", "creator"]
    except:
        return False

async def force_join(update, context):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔔 عضویت در کانال", url=f"https://t.me/{CHANNEL_USERNAME}")],
        [InlineKeyboardButton("✅ بررسی عضویت", callback_data="check_join")]
    ])
    await update.message.reply_text("🔒 لطفاً برای استفاده از ربات ابتدا عضو کانال شوید:", reply_markup=kb)

async def check_join_callback(update, context):
    q = update.callback_query
    await q.answer()
    if await is_member(q.from_user.id, context):
        await q.edit_message_text("✅ عضویت شما تایید شد، حالا می‌توانید موزیک ارسال کنید 🎵")
    else:
        await q.answer("❌ هنوز عضو کانال نشده‌اید!", show_alert=True)

# =========================================================
# 7. START
# =========================================================
async def start(update, context):
    save_user(update.message.from_user.id)
    if not await is_member(update.message.from_user.id, context):
        return await force_join(update, context)
    await update.message.reply_text(
        "🎧 لطفاً لینک SoundCloud ارسال کنید یا موزیک خود را فوروارد نمایید.\n\n"
        "📥 خروجی تمیز داخل کانال منتشر خواهد شد."
    )

# =========================================================
# 8. AUDIO PROCESSING QUEUE
# =========================================================
queue = asyncio.Queue()
CONCURRENCY = 3

async def audio_worker(context):
    while True:
        task = await queue.get()
        try:
            await task()
        except Exception as e:
            logging.exception(e)
        queue.task_done()

async def process_audio(raw_path, final_path, original_name, progress_cb=None):
    await run_cmd(
        "ffmpeg",
        "-i", raw_path,
        "-i", COVER_PATH,
        "-map_metadata", "-1",
        "-map", "0:a", "-map", "1:v",
        "-c:a", "libmp3lame",
        "-b:a", "320k",
        "-c:v", "mjpeg",
        "-id3v2_version", "3",
        "-metadata", f"title={original_name}",
        "-metadata", f"artist=@{CHANNEL_USERNAME}",
        "-metadata", f"album=@{CHANNEL_USERNAME}",
        "-metadata", f"comment=@{CHANNEL_USERNAME}",
        final_path,
        progress_callback=progress_cb
    )

async def parse_ffmpeg_progress(line, start_time):
    """Parse ffmpeg stderr and update progress"""
    time_match = re.search(r'time=(\d+:\d+:\d+.\d+)', line)
    if time_match:
        current_time = time_match.group(1)
        # می‌توان زمان تقریبی باقی‌مانده را هم محاسبه کرد
        # (در این نسخه ساده فقط زمان جاری نشان داده می‌شود)
        pass

async def handle_forwarded_audio(update, context):
    save_user(update.message.from_user.id)
    if not await is_member(update.message.from_user.id, context):
        return await force_join(update, context)

    status = await update.message.reply_text("📥 فایل شما دریافت شد، در حال پردازش هستیم...")

    audio = update.message.audio or update.message.document
    original_name = clean_filename(audio.file_name or "music.mp3")

    uid = uuid4().hex
    raw = f"{DOWNLOAD_DIR}/{uid}.mp3"
    final = f"{DOWNLOAD_DIR}/{uid}_final.mp3"

    file = await audio.get_file()
    await file.download_to_drive(raw)

    async def task():
        try:
            await process_audio(raw, final, original_name, parse_ffmpeg_progress)
            caption = f"🎵 {original_name}\n🔗 @{CHANNEL_USERNAME}"
            async with open(final, "rb") as f:
                await context.bot.send_audio(
                    chat_id=CHANNEL_ID,
                    audio=f,
                    filename=original_name,
                    caption=caption
                )
            await status.edit_text("✅ فایل شما با موفقیت منتشر شد 🎉")
        except Exception as e:
            logging.exception(e)
            await status.edit_text("❌ خطا در پردازش فایل شما!")

    await queue.put(task)

SC_REGEX = re.compile(r"(soundcloud\.com\/[^\s]+)")

async def handle_soundcloud(update, context):
    save_user(update.message.from_user.id)
    if not await is_member(update.message.from_user.id, context):
        return await force_join(update, context)

    match = SC_REGEX.search(update.message.text or "")
    if not match:
        return

    status = await update.message.reply_text("⏳ در حال دانلود از SoundCloud، لطفاً کمی صبور باشید...")

    uid = uuid4().hex
    raw = f"{DOWNLOAD_DIR}/{uid}.mp3"
    final = f"{DOWNLOAD_DIR}/{uid}_final.mp3"
    original_name = f"{uid}.mp3"

    async def task():
        try:
            await run_cmd("yt-dlp", "-x", "--audio-format", "mp3", "-o", raw, match.group(1), progress_callback=parse_ffmpeg_progress)
            await process_audio(raw, final, original_name, parse_ffmpeg_progress)
            caption = f"🎵 {original_name}\n🔗 @{CHANNEL_USERNAME}"
            async with open(final, "rb") as f:
                await context.bot.send_audio(
                    chat_id=CHANNEL_ID,
                    audio=f,
                    filename=original_name,
                    caption=caption
                )
            await status.edit_text("✅ دانلود و انتشار موزیک با موفقیت انجام شد 🎉")
        except Exception as e:
            logging.exception(e)
            await status.edit_text("❌ خطا در دانلود یا پردازش فایل!")

    await queue.put(task)

# =========================================================
# 9. BROADCAST
# =========================================================
async def broadcast(update, context):
    if update.message.from_user.id != ADMIN_ID:
        return
    text = " ".join(context.args)
    if not text:
        return await update.message.reply_text("❗ لطفاً متن پیام را وارد کنید.")
    for (uid,) in cur.execute("SELECT user_id FROM users"):
        try:
            await context.bot.send_message(uid, text)
        except:
            pass
    await update.message.reply_text("✅ پیام شما برای همه کاربران ارسال شد.")

# =========================================================
# 10. FALLBACK
# =========================================================
async def fallback(update, context):
    await update.message.reply_text("🎵 لطفاً فقط موزیک یا لینک SoundCloud ارسال کنید.")

# =========================================================
# 11. MAIN
# =========================================================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CallbackQueryHandler(check_join_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_soundcloud))
    app.add_handler(MessageHandler(filters.AUDIO | filters.Document.AUDIO, handle_forwarded_audio))
    app.add_handler(MessageHandler(filters.ALL, fallback))

    # راه‌اندازی workerها قبل از webhook
    async def start_workers(app):
        for _ in range(CONCURRENCY):
            asyncio.create_task(audio_worker(app))

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        webhook_url=BASE_URL,
        post_init=start_workers
    )

if __name__ == "__main__":
    main()
