import os
import asyncio
import logging
import time
import shutil
import uuid
import static_ffmpeg
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import yt_dlp
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, TPE1, TIT2, TALB, COMM, ID3NoHeaderError
from mutagen.flac import FLAC, Picture
from mutagen.mp4 import MP4, MP4Cover

# ----------------- تنظیمات لاگینگ پیشرفته -----------------
logging.basicConfig(
    format='%(asctime)s - [%(levelname)s] - [%(name)s:%(lineno)d] - %(message)s',
    level=logging.DEBUG
)
logger = logging.getLogger(__name__)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.INFO)

static_ffmpeg.add_paths()

# ----------------- تنظیمات متغیرهای محیطی و ثابت‌ها -----------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = "@voxxboxx"            # آیدی کانال شما
COVER_PATH = "cover.jpg"            # تصویر کاور در ریشه پروژه
NUM_WORKERS = 3                     # تعداد پردازش همزمان (ورکرها)

if not BOT_TOKEN:
    logger.critical("❌ BOT_TOKEN در متغیرهای محیطی یافت نشد!")

task_queue = asyncio.Queue()
download_semaphore = asyncio.Semaphore(2)  # حداکثر ۲ دانلود سنگین همزمان جهت حفظ حافظه RAM

# ----------------- وب‌سرور جهت بیدار نگه داشتن ربات -----------------
async def handle_ping(request):
    return web.Response(text="Bot is live & active!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/ping", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"🌐 وب‌سرور فعال شد روی پورت {port}")

# ----------------- توابع کمکی -----------------
def make_progress_bar(percent: float, length: int = 10) -> str:
    filled = int(length * percent / 100)
    bar = '▓' * filled + '░' * (length - filled)
    return f"[{bar}] {percent:.1f}%"

def format_size(size_in_bytes: int) -> str:
    if not size_in_bytes:
        return "نامشخص"
    mb = size_in_bytes / (1024 * 1024)
    return f"{mb:.2f} MB"

def format_duration(seconds: int) -> str:
    if not seconds:
        return "نامشخص"
    mins, secs = divmod(int(seconds), 60)
    return f"{mins:02d}:{secs:02d}"

def clean_old_temp_files():
    """پاکسازی فایل‌های باقی‌مانده از اجراهای قبلی"""
    for item in os.listdir('.'):
        if item.startswith("sc_downloads_") or item.startswith("temp_"):
            try:
                if os.path.isdir(item):
                    shutil.rmtree(item)
                else:
                    os.remove(item)
                logger.info(f"🧹 فایل/پوشه قدیمی پاک شد: {item}")
            except Exception as e:
                logger.warning(f"⚠️ خطا در پاکسازی اولیه: {e}")

# ----------------- ویرایش متادیتا -----------------
def edit_metadata(file_path: str, title: str):
    ext = os.path.splitext(file_path)[1].lower()
    cover_absolute_path = os.path.abspath(COVER_PATH)

    if not os.path.exists(cover_absolute_path):
        logger.error(f"❌ فایل کاور در مسیر {cover_absolute_path} یافت نشد!")
        return False

    try:
        with open(cover_absolute_path, 'rb') as f:
            cover_data = f.read()

        if ext == '.mp3':
            try:
                tags = ID3(file_path)
                tags.delete(file_path)
                tags = ID3()
            except ID3NoHeaderError:
                tags = ID3()

            tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=cover_data))
            tags.add(TIT2(encoding=3, text=f"{title} {CHANNEL_ID}"))
            tags.add(TPE1(encoding=3, text=CHANNEL_ID))
            tags.add(TALB(encoding=3, text=CHANNEL_ID))
            tags.add(COMM(encoding=3, lang='eng', desc='Comment', text=CHANNEL_ID))
            tags.save(file_path, v2_version=3)

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
        logger.error(f"❌ خطا در ویرایش متادیتا: {e}", exc_info=True)
        return False

# ----------------- مدیریت تلاش مجدد (Retry Helper) -----------------
async def run_with_retry(coro_fn, max_retries=3, delay=2):
    for attempt in range(1, max_retries + 1):
        try:
            return await coro_fn()
        except Exception as e:
            logger.warning(f"⚠️ تلاش {attempt} از {max_retries} ناموفق بود: {e}")
            if attempt == max_retries:
                raise e
            await asyncio.sleep(delay)

