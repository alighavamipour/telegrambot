import os, logging, re, time
from flask import Flask, request
import telebot
from telebot import types
from functools import wraps
from config import BOT_TOKEN, CHANNEL_ID, REQUIRED_CHANNELS, DOWNLOAD_PATH, DB_PATH
import database, utils

# ------------------- Logging -------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)
database.init_db()
os.makedirs(DOWNLOAD_PATH, exist_ok=True)
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

# ------------------- Membership Decorator -------------------
def require_membership(func):
    @wraps(func)
    def wrapper(message, *args, **kwargs):
        try:
            if not utils.check_membership(bot, message.from_user.id):
                kb = types.InlineKeyboardMarkup()
                kb.add(types.InlineKeyboardButton("👥 عضویت در کانال",
                                                  url=f"https://t.me/{CHANNEL_ID.lstrip('@')}"))
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
        f = message.audio
        return f.file_id, f.file_name or f.title or f"audio_{int(time.time())}.mp3", 'audio', getattr(f, 'file_size', None)
    elif message.content_type == 'voice':
        f = message.voice
        return f.file_id, f"voice_{int(time.time())}.ogg", 'audio', getattr(f, 'file_size', None)
    elif message.content_type == 'video':
        f = message.video
        return f.file_id, f.file_name or f"video_{int(time.time())}.mp4", 'video', getattr(f, 'file_size', None)
    elif message.content_type == 'document':
        f = message.document
        return f.file_id, f.file_name or f"file_{int(time.time())}", 'document', getattr(f, 'file_size', None)
    return None, None, None, None

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

def extract_soundcloud_link(text):
    pattern = r'(https?://(?:\S+\.)?soundcloud\.com/[^\s]+)'
    match = re.search(pattern, text)
    return match.group(1) if match else None

# ------------------- Handlers -------------------
@bot.message_handler(commands=['start','help'])
def cmd_start(m):
    msg = ("سلام! 👋\n"
           "📌 قابلیت‌ها:\n"
           "- ارسال فایل، ویدئو، داکیومنت\n"
           "- دانلود از SoundCloud\n"
           "- دانلود از YouTube با انتخاب کیفیت\n⚠️ حتما عضو کانال باشید.")
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
    processing_msg = bot.reply_to(message, "📥 فایل دریافت شد و در حال پردازش است…")
    safe_name = re.sub(r'[^A-Za-z0-9\.\-_ء-ي ]','_', file_name)
    local_path = os.path.join(DOWNLOAD_PATH, safe_name)
    try:
        finfo = bot.get_file(file_id)
        data = bot.download_file(finfo.file_path)
        with open(local_path,'wb') as f: f.write(data)
    except Exception as e:
        logger.exception("download error: %s", e)
        bot.edit_message_text("❌ خطا در دریافت فایل.", processing_msg.chat.id, processing_msg.message_id)
        return
    if media_type == 'audio':
        local_path = utils.finalize_audio_file(local_path, file_name)
    caption = f"{file_name}\n{utils.make_channel_caption(CHANNEL_ID)}"
    with open(local_path,'rb') as fh:
        if media_type == 'audio':
            bot.send_audio(CHANNEL_ID, fh, caption=caption, title=file_name)
        elif media_type == 'video':
            bot.send_video(CHANNEL_ID, fh, caption=caption)
        else:
            bot.send_document(CHANNEL_ID, fh, caption=caption)
    bot.edit_message_text("✅ فایل شما با موفقیت در کانال منتشر شد.", processing_msg.chat.id, processing_msg.message_id)

@bot.message_handler(func=lambda m: isinstance(m.text,str) and 'soundcloud.com' in m.text.lower())
@require_membership
def sc_handler(message):
    link = extract_soundcloud_link(message.text)
    if not link:
        bot.reply_to(message, "❌ لینک SoundCloud معتبر پیدا نشد.")
        return
    processing_msg = bot.reply_to(message, "📥 لینک دریافت شد و در حال دانلود فایل…")
    try:
        local_path, info = utils.ytdlp_download(link, DOWNLOAD_PATH, audio_only=True)
        local_path = utils.finalize_audio_file(local_path, info.get('title'))
        caption = f"{info.get('title','SoundCloud Track')}\n{utils.make_channel_caption(CHANNEL_ID)}"
        with open(local_path,'rb') as fh:
            bot.send_audio(CHANNEL_ID, fh, caption=caption, title=info.get('title'))
        bot.edit_message_text("✅ فایل SoundCloud دانلود و منتشر شد.", processing_msg.chat.id, processing_msg.message_id)
    except Exception as e:
        logger.exception("SoundCloud download error: %s", e)
        bot.edit_message_text(f"❌ دانلود ناموفق: {e}", processing_msg.chat.id, processing_msg.message_id)

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
    processing_msg = bot.edit_message_text("⏳ در حال دانلود...", call.message.chat.id, call.message.message_id)
    try:
        filepath, info = utils.ytdlp_download(link, DOWNLOAD_PATH, quality, audio_only)
        with open(filepath,'rb') as f:
            if audio_only:
                bot.send_audio(call.message.chat.id,f,caption=info.get("title","YouTube Video"))
            else:
                bot.send_video(call.message.chat.id,f,caption=info.get("title","YouTube Video"))
        bot.edit_message_text("✔ آماده شد!", call.message.chat.id, call.message.message_id)
    except Exception as e:
        logger.exception("YouTube download error: %s", e)
        bot.edit_message_text("❌ خطا: "+str(e), call.message.chat.id, call.message.message_id)

# ------------------- Flask Webhook -------------------
app = Flask(__name__)
WEBHOOK_URL = f"https://{os.getenv('RENDER_EXTERNAL_URL').replace('https://','')}/webhook"

@app.route('/webhook', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        update = telebot.types.Update.de_json(request.get_data().decode('utf-8'))
        bot.process_new_updates([update])
        return "OK", 200
    return "Unsupported Media", 403

@app.route('/')
def home():
    return "Bot is running (Webhook active)."

if __name__ == '__main__':
    try:
        bot.remove_webhook()
    except:
        pass
    bot.set_webhook(WEBHOOK_URL)
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT',5000)))
