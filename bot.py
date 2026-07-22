import os
import asyncio
import logging
import time
import static_ffmpeg
from aiohttp import web
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import yt_dlp
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, TPE1, TIT2, TALB, COMM, ID3NoHeaderError
from mutagen.flac import FLAC, Picture
from mutagen.mp4 import MP4, MP4Cover

# ----------------- تنظیمات لاگینگ پیشرفته (Trace کامل) -----------------
logging.basicConfig(
    format='%(asctime)s - [%(levelname)s] - [%(name)s:%(lineno)d] - %(message)s',
    level=logging.DEBUG
)
logger = logging.getLogger(__name__)

# کاهش لاگ‌های تکراری و شلوغ httpx و telegram
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.INFO)

# راه‌اندازی FFmpeg
static_ffmpeg.add_paths()

# ----------------- تنظیمات متغیرهای محیطی و ثابت‌ها -----------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = "@voxxboxx"            # آیدی کانال شما
COVER_PATH = "cover.jpg"            # نام فایل تصویر کاور در ریشه پروژه

if not BOT_TOKEN:
    logger.critical("❌ BOT_TOKEN در متغیرهای محیطی (Environment Variables) تعریف نشده است!")

task_queue = asyncio.Queue()

# ----------------- وب‌سرور جهت بیدار نگه داشتن ربات در Render -----------------
async def handle_ping(request):
    return web.Response(text="Bot is running active!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/ping", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"🌐 Web server live on port {port}")

# ----------------- ساخت نوار پیشرفت (Progress Bar) -----------------
def make_progress_bar(percent: float, length: int = 10) -> str:
    filled = int(length * percent / 100)
    bar = '▓' * filled + '░' * (length - filled)
    return f"[{bar}] {percent:.1f}%"

# ----------------- پاکسازی کامل و ویرایش متادیتا -----------------
def edit_metadata(file_path: str, title: str):
    ext = os.path.splitext(file_path)[1].lower()
    cover_absolute_path = os.path.abspath(COVER_PATH)
    logger.debug(f"🎨 Starting metadata edit for file: {file_path}")
    logger.debug(f"🖼️ Checking cover image path: {cover_absolute_path}")

    if not os.path.exists(cover_absolute_path):
        logger.error(f"❌ Cover file NOT found at path: {cover_absolute_path}! Check repository root.")
        return False

    try:
        with open(cover_absolute_path, 'rb') as f:
            cover_data = f.read()
        logger.debug(f"📸 Cover file loaded successfully ({len(cover_data)} bytes).")

        if ext == '.mp3':
            try:
                audio = MP3(file_path, ID3=ID3)
            except ID3NoHeaderError:
                logger.debug("No ID3 header found, creating new one...")
                audio = MP3(file_path)
                audio.add_tags()

            # حذف تمام تگ‌های قبلی
            if audio.tags:
                audio.tags.delete(file_path)
            audio.add_tags()

            # افزودن کاور و تگ‌های اختصاصی
            audio.tags.add(
                APIC(
                    encoding=3,      # UTF-8
                    mime='image/jpeg',
                    type=3,          # Front Cover
                    desc='Cover',
                    data=cover_data
                )
            )
            audio.tags.add(TIT2(encoding=3, text=f"{title} {CHANNEL_ID}"))
            audio.tags.add(TPE1(encoding=3, text=CHANNEL_ID))
            audio.tags.add(TALB(encoding=3, text=CHANNEL_ID))
            audio.tags.add(COMM(encoding=3, lang='eng', desc='Comment', text=CHANNEL_ID))
            
            # ذخیره با استاندارد ID3v2.3 جهت نمایش درست در تلگرام و تمام پلیرها
            audio.save(v2_version=3)
            logger.info(f"✅ ID3v2.3 tags and cover successfully saved to: {file_path}")

        elif ext == '.flac':
            audio = FLAC(file_path)
            audio.clear()
            image = Picture()
            image.type = 3
            image.mime = 'image/jpeg'
            image.data = cover_data
            audio.add_picture(image)
            audio['title'] = f"{title} {CHANNEL_ID}"
            audio['artist'] = CHANNEL_ID
            audio['album'] = CHANNEL_ID
            audio['comment'] = CHANNEL_ID
            audio.save()

        elif ext in ['.m4a', '.mp4']:
            audio = MP4(file_path)
            audio.delete()
            audio['covr'] = [MP4Cover(cover_data, imageformat=MP4Cover.FORMAT_JPEG)]
            audio['\xa9nam'] = f"{title} {CHANNEL_ID}"
            audio['\xa9ART'] = CHANNEL_ID
            audio['\xa9alb'] = CHANNEL_ID
            audio['\xa9cmt'] = CHANNEL_ID
            audio.save()

        return True
    except Exception as e:
        logger.error(f"❌ Critical exception in edit_metadata: {e}", exc_info=True)
        return False

