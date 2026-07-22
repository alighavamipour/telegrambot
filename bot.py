import os
import asyncio
import logging
import static_ffmpeg
from aiohttp import web
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import yt_dlp
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, TPE1, COMM, ID3NoHeaderError
from mutagen.flac import FLAC, Picture
from mutagen.mp4 import MP4, MP4Cover

# تنظیمات لوگ
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# ----------------- آماده‌سازی FFmpeg خودکار -----------------
# دانلود و تنظیم خودکار FFmpeg بدون نیاز به دسترسی Root یا داکر
static_ffmpeg.add_paths()

# ----------------- تنظیمات -----------------
BOT_TOKEN = "8527003524:AAFBSHWc3eJ_D6xJEe4IKM9CKCqK_S7bMAc"  # توکن رباتت رو اینجا بذار
CHANNEL_ID = "@voxxboxx"            # آیدی کانال
COVER_PATH = "cover.jpg"            # عکس کاور اختصاصی

task_queue = asyncio.Queue()

# ----------------- وب‌سرور جهت بیدار نگه داشتن ربات -----------------
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
    logging.info(f"Web server started on port {port}")

# ----------------- ویرایش کاور و متادیتا -----------------
def edit_metadata(file_path: str, title: str):
    ext = os.path.splitext(file_path)[1].lower()
    if not os.path.exists(COVER_PATH):
        logging.warning("فایل کاور یافت نشد!")
        return

    with open(COVER_PATH, 'rb') as f:
        cover_data = f.read()

    try:
        if ext == '.mp3':
            try:
                audio = MP3(file_path, ID3=ID3)
            except ID3NoHeaderError:
                audio = MP3(file_path)
                audio.add_tags()
            audio.tags.delall('APIC')
            audio.tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=cover_data))
            audio.tags.add(TPE1(encoding=3, text=CHANNEL_ID))
            audio.tags.add(COMM(encoding=3, lang='eng', desc='Comment', text=CHANNEL_ID))
            audio.save()

        elif ext == '.flac':
            audio = FLAC(file_path)
            image = Picture()
            image.type = 3
            image.mime = 'image/jpeg'
            image.data = cover_data
            audio.clear_pictures()
            audio.add_picture(image)
            audio['artist'] = CHANNEL_ID
            audio['comment'] = CHANNEL_ID
            audio.save()

        elif ext in ['.m4a', '.mp4']:
            audio = MP4(file_path)
            audio['covr'] = [MP4Cover(cover_data, imageformat=MP4Cover.FORMAT_JPEG)]
            audio['\xa9ART'] = CHANNEL_ID
            audio['\xa9cmt'] = CHANNEL_ID
            audio.save()
    except Exception as e:
        logging.error(f"خطا در ویرایش متادیتا: {e}")

# ----------------- مدیریت صف (Queue Worker) -----------------
async def queue_worker(app: Application):
    while True:
        task = await task_queue.get()
        chat_id, status_msg_id, task_type, data = task
        try:
            if task_type == 'audio_file':
                await process_audio_file(app, chat_id, status_msg_id, data)
            elif task_type == 'soundcloud_url':
                await process_soundcloud_url(app, chat_id, status_msg_id, data)
        except Exception as e:
            logging.error(f"خطا در پردازش صف: {e}")
            await app.bot.edit_message_text("❌ متأسفانه در پردازش خطایی رخ داد.", chat_id=chat_id, message_id=status_msg_id)
        finally:
            task_queue.task_done()