# ----------------- مدیریت صف (Multi-Worker Queue) -----------------
async def queue_worker(worker_id: int, app: Application):
    logger.info(f"⚙️ Worker-{worker_id} شروع به کار کرد.")
    while True:
        task = await task_queue.get()
        chat_id, status_msg_id, task_type, data = task
        logger.info(f"👷 Worker-{worker_id} در حال انجام وظیفه نوع {task_type}")
        try:
            if task_type == 'audio_file':
                await process_audio_file(app, chat_id, status_msg_id, data, worker_id)
            elif task_type == 'soundcloud_url':
                await process_soundcloud_url(app, chat_id, status_msg_id, data, worker_id)
        except Exception as e:
            error_details = str(e)
            logger.error(f"❌ Worker-{worker_id} - خطای نهایی: {error_details}", exc_info=True)
            err_text = (
                "❌ **خطا در پردازش فایل!**\n\n"
                f"🔻 **جزئیات خطا:**\n`{error_details[:300]}`\n\n"
                "💡 لطفاً مجدداً فایل یا لینک دیگری را ارسال کنید."
            )
            try:
                await app.bot.edit_message_text(err_text, chat_id=chat_id, message_id=status_msg_id, parse_mode="Markdown")
            except Exception:
                pass
        finally:
            task_queue.task_done()

# ----------------- پردازش فایل صوتی تلگرام -----------------
async def process_audio_file(app, chat_id, status_msg_id, doc_obj, worker_id: int):
    file_name = getattr(doc_obj, 'file_name', None) or "music.mp3"
    duration = getattr(doc_obj, 'duration', 0)
    file_size = getattr(doc_obj, 'file_size', 0)

    name_without_ext, ext = os.path.splitext(file_name)
    if not ext:
        ext = ".mp3"

    clean_title = name_without_ext.replace(CHANNEL_ID, "").strip()
    final_title = f"{clean_title} {CHANNEL_ID}"
    
    unique_id = uuid.uuid4().hex[:8]
    new_filename = f"temp_{unique_id}_{final_title}{ext}"

    try:
        await app.bot.edit_message_text(
            f"📥 **در حال دانلود فایل از تلگرام (Worker {worker_id})...**\n\n"
            f"🎵 **نام:** `{clean_title}`\n"
            f"💾 **حجم:** `{format_size(file_size)}`\n"
            f"⏱ **زمان:** `{format_duration(duration)}`",
            chat_id=chat_id, message_id=status_msg_id, parse_mode="Markdown"
        )

        async def download_task():
            file = await app.bot.get_file(doc_obj.file_id)
            await file.download_to_drive(custom_path=new_filename)

        await run_with_retry(download_task, max_retries=3)

        await app.bot.edit_message_text("🎨 **در حال اعمال کاور اختصاصی و ویرایش متادیتا...**", chat_id=chat_id, message_id=status_msg_id, parse_mode="Markdown")
        await asyncio.to_thread(edit_metadata, new_filename, clean_title)

        await app.bot.edit_message_text("📤 **در حال آپلود و انتشار در کانال...**", chat_id=chat_id, message_id=status_msg_id, parse_mode="Markdown")
        caption = (
            f"🎵 **{final_title}**\n\n"
            f"⏱ **زمان:** {format_duration(duration)}\n"
            f"💾 **حجم:** {format_size(file_size)}\n\n"
            f"🆔 {CHANNEL_ID}"
        )

        sent_msg = None
        async def upload_task():
            nonlocal sent_msg
            with open(new_filename, 'rb') as audio_file:
                sent_msg = await app.bot.send_audio(
                    chat_id=CHANNEL_ID,
                    audio=audio_file,
                    caption=caption,
                    title=final_title,
                    performer=CHANNEL_ID,
                    duration=duration,
                    parse_mode="Markdown"
                )

        await run_with_retry(upload_task, max_retries=3)

        channel_username = CHANNEL_ID.replace("@", "")
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🎧 مشاهده در کانال", url=f"https://t.me/{channel_username}/{sent_msg.message_id}"),
                InlineKeyboardButton("🗑 حذف از کانال", callback_data=f"del_{sent_msg.message_id}")
            ]
        ])

        await app.bot.edit_message_text(
            f"✨ **پست با موفقیت در کانال منتشر شد!**\n\n"
            f"🎵 **عنوان:** `{final_title}`\n"
            f"⏱ **زمان:** `{format_duration(duration)}` | 💾 **حجم:** `{format_size(file_size)}`",
            chat_id=chat_id, message_id=status_msg_id, reply_markup=keyboard, parse_mode="Markdown"
        )

    finally:
        if os.path.exists(new_filename):
            os.remove(new_filename)
            logger.info(f"🧹 فایل موقت پاک شد: {new_filename}")

