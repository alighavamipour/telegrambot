import os
import logging
import time
import re
import telebot
from telebot import types
from flask import Flask, request
from config import BOT_TOKEN, CHANNEL_ID, OWNER_ID, REQUIRED_CHANNELS, DOWNLOAD_PATH, DB_PATH
import database, utils
from functools import wraps

# ------------------- Logging -------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------------- Bot -------------------
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
database.init_db()
os.makedirs(DOWNLOAD_PATH, exist_ok=True)

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

# ------------------- Decorator -------------------
def require_membership(func):
    @wraps(func)
    def wrapper(message, *args, **kwargs):
        try:
            if not utils.check_membership(bot, message.from_user.id):
                kb = types.InlineKeyboardMarkup()
                kb.add(types.InlineKeyboardButton("👥 عضویت در کانال", url=f"https://t.me/{CHANNEL_ID.lstrip('@')}"))
                bot.reply_to(message, "❌ برای استفاده از ربات حتماً باید عضو کانال شوید.", reply_markup=kb)
                return
        except Exception as e:
            logger.exception("Membership check failed: %s", e)
            bot.reply_to(message, "❌ خطا در بررسی عضویت. دوباره تلاش کنید.")
            return
        return func(message, *args, **kwargs)
    return wrapper

# ------------------- Helpers -------------------
def get_file_info(message):
    if message.content_type == 'audio':
        file_id = message.audio.file_id
        file_name = message.audio.file_name or message.audio.title or f"audio_{int(time.time())}.mp3"
        media_type = 'audio'
        file_size = getattr(message.audio, 'file_size', None)
    elif message.content_type == 'voice':
        file_id = message.voice.file_id
        file_name = f"voice_{int(time.time())}.ogg"
        media_type = 'audio'
        file_size = getattr(message.voice, 'file_size', None)
    elif message.content_type == 'video':
        file_id = message.video.file_id
        file_name = message.video.file_name or f"video_{int(time.time())}.mp4"
        media_type = 'video'
        file_size = getattr(message.video, 'file_size', None)
    elif message.content_type == 'document':
        file_id = message.document.file_id
        file_name = message.document.file_name or f"file_{int(time.time())}"
        media_type = 'document'
        file_size = getattr(message.document, 'file_size', None)
    else:
        return None, None, None, None
    return file_id, file_name, media_type, file_size

def build_quality_kb(link):
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("720p", callback_data=f"ytq|720|{link}"),
        types.InlineKeyboardButton("480p", callback_data=f"ytq|480|{link}")
    )
    kb.add(
        types.InlineKeyboardButton("360p", callback_data=f"ytq|360|{link}"),
        types.InlineKeyboardButton("Audio", callback_data=f"ytq|audio|{link}")
    )
    return kb

# ------------------- Flask -------------------
app = Flask(__name__)
WEBHOOK_URL = f"{os.getenv('RENDER_EXTERNAL_URL')}/webhook"

@app.route('/')
def home():
    return "Bot is running (Webhook active)."

@app.route('/webhook', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        try:
            update_json = request.get_json(force=True)
            update = telebot.types.Update.de_json(update_json)
            bot.process_new_updates([update])
            return "OK", 200
        except Exception as e:
            logger.exception("Webhook processing failed: %s", e)
            return "Error", 500
    return "Unsupported Media", 403

# ------------------- Handlers -------------------
@bot.message_handler(commands=['start','help'])
def cmd_start(m):
    msg = (
        "سلام! 👋\n📌 قابلیت‌ها:\n"
        "- ارسال فایل، ویدئو، داکیومنت\n"
        "- دانلود از SoundCloud\n"
        "- دانلود از YouTube با انتخاب کیفیت\n⚠️ حتما عضو کانال باشید."
    )
    bot.send_message(m.chat.id, msg)

@bot.message_handler(content_types=['audio','video','document','voice'])
@require_membership
def media_handler(message):
    file_id, file_name, media_type, file_size = get_file_info(message)
    if not file_id:
        bot.reply_to(message, "❌ نوع فایل پشتیبانی نمی‌شود.")
        return
    if file_size and file_size > MAX_FILE_SIZE:
        bot.reply_to(message, f"❌ حجم فایل بیشتر از 50MB است ({file_size/1024/1024:.2f}MB).")
        return
    try:
        finfo = bot.get_file(file_id)
        data = bot.download_file(finfo.file_path)
        safe_name = re.sub(r'[^A-Za-z0-9\.\-_ء-ي ]','_', file_name)
        local_path = os.path.join(DOWNLOAD_PATH, safe_name)
        with open(local_path,'wb') as f: f.write(data)
        bot.reply_to(message, f"✅ فایل دریافت و ذخیره شد: {safe_name}")
    except Exception as e:
        bot.reply_to(message, f"❌ خطا در دانلود فایل: {e}")

@bot.message_handler(func=lambda m: isinstance(m.text,str) and 'soundcloud.com' in m.text.lower())
@require_membership
def sc_handler(message):
    link = message.text.strip()
    try:
        filepath, info = utils.download_with_ytdlp(link, outdir=DOWNLOAD_PATH)
        title = info.get('title','SoundCloud Track')
        bot.reply_to(message, f"✅ فایل {title} دانلود شد.")
    except Exception as e:
        bot.reply_to(message, f"❌ خطا در دانلود SoundCloud: {e}")

@bot.message_handler(func=lambda m: isinstance(m.text,str) and 'youtube.com' in m.text.lower())
@require_membership
def yt_handler(message):
    link = message.text.strip()
    bot.reply_to(message, "🎯 لطفاً کیفیت دلخواه را انتخاب کنید:", reply_markup=build_quality_kb(link))

@bot.callback_query_handler(func=lambda c: c.data.startswith("ytq|"))
def cb_quality(call):
    _,q,link = call.data.split("|",2)
    audio_only = (q=="audio")
    quality = None if audio_only else int(q)
    msg = bot.edit_message_text("⏳ در حال دانلود...", call.message.chat.id, call.message.message_id)
    try:
        filepath, info = utils.ytdlp_download(link, DOWNLOAD_PATH, quality, audio_only)
        title = info.get("title","YouTube Video")
        with open(filepath,'rb') as f:
            if audio_only:
                bot.send_audio(call.message.chat.id,f,caption=title)
            else:
                bot.send_video(call.message.chat.id,f,caption=title)
        bot.edit_message_text("✔ آماده شد!", call.message.chat.id, call.message.message_id)
    except Exception as e:
        bot.edit_message_text("❌ خطا: "+str(e), call.message.chat.id, call.message.message_id)

# ------------------- Webhook Setup -------------------
# ست خودکار وبهوک با هر deploy Render
try:
    bot.remove_webhook()
    bot.set_webhook(WEBHOOK_URL)
    logger.info(f"Webhook set to {WEBHOOK_URL}")
except Exception as e:
    logger.exception("Failed to set webhook: %s", e)
