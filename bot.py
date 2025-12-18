# =========================================================
# 1. IMPORTS
# =========================================================
import os
import re
import sqlite3
import logging
import subprocess
from uuid import uuid4

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    CallbackQueryHandler,
    filters,
)

# =========================================================
# 2. ENV CONFIG (FROM RENDER)
# =========================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME")   # example: mychannel
CHANNEL_ID = os.getenv("CHANNEL_ID")               # example: -100123456789
ADMIN_ID = int(os.getenv("ADMIN_ID"))

BASE_URL = os.getenv("BASE_URL")  # render webhook url

DOWNLOAD_DIR = "downloads"
COVER_PATH = "cover.jpg"  # کاور آماده‌ای که گفتی

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
# 4. DATABASE (USERS)
# =========================================================
conn = sqlite3.connect("users.db", check_same_thread=False)
cur = conn.cursor()
cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY
)
""")
conn.commit()

def save_user(user_id: int):
    cur.execute("INSERT OR IGNORE INTO users VALUES (?)", (user_id,))
    conn.commit()

# =========================================================
# 5. FORCE JOIN CHECK
# =========================================================
async def is_member(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ["member", "administrator", "creator"]
    except:
        return False

async def force_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔔 عضویت در کانال", url=f"https://t.me/{CHANNEL_USERNAME}")],
        [InlineKeyboardButton("✅ بررسی عضویت", callback_data="check_join")]
    ])
    await update.message.reply_text(
        "🔒 برای استفاده از ربات باید عضو کانال باشید",
        reply_markup=keyboard
    )

async def check_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if await is_member(query.from_user.id, context):
        await query.edit_message_text("✅ عضویت تایید شد، حالا می‌تونی استفاده کنی 🎵")
    else:
        await query.answer("❌ هنوز عضو کانال نیستی", show_alert=True)

# =========================================================
# 6. START & HELP
# =========================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    save_user(user_id)

    if not await is_member(user_id, context):
        return await force_join(update, context)

    await update.message.reply_text(
        "🎧 سلام!\n\n"
        "🔹 لینک SoundCloud بفرست\n"
        "🔹 یا موزیک رو فوروارد کن\n\n"
        "🎵 موزیک به‌صورت اختصاصی داخل کانال منتشر میشه"
    )

# =========================================================
# 7. SOUNDCLOUD LINK HANDLER
# =========================================================
SC_REGEX = re.compile(r"(soundcloud\.com\/[^\s]+)")

async def handle_soundcloud(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    save_user(user_id)

    if not await is_member(user_id, context):
        return await force_join(update, context)

    text = update.message.text
    match = SC_REGEX.search(text)
    if not match:
        return

    status = await update.message.reply_text("⏳ لینک دریافت شد، در حال دانلود...")

    try:
        uid = uuid4().hex
        raw_path = f"{DOWNLOAD_DIR}/{uid}.mp3"
        final_path = f"{DOWNLOAD_DIR}/{uid}_final.mp3"

        subprocess.run([
            "yt-dlp",
            "-x", "--audio-format", "mp3",
            "-o", raw_path,
            match.group(1)
        ], check=True)

        subprocess.run([
            "ffmpeg",
            "-i", raw_path,
            "-i", COVER_PATH,
            "-map", "0:a",
            "-map", "1:v",
            "-metadata", f"artist=@{CHANNEL_USERNAME}",
            "-metadata", f"title=SoundCloud Track",
            "-metadata", f"album=@{CHANNEL_USERNAME}",
            "-metadata", f"comment=@{CHANNEL_USERNAME}",
            "-metadata", f"copyright=@{CHANNEL_USERNAME}",
            "-c", "copy",
            final_path
        ], check=True)

        caption = (
            "🎵 موزیک جدید\n\n"
            f"🔗 @{CHANNEL_USERNAME}"
        )

        await context.bot.send_audio(
            chat_id=CHANNEL_ID,
            audio=open(final_path, "rb"),
            caption=caption
        )

        await status.edit_text("✅ موزیک منتشر شد!\n📥 برو کانال دانلود کن")

    except Exception as e:
        logging.exception(e)
        await status.edit_text("❌ خطا در پردازش موزیک")

# =========================================================
# 8. FORWARDED MUSIC HANDLER
# =========================================================
async def handle_forwarded_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    save_user(user_id)

    if not await is_member(user_id, context):
        return await force_join(update, context)

    status = await update.message.reply_text("🎧 فایل دریافت شد...")

    try:
        audio = update.message.audio or update.message.document
        file = await audio.get_file()

        uid = uuid4().hex
        raw_path = f"{DOWNLOAD_DIR}/{uid}.mp3"
        final_path = f"{DOWNLOAD_DIR}/{uid}_final.mp3"

        await file.download_to_drive(raw_path)

        subprocess.run([
            "ffmpeg",
            "-i", raw_path,
            "-i", COVER_PATH,
            "-map", "0:a",
            "-map", "1:v",
            "-metadata", f"artist=@{CHANNEL_USERNAME}",
            "-metadata", f"title=Exclusive Track",
            "-metadata", f"album=@{CHANNEL_USERNAME}",
            "-metadata", f"comment=@{CHANNEL_USERNAME}",
            "-c", "copy",
            final_path
        ], check=True)

        await context.bot.send_audio(
            chat_id=CHANNEL_ID,
            audio=open(final_path, "rb"),
            caption=f"🎵 موزیک اختصاصی\n\n@{CHANNEL_USERNAME}"
        )

        await status.edit_text("✅ با موفقیت منتشر شد")

    except Exception as e:
        logging.exception(e)
        await status.edit_text("❌ خطا در پردازش فایل")

# =========================================================
# 9. BROADCAST (ADMIN)
# =========================================================
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID:
        return

    text = " ".join(context.args)
    if not text:
        return await update.message.reply_text("❗ متن برودکست رو بنویس")

    cur.execute("SELECT user_id FROM users")
    users = cur.fetchall()

    sent = 0
    for (uid,) in users:
        try:
            await context.bot.send_message(uid, text)
            sent += 1
        except:
            pass

    await update.message.reply_text(f"✅ ارسال شد به {sent} کاربر")

# =========================================================
# 10. FALLBACK
# =========================================================
async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 فقط موزیک یا لینک SoundCloud بفرست 🎵")

# =========================================================
# 11. MAIN (WEBHOOK)
# =========================================================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CallbackQueryHandler(check_join_callback, pattern="check_join"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_soundcloud))
    app.add_handler(MessageHandler(filters.AUDIO | filters.Document.AUDIO, handle_forwarded_audio))

    app.add_handler(MessageHandler(filters.ALL, fallback))

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 8443)),
        webhook_url=BASE_URL
    )

if __name__ == "__main__":
    main()