# ----------------- پردازش بهینه‌شده لینک SoundCloud -----------------
async def process_soundcloud_url(app, chat_id, status_msg_id, url, worker_id: int):
    unique_dir = f"sc_downloads_{uuid.uuid4().hex[:8]}"
    os.makedirs(unique_dir, exist_ok=True)

    await app.bot.edit_message_text("🔎 **در حال استخراج اطلاعات از ساندکلود...**", chat_id=chat_id, message_id=status_msg_id, parse_mode="Markdown")

    loop = asyncio.get_running_loop()
    last_update_time = [0]

    def ytdl_hook(d):
        if d['status'] == 'downloading':
            now = time.time()
            if now - last_update_time[0] > 2.0:
                last_update_time[0] = now
                total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                downloaded = d.get('downloaded_bytes', 0)
                percent = (downloaded / total * 100) if total > 0 else 0
                bar = make_progress_bar(percent)
                msg = f"📥 **در حال دریافت موزیک از ساندکلود (Worker {worker_id})...**\n\n{bar}"
                asyncio.run_coroutine_threadsafe(
                    app.bot.edit_message_text(msg, chat_id=chat_id, message_id=status_msg_id, parse_mode="Markdown"),
                    loop
                )

    # کانفیگ فوق‌بهینه‌شده yt_dlp
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': f'{unique_dir}/%(title)s.%(ext)s',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '320',
        }],
        'progress_hooks': [ytdl_hook],
        'quiet': False,
        'no_warnings': True,
        'socket_timeout': 15,
        'source_address': '0.0.0.0',
        'hls_prefer_native': True,
        'concurrent_fragment_downloads': 5,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        },
        'postprocessor_args': [
            '-threads', '2',              # محدود کردن نخ‌های FFmpeg جهت جلوگیری از ۱۰۰٪ شدن CPU
            '-timeout', '15000000'        # تایم‌اوت ۱۵ ثانیه‌ای FFmpeg
        ],
    }

    def download_sc():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            tracks = []
            if 'entries' in info:
                for entry in info['entries']:
                    if entry:
                        tracks.append(entry)
            else:
                tracks.append(info)
            return tracks

    try:
        # مدیریت دانلود با کنترل RAM و تایم‌اوت کلی ۲.۵ دقیقه
        async with download_semaphore:
            tracks_info = await asyncio.wait_for(
                asyncio.to_thread(download_sc),
                timeout=150.0
            )
    except asyncio.TimeoutError:
        if os.path.exists(unique_dir):
            shutil.rmtree(unique_dir)
        raise Exception("⏱ زمان دانلود از ساندکلود به پایان رسید (Timeout). احتمالاً سرور درگیر است.")
    except Exception as e:
        if os.path.exists(unique_dir):
            shutil.rmtree(unique_dir)
        err_msg = str(e)
        if "DRM protected" in err_msg:
            raise Exception("🔒 این ترک دارای قفل کپی‌رایت دیجیتال (DRM) است و اجازه دانلود مستقیم ندارد.")
        raise Exception(f"خطا در دریافت از ساندکلود: {err_msg}")

    total_tracks = len(tracks_info)
    channel_username = CHANNEL_ID.replace("@", "")

    try:
        for idx, track in enumerate(tracks_info, start=1):
            raw_title = track.get('title', 'Track')
            duration = track.get('duration', 0)
            clean_title = raw_title.replace(CHANNEL_ID, "").strip()
            final_title = f"{clean_title} {CHANNEL_ID}"
            
            expected_file = f"{unique_dir}/{raw_title}.mp3"
            if not os.path.exists(expected_file):
                for f in os.listdir(unique_dir):
                    if f.endswith(".mp3"):
                        expected_file = os.path.join(unique_dir, f)
                        break

            if os.path.exists(expected_file):
                file_size = os.path.getsize(expected_file)
                
                await app.bot.edit_message_text(
                    f"🎨 **در حال تنظیم متادیتا و کاور ({idx}/{total_tracks}):**\n`{clean_title}`",
                    chat_id=chat_id, message_id=status_msg_id, parse_mode="Markdown"
                )
                await asyncio.to_thread(edit_metadata, expected_file, clean_title)

                await app.bot.edit_message_text(
                    f"📤 **در حال انتشار در کانال ({idx}/{total_tracks}):**\n`{clean_title}`",
                    chat_id=chat_id, message_id=status_msg_id, parse_mode="Markdown"
                )

                caption = (
                    f"🎶 **{final_title}**\n\n"
                    f"⏱ **زمان:** {format_duration(duration)}\n"
                    f"💾 **حجم:** {format_size(file_size)}\n\n"
                    f"🆔 {CHANNEL_ID}"
                )

                sent_msg = None
                async def upload_sc_task():
                    nonlocal sent_msg
                    with open(expected_file, 'rb') as audio_file:
                        sent_msg = await app.bot.send_audio(
                            chat_id=CHANNEL_ID,
                            audio=audio_file,
                            caption=caption,
                            title=final_title,
                            performer=CHANNEL_ID,
                            duration=int(duration),
                            parse_mode="Markdown"
                        )

                await run_with_retry(upload_sc_task, max_retries=3)

                keyboard = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("🎧 مشاهده در کانال", url=f"https://t.me/{channel_username}/{sent_msg.message_id}"),
                        InlineKeyboardButton("🗑 حذف از کانال", callback_data=f"del_{sent_msg.message_id}")
                    ]
                ])

                await app.bot.send_message(
                    chat_id=chat_id,
                    text=f"✅ **ترک {idx} از {total_tracks} با موفقیت منتشر شد:**\n`{final_title}`",
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )

                os.remove(expected_file)

        await app.bot.edit_message_text("🎉 **تمامی ترک‌های ساندکلود با موفقیت منتشر شدند!**", chat_id=chat_id, message_id=status_msg_id, parse_mode="Markdown")

    finally:
        if os.path.exists(unique_dir):
            shutil.rmtree(unique_dir)
            logger.info(f"🧹 پوشه موقت {unique_dir} کاملاً پاکسازی شد.")