# ----------------- پردازش فایل صوتی معمولی -----------------
async def process_audio_file(app, chat_id, status_msg_id, document):
    await app.bot.edit_message_text("⏳ در حال دریافت فایل از تلگرام...", chat_id=chat_id, message_id=status_msg_id)
    file = await app.bot.get_file(document.file_id)
    original_name = document.file_name or "music.mp3"
    name_without_ext, ext = os.path.splitext(original_name)
    
    new_filename = f"{name_without_ext} {CHANNEL_ID}{ext}"
    await file.download_to_drive(new_filename)
    
    await app.bot.edit_message_text("🎨 در حال تعویض کاور و متادیتا...", chat_id=chat_id, message_id=status_msg_id)
    await asyncio.to_thread(edit_metadata, new_filename, name_without_ext)
    
    await app.bot.edit_message_text("📤 در حال ارسال به کانال...", chat_id=chat_id, message_id=status_msg_id)
    caption = f"🎵 {new_filename}\n\n🆔 {CHANNEL_ID}"
    
    with open(new_filename, 'rb') as audio_file:
        await app.bot.send_audio(chat_id=CHANNEL_ID, audio=audio_file, caption=caption, title=name_without_ext, performer=CHANNEL_ID)
    
    if os.path.exists(new_filename):
        os.remove(new_filename)
    await app.bot.edit_message_text("✅ با موفقیت پردازش و در کانال پست شد!", chat_id=chat_id, message_id=status_msg_id)

# ----------------- پردازش لینک ساندکلود -----------------
async def process_soundcloud_url(app, chat_id, status_msg_id, url):
    await app.bot.edit_message_text("🔎 در حال استخراج لینک ساندکلود...", chat_id=chat_id, message_id=status_msg_id)
    
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': f'%(title)s {CHANNEL_ID}.%(ext)s',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '320',
        }],
        'quiet': True
    }
    
    def download_sc():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if 'entries' in info: # آلبوم
                return [(f"{entry.get('title')} {CHANNEL_ID}.mp3", entry.get('title')) for entry in info['entries']]
            else: # تک ترک
                title = info.get('title')
                return [(f"{title} {CHANNEL_ID}.mp3", title)]

    files = await asyncio.to_thread(download_sc)
    
    for filename, title in files:
        if os.path.exists(filename):
            await app.bot.edit_message_text(f"🎨 تنظیم کاور برای: {title}...", chat_id=chat_id, message_id=status_msg_id)
            await asyncio.to_thread(edit_metadata, filename, title)
            
            await app.bot.edit_message_text(f"📤 ارسال به کانال: {title}...", chat_id=chat_id, message_id=status_msg_id)
            caption = f"🎶 {os.path.basename(filename)}\n\n🆔 {CHANNEL_ID}"
            
            with open(filename, 'rb') as audio_file:
                await app.bot.send_audio(chat_id=CHANNEL_ID, audio=audio_file, caption=caption, title=title, performer=CHANNEL_ID)
            os.remove(filename)

    await app.bot.edit_message_text("✅ تمام ترک‌ها با موفقیت پست شدند!", chat_id=chat_id, message_id=status_msg_id)

# ----------------- هندلرهای تلگرام -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "سلام! به ربات مدیریت پست کانال خوش آمدید 👋\n\n"
        "✨ **قابلیت‌ها:**\n"
        "1️⃣ تغییر کاور تمامی فرمت‌ها به کاور اختصاصی کانال\n"
        "2️⃣ اضافه کردن آیدی `@voxxboxx` به انتهای اسم فایل و متادیتا\n"
        "3️⃣ دانلود تک ترک و آلبوم از ساندکلود با اسم و کیفیت اصلی\n"
        "4️⃣ پردازش صفی و سریع بدون هنگ کردن\n\n"
        "📥 **راهنما:** کافیست فایل موزیک یا لینک ساندکلود را بفرستید."
    )
    await update.message.reply_text(welcome_text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.audio or msg.document:
        doc = msg.audio or msg.document
        status_msg = await msg.reply_text("📥 فایل دریافت شد! در صف پردازش قرار گرفت...")
        await task_queue.put((msg.chat_id, status_msg.message_id, 'audio_file', doc))
    elif msg.text and ("soundcloud.com" in msg.text):
        status_msg = await msg.reply_text("🔗 لینک ساندکلود دریافت شد! در صف قرار گرفت...")
        await task_queue.put((msg.chat_id, status_msg.message_id, 'soundcloud_url', msg.text))

# ----------------- اجرا -----------------
async def main():
    await start_web_server()
    
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.AUDIO | filters.Document.ALL | filters.TEXT, handle_message))
    
    asyncio.create_task(queue_worker(app))
    
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
    await asyncio.Event().wait()

if __name__ == '__main__':
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(main())