# ----------------- مدیریت صف (Queue Worker) -----------------
async def queue_worker(app: Application):
    logger.info("⚙️ Queue worker thread running.")
    while True:
        task = await task_queue.get()
        chat_id, status_msg_id, task_type, data = task
        logger.debug(f"🔄 Processing queue item | Type: {task_type} | Chat: {chat_id}")
        try:
            if task_type == 'audio_file':
                await process_audio_file(app, chat_id, status_msg_id, data)
            elif task_type == 'soundcloud_url':
                await process_soundcloud_url(app, chat_id, status_msg_id, data)
        except Exception as e:
            logger.error(f"❌ Worker exception for task {task_type}: {e}", exc_info=True)
            try:
                await app.bot.edit_message_text("❌ خطا در پردازش فایل. لطفاً مجدداً تلاش کنید.", chat_id=chat_id, message_id=status_msg_id)
            except Exception:
                pass
        finally:
            task_queue.task_done()

# ----------------- پردازش فایل صوتی تلگرام (پشتیبانی از فوروارد) -----------------
async def process_audio_file(app, chat_id, status_msg_id, doc_obj):
    file_name = getattr(doc_obj, 'file_name', None) or "music.mp3"
    logger.info(f"📥 Processing Telegram media/forwarded file: {file_name}")

    file = await app.bot.get_file(doc_obj.file_id)
    name_without_ext, ext = os.path.splitext(file_name)
    if not ext:
        ext = ".mp3"

    # پاکسازی اسم فایل از آیدی‌های احتمالی
    clean_title = name_without_ext.replace(CHANNEL_ID, "").strip()
    final_title = f"{clean_title} {CHANNEL_ID}"
    new_filename = f"{final_title}{ext}"

    await app.bot.edit_message_text("⏳ در حال دانلود فایل از تلگرام...", chat_id=chat_id, message_id=status_msg_id)
    
    # دانلود فایل (سازگار با نسخه python-telegram-bot v20+)
    await file.download_to_drive(custom_path=new_filename)
    logger.info(f"💾 Saved downloaded media to: {new_filename}")

    await app.bot.edit_message_text("🎨 در حال پاکسازی تگ‌های قدیمی و تنظیم کاور جدید...", chat_id=chat_id, message_id=status_msg_id)
    await asyncio.to_thread(edit_metadata, new_filename, clean_title)

    await app.bot.edit_message_text("📤 در حال ارسال به کانال...", chat_id=chat_id, message_id=status_msg_id)
    caption = f"🎵 {final_title}\n\n🆔 {CHANNEL_ID}"

    with open(new_filename, 'rb') as audio_file:
        await app.bot.send_audio(
            chat_id=CHANNEL_ID,
            audio=audio_file,
            caption=caption,
            title=final_title,
            performer=CHANNEL_ID
        )

    if os.path.exists(new_filename):
        os.remove(new_filename)
    
    logger.info(f"✅ Successfully processed and posted: {final_title}")
    await app.bot.edit_message_text("✅ فایل با موفقیت پردازش و در کانال منتشر شد!", chat_id=chat_id, message_id=status_msg_id)