# ----------------- کلیک روی دکمه‌های شیشه‌ای -----------------
async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data.startswith("del_"):
        msg_id_to_delete = int(query.data.split("_")[1])
        try:
            await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=msg_id_to_delete)
            await query.edit_message_text("🗑 **پست مورد نظر با موفقیت از کانال حذف شد.**", parse_mode="Markdown")
        except Exception as e:
            logger.error(f"❌ خطا در حذف پست از کانال: {e}")
            await query.message.reply_text(f"❌ **خطا در حذف پست از کانال!**\nعلاوه بر این ممکن است پست قبلاً پاک شده باشد.\n`{str(e)}`", parse_mode="Markdown")

# ----------------- هندلرهای پیام‌ها -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "✨ **به ربات مدیریت و انتشار موزیک خوش آمدید**\n\n"
        "🔸 **ویژگی‌ها:**\n"
        "• حذف متادیتای قبلی و اعمال کاور اختصاصی کانال\n"
        "• تنظیم آیدی `@voxxboxx` روی نام اثر و متادیتا\n"
        "• پردازش همزمان چند فایل و لینک (موازی)\n"
        "• استخراج موزیک از ساندکلود با الگوریتم بهینه‌شده\n"
        "• نمایش زمان، حجم و ساخت دکمه مدیریت پست\n\n"
        "📥 *کافیست فایل‌های موزیک یا لینک‌های ساندکلود را ارسال کنید.*"
    )
    if update.effective_message:
        await update.effective_message.reply_text(welcome_text, parse_mode="Markdown")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg:
        return

    doc_obj = msg.audio or msg.voice
    if not doc_obj and msg.document:
        if msg.document.mime_type and msg.document.mime_type.startswith("audio/"):
            doc_obj = msg.document
        elif msg.document.file_name and msg.document.file_name.lower().endswith(('.mp3', '.m4a', '.flac', '.wav', '.ogg')):
            doc_obj = msg.document

    if doc_obj:
        file_name = getattr(doc_obj, 'file_name', 'Music_File')
        logger.info(f"🎵 فایل صوتی جدید افزوده‌شده به صف: {file_name}")

        status_msg = await msg.reply_text("📥 **فایل دریافت شد! در صف پردازش قرار گرفت...**", parse_mode="Markdown")
        await task_queue.put((msg.chat_id, status_msg.message_id, 'audio_file', doc_obj))

    elif msg.text and ("soundcloud.com" in msg.text):
        logger.info(f"🔗 لینک ساندکلود جدید افزوده‌شده به صف: {msg.text}")
        status_msg = await msg.reply_text("🔗 **لینک ساندکلود در صف قرار گرفت...**", parse_mode="Markdown")
        await task_queue.put((msg.chat_id, status_msg.message_id, 'soundcloud_url', msg.text))

# ----------------- اجرای اصلی -----------------
async def main():
    clean_old_temp_files()
    await start_web_server()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callback_query))
    app.add_handler(MessageHandler(filters.AUDIO | filters.Document.ALL | filters.VOICE | filters.TEXT, handle_message))

    for i in range(1, NUM_WORKERS + 1):
        asyncio.create_task(queue_worker(i, app))

    logger.info(f"🤖 ربات با {NUM_WORKERS} ورکر همزمان استارت شد...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    await asyncio.Event().wait()

if __name__ == '__main__':
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(main())
