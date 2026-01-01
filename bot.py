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
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise Exception(stderr.decode() or stdout.decode())

def resolve_soundcloud_url(url):
    try:
        r = requests.get(url, allow_redirects=True, timeout=10)
        final_url = r.url
        logging.info(f"[SoundCloud Redirect] {url}  -->  {final_url}")
        return final_url
    except Exception as e:
        logging.warning(f"resolve_soundcloud_url failed: {e}")
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
    await update.message.reply_text(
        "🔔 برای استفاده از خدمات ربات، ابتدا در کانال رسمی عضو شوید.\n"
        "پس از عضویت، روی «بررسی عضویت» بزنید.",
        reply_markup=kb
    )

async def check_join(update, context):
    q = update.callback_query
    await q.answer()
    if await is_member(q.from_user.id, context):
        await q.edit_message_text(
            "✅ عضویت شما با موفقیت تأیید شد.\n"
            "اکنون می‌توانید فایل یا لینک موسیقی ارسال کنید."
        )
    else:
        await q.answer("❌ هنوز عضو کانال نیستید.", show_alert=True)

# ================= START =================
async def start(update, context):
    save_user(update.message.from_user.id)
    if not await is_member(update.message.from_user.id, context):
        return await force_join(update, context)

    await update.message.reply_text(
        "🎵 خوش آمدید!\n"
        "برای دریافت نسخهٔ باکیفیت و کاور‌دار موسیقی، کافیست فایل یا لینک SoundCloud ارسال کنید."
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
                logging.error(f"Worker task error: {e}")
            finally:
                queue.task_done()
    except asyncio.CancelledError:
        # Shutdown تمیز هنگام بسته شدن ربات
        logging.info("Worker task cancelled, shutting down worker.")

# این تابع بعد از آماده شدن Application صدا زده می‌شود
async def start_workers(app: Application):
    for _ in range(CONCURRENCY):
        asyncio.create_task(worker())
    logging.info(f"{CONCURRENCY} workers started.")

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
    ext = (audio.file_name or "").split(".")[-1].lower() if audio.file_name else "mp3"

    msg = await update.message.reply_text(
        f"✨ فایل «{name}.{ext}» با موفقیت دریافت شد.\n"
        "در حال آماده‌سازی برای پردازش…",
        reply_to_message_id=update.message.message_id
    )

    uid = uuid4().hex
    raw = f"{DOWNLOAD_DIR}/{uid}.{ext}"
    final = f"{DOWNLOAD_DIR}/{uid}.mp3"

    async def task():
        try:
            await msg.edit_text("⬇️ در حال دریافت فایل…\nلطفاً چند لحظه صبور باشید.")
            file = await audio.get_file()
            await file.download_to_drive(raw)

            if ext != "mp3":
                await msg.edit_text(
                    "🎼 در حال تبدیل فایل به فرمت MP3 و افزودن کاور اختصاصی…\n"
                    "کیفیت خروجی تضمین‌شده است."
                )
                success = await retry_task(lambda: tag_and_cover(raw, final, name))
                if not success:
                    await msg.edit_text("⚠️ متأسفانه پردازش فایل ناموفق بود.")
                    return
            else:
                # اگر خود فایل mp3 بود، همان را استفاده می‌کنیم
                nonlocal final
                final = raw

            await msg.edit_text("📡 در حال انتقال فایل به کانال…\nفرآیند انتشار در حال انجام است.")
            size = os.path.getsize(final)
            caption = f"🎵 {name}\n🔗 @{CHANNEL_USERNAME}"

            with open(final, "rb") as f:
                if size <= MAX_FILE_SIZE:
                    await context.bot.send_audio(CHANNEL_ID, f, filename=name, caption=caption)
                else:
                    await context.bot.send_document(CHANNEL_ID, f, caption=caption)

            await msg.edit_text("✅ عملیات با موفقیت به پایان رسید.\nفایل شما اکنون در کانال منتشر شده است.")
        except Exception as e:
            logging.error(f"Error processing audio: {e}")
            try:
                await msg.edit_text("❌ خطایی در پردازش فایل رخ داد.")
            except:
                pass

    await queue.put(task)

# ================= LINKS / SOUNDCLOUD =================
SC_REGEX = re.compile(r"https?://(?:on\.)?soundcloud\.com/[^\s]+")
URL_REGEX = re.compile(r"https?://[^\s]+")

async def handle_links(update, context):
    text = update.message.text or ""
    save_user(update.message.from_user.id)

    if not await is_member(update.message.from_user.id, context):
        return await force_join(update, context)

    url_match = SC_REGEX.search(text) or URL_REGEX.search(text)
    if not url_match:
        await update.message.reply_text("⚠️ لینک ارسال‌شده معتبر نیست.")
        return

    url = resolve_soundcloud_url(url_match.group(0))

    msg = await update.message.reply_text(
        "🔍 در حال بررسی و تحلیل لینک SoundCloud…\n"
        "لطفاً چند لحظه صبر کنید.",
        reply_to_message_id=update.message.message_id
    )

    uid = uuid4().hex
    raw = f"{DOWNLOAD_DIR}/{uid}.raw"
    final = f"{DOWNLOAD_DIR}/{uid}.mp3"

    async def task():
        try:
            await msg.edit_text(
                "⏳ در حال استخراج اطلاعات آهنگ…\n"
                "در حال آماده‌سازی برای دانلود."
            )
            title = os.popen(f'yt-dlp --print "%(title)s" "{url}"').read().strip() or "music"

            await msg.edit_text(
                f"⬇️ در حال دانلود آهنگ «{title}»…\n"
                "این مرحله بسته به حجم فایل ممکن است کمی زمان ببرد."
            )
            success = await retry_task(lambda: run_cmd("yt-dlp", "-f", "bestaudio", "-o", raw, url))
            if not success:
                await msg.edit_text("❌ دانلود فایل ناموفق بود.")
                return

            await msg.edit_text(
                "🎧 در حال تبدیل آهنگ و افزودن کاور اختصاصی…\n"
                "لطفاً منتظر بمانید."
            )
            success = await retry_task(lambda: tag_and_cover(raw, final, title))
            if not success:
                await msg.edit_text("⚠️ پردازش فایل ناموفق بود.")
                return

            await msg.edit_text("📡 در حال انتقال فایل به کانال…")
            size = os.path.getsize(final)
            caption = f"🎵 {title}\n🔗 @{CHANNEL_USERNAME}"

            with open(final, "rb") as f:
                if size <= MAX_FILE_SIZE:
                    await context.bot.send_audio(CHANNEL_ID, f, filename=title, caption=caption)
                else:
                    await context.bot.send_document(CHANNEL_ID, f, caption=caption)

            await msg.edit_text("✅ عملیات با موفقیت انجام شد.\nفایل شما اکنون در کانال قرار دارد.")
        except Exception as e:
            logging.error(f"Error processing link: {e}")
            try:
                await msg.edit_text("❌ خطایی در دانلود یا پردازش فایل رخ داد.")
            except:
                pass

    await queue.put(task)

# ================= MAIN =================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(check_join))
    app.add_handler(MessageHandler(filters.AUDIO | filters.Document.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_links))

    # استارت شدن workerها بعد از آماده شدن اپلیکیشن
    app.post_init = start_workers

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        webhook_url=BASE_URL
    )

if __name__ == "__main__":
    main()