# ----------------- پردازش لینک SoundCloud -----------------
async def process_soundcloud_url(app, chat_id, status_msg_id, url):
    logger.info(f"🔗 Processing SoundCloud link: {url}")
    await app.bot.edit_message_text("🔎 در حال آنالیز لینک ساندکلود...", chat_id=chat_id, message_id=status_msg_id)

    loop = asyncio.get_running_loop()
    last_update_time = [0]

    def ytdl_hook(d):
        if d['status'] == 'downloading':
            now = time.time()
            if now - last_update_time[0] > 2.5:
                last_update_time[0] = now
                total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                downloaded = d.get('downloaded_bytes', 0)
                percent = (downloaded / total * 100) if total > 0 else 0
                bar = make_progress_bar(percent)
                msg = f"⏳ در حال دانلود از ساندکلود...\n\n{bar}"
                asyncio.run_coroutine_threadsafe(
                    app.bot.edit_message_text(msg, chat_id=chat_id, message_id=status_msg_id),
                    loop
                )

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': 'sc_downloads/%(title)s.%(ext)s',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '320',
        }],
        'progress_hooks': [ytdl_hook],
        'quiet': True,
    }

    def download_sc():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            tracks = []
            if 'entries' in info:
                for entry in info['entries']:
                    if entry:
                        tracks.append(entry.get('title', 'Track'))
            else:
                tracks.append(info.get('title', 'Track'))
            return tracks

    try:
        titles = await asyncio.to_thread(download_sc)
    except Exception as e:
        logger.error(f"❌ SoundCloud error: {e}", exc_info=True)
        await app.bot.edit_message_text(f"❌ خطا در دانلود از ساندکلود: {e}", chat_id=chat_id, message_id=status_msg_id)
        return

    total_tracks = len(titles)
    for idx, raw_title in enumerate(titles, start=1):
        clean_title = raw_title.replace(CHANNEL_ID, "").strip()
        final_title = f"{clean_title} {CHANNEL_ID}"
        
        expected_file = f"sc_downloads/{raw_title}.mp3"
        if not os.path.exists(expected_file):
            for f in os.listdir("sc_downloads"):
                if f.endswith(".mp3"):
                    expected_file = os.path.join("sc_downloads", f)
                    break

        if os.path.exists(expected_file):
            await app.bot.edit_message_text(f"🎨 ویرایش متادیتا ({idx}/{total_tracks}):\n{clean_title}", chat_id=chat_id, message_id=status_msg_id)
            await asyncio.to_thread(edit_metadata, expected_file, clean_title)

            await app.bot.edit_message_text(f"📤 ارسال به کانال ({idx}/{total_tracks}):\n{clean_title}", chat_id=chat_id, message_id=status_msg_id)

            caption = f"🎶 {final_title}\n\n🆔 {CHANNEL_ID}"
            with open(expected_file, 'rb') as audio_file:
                await app.bot.send_audio(
                    chat_id=CHANNEL_ID,
                    audio=audio_file,
                    caption=caption,
                    title=final_title,
                    performer=CHANNEL_ID
                )
            os.remove(expected_file)

    await app.bot.edit_message_text("✅ تمام ترک‌های ساندکلود با موفقیت منتشر شدند!", chat_id=chat_id, message_id=status_msg_id)

# ----------------- هندلرهای پیام‌ها -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "سلام! به ربات پست‌گذار کانال خوش آمدید 👋\n\n"
        "✨ **امکانات:**\n"
        "• حذف کاور و آیدی کانال‌های دیگر و جایگذاری کاور اختصاصی\n"
        "• تنظیم آیدی `@voxxboxx` روی فایل و متادیتا\n"
        "• پشتیبانی از لینک ساندکلود (تک‌ترک و آلبوم)\n"
        "• پشتیبانی کامل از فایل‌های مستقیم و فورواردی\n\n"
        "📥 کافیست فایل موزیک (یا فوروارد) و یا لینک ساندکلود را بفرستید."
    )
    if update.effective_message:
        await update.effective_message.reply_text(welcome_text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg:
        return

    user = update.effective_user
    user_info = f"{user.id} (@{user.username})" if user else "Unknown User"
    logger.debug(f"📩 Incoming message | User: {user_info} | Chat ID: {msg.chat_id}")

    doc_obj = msg.audio or msg.document or msg.voice

    if doc_obj:
        file_name = getattr(doc_obj, 'file_name', 'Voice/Media')
        mime_type = getattr(doc_obj, 'mime_type', 'Unknown')
        logger.info(f"🎵 Audio/Document received | Name: {file_name} | MIME: {mime_type} | Size: {doc_obj.file_size} bytes")

        status_msg = await msg.reply_text("📥 فایل دریافت شد! در صف پردازش قرار گرفت...")
        await task_queue.put((msg.chat_id, status_msg.message_id, 'audio_file', doc_obj))

    elif msg.text and ("soundcloud.com" in msg.text):
        logger.info(f"🔗 SoundCloud URL received: {msg.text}")
        status_msg = await msg.reply_text("🔗 لینک ساندکلود دریافت شد! در صف قرار گرفت...")
        await task_queue.put((msg.chat_id, status_msg.message_id, 'soundcloud_url', msg.text))
    else:
        logger.debug(f"ℹ️ Received non-audio message text: {msg.text}")

# ----------------- اجرای اصلی -----------------
async def main():
    if not os.path.exists("sc_downloads"):
        os.makedirs("sc_downloads")

    await start_web_server()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.AUDIO | filters.Document.ALL | filters.VOICE | filters.TEXT, handle_message))

    asyncio.create_task(queue_worker(app))

    logger.info("🤖 Bot polling started...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    await asyncio.Event().wait()

if __name__ == '__main__':
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(main())
