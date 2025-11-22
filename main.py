import os, logging, time, re
import telebot
from telebot import types
from config import BOT_TOKEN, CHANNEL_ID, OWNER_ID, REQUIRED_CHANNELS, DOWNLOAD_PATH, DB_PATH
import database, utils
from functools import wraps
from flask import Flask, request

# ------------------- Logging -------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)
database.init_db()
os.makedirs(DOWNLOAD_PATH, exist_ok=True)

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

# ------------------- Decorator: Require Membership -------------------
def require_membership(func):
    @wraps(func)
    def wrapper(message, *args, **kwargs):
        uid = message.from_user.id
        try:
            if not utils.check_membership(bot, uid):
                kb = types.InlineKeyboardMarkup()
                kb.add(types.InlineKeyboardButton(
                    "👥 عضویت در کانال",
                    url=f"https://t.me/{CHANNEL_ID.lstrip('@')}"
                ))
                bot.reply_to(message, "❌ برای استفاده از ربات حتماً باید عضو کانال شوید.", reply_markup=kb)
                return
        except Exception as e:
            logger.exception("Membership check failed: %s", e)
            bot.reply_to(message, "❌ خطا در بررسی عضویت. دوباره تلاش کنید.")
            return
        return func(message, *args, **kwargs)
    return wrapper

# ------------------- Start / Help -------------------
@bot.message_handler(commands=['start','help'])
def cmd_start(m):
    msg = (
        "سلام! 👋\n"
        "این ربات اختصاصی کانال وکس باکس است.\n\n"
        "📌 قابلیت‌ها:\n"
        "🎵 دانلود و انتشار موسیقی از لینک‌ها (مثل SoundCloud)\n"
        "🎬 ارسال و انتشار ویدئو و فایل‌ها به کانال\n"
        "📥 حتی فایل‌های فوروارد شده نیز قابل انتشار هستند\n"
        "🎥 دانلود ویدئو و موسیقی از یوتیوب با انتخاب کیفیت\n\n"
        "⚠️ حتما عضو کانال باشید تا بتوانید فایل ارسال کنید."
    )
    bot.send_message(m.chat.id, msg)

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

def add_channel_metadata(file_path, channel_name):
    from mutagen.easyid3 import EasyID3
    from mutagen.id3 import ID3NoHeaderError
    try:
        if not file_path.lower().endswith('.mp3'):
            return
        try:
            audio = EasyID3(file_path)
        except ID3NoHeaderError:
            audio = EasyID3()
            audio.save(file_path)
            audio = EasyID3(file_path)
        title = audio.get('title', [os.path.basename(file_path)])[0]
        audio['title'] = title
        audio['artist'] = channel_name
        audio['comments'] = [f"Published via {channel_name}"]
        audio.save(file_path)
    except Exception as e:
        logger.warning("Cannot add metadata to audio file: %s", e)

def extract_soundcloud_link(text):
    pattern = r'(https?://(?:\S+\.)?soundcloud\.com/[^\s]+)'
    match = re.search(pattern, text)
    if match:
        return match.group(1)
    return None

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

# ------------------- Media Handler -------------------
@bot.message_handler(content_types=['audio','video','document','voice'])
@require_membership
def media_handler(message):
    user = message.from_user
    uid = user.id
    database.add_or_update_user(uid, user.first_name or "", user.last_name or "", getattr(user, 'username', '') or "")

    file_id, file_name, media_type, file_size = get_file_info(message)
    if not file_id:
        bot.reply_to(message, "❌ نوع فایل پشتیبانی نمی‌شود.")
        return

    if file_size and file_size > MAX_FILE_SIZE:
        bot.reply_to(message, f"❌ حجم فایل بیشتر از 50MB است ({file_size/1024/1024:.2f}MB) و نمی‌توان آن را پردازش کرد.")
        return

    processing_msg = bot.reply_to(message, "📥 فایل دریافت شد و در حال پردازش است… لطفاً صبر کنید.")

    safe_name = re.sub(r'[^A-Za-z0-9\.\-_ء-ي ]', '_', file_name or f"{media_type}_{int(time.time())}")
    local_path = os.path.join(DOWNLOAD_PATH, safe_name)

    try:
        finfo = bot.get_file(file_id)
        data = bot.download_file(finfo.file_path)
        with open(local_path, 'wb') as f:
            f.write(data)
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"{local_path} not found after download")
    except Exception as e:
        logger.exception("download error: %s", e)
        bot.edit_message_text("❌ خطا در دریافت فایل.", processing_msg.chat.id, processing_msg.message_id)
        return

    if media_type == 'audio':
        local_path = utils.finalize_audio_file(local_path, file_name)
        add_channel_metadata(local_path, CHANNEL_ID)

    caption = f"🎵 {file_name}\n📌 {utils.make_channel_caption(CHANNEL_ID)}"
    database.add_post(local_path, file_id, safe_name, media_type, file_name, utils.user_display_name(user), uid)

    try:
        with open(local_path, 'rb') as fh:
            if media_type == 'audio':
                bot.send_audio(CHANNEL_ID, fh, caption=caption, title=file_name)
            elif media_type == 'video':
                bot.send_video(CHANNEL_ID, fh, caption=caption)
            else:
                bot.send_document(CHANNEL_ID, fh, caption=caption)
        bot.edit_message_text(f"✅ فایل شما با موفقیت در کانال منتشر شد.\n📌 برای مشاهده به کانال مراجعه کنید.", processing_msg.chat.id, processing_msg.message_id)
    except Exception as e:
        logger.exception("post to channel error: %s", e)
        bot.edit_message_text(f"❌ خطا در ارسال به کانال: {e}", processing_msg.chat.id, processing_msg.message_id)

# ------------------- SoundCloud Handler -------------------
@bot.message_handler(func=lambda m: isinstance(m.text, str) and 'soundcloud.com' in m.text.lower())
@require_membership
def sc_handler(message):
    user = message.from_user
    uid = user.id
    database.add_or_update_user(uid, user.first_name or "", user.last_name or "", getattr(user, 'username', '') or "")

    link = extract_soundcloud_link(message.text)
    if not link:
        bot.reply_to(message, "❌ لینک SoundCloud معتبر پیدا نشد.")
        return

    processing_msg = bot.reply_to(message, "📥 لینک دریافت شد و در حال دانلود فایل… لطفاً صبر کنید.")

    try:
        local_path, info = utils.download_with_ytdlp(link, outdir=DOWNLOAD_PATH)
        title = info.get('title', 'SoundCloud Track')
        local_path = utils.finalize_audio_file(local_path, title)
        add_channel_metadata(local_path, CHANNEL_ID)

        caption = f"🎵 {title}\n📌 {utils.make_channel_caption(CHANNEL_ID)}"
        with open(local_path, 'rb') as fh:
            bot.send_audio(CHANNEL_ID, fh, caption=caption, title=title)

        bot.edit_message_text(f"✅ فایل SoundCloud دانلود و در کانال منتشر شد.\n📌 برای مشاهده به کانال مراجعه کنید.", processing_msg.chat.id, processing_msg.message_id)
        database.add_post(local_path, None, os.path.basename(local_path), 'soundcloud', title, utils.user_display_name(user), uid)
    except Exception as e:
        logger.exception("SoundCloud download error: %s", e)
        bot.edit_message_text(f"❌ دانلود ناموفق: {e}", processing_msg.chat.id, processing_msg.message_id)

# ------------------- YouTube Handler -------------------
@bot.message_handler(func=lambda m: m.text and "youtube.com" in m.text.lower())
@require_membership
def yt_handler(message):
    link = message.text.strip()
    bot.reply_to(message,
                 "🎯 لطفاً کیفیت دلخواه را انتخاب کنید:",
                 reply_markup=build_quality_kb(link)
                 )

@bot.callback_query_handler(func=lambda c: c.data.startswith("ytq|"))
def cb_quality(call):
    _,q,link = call.data.split("|",2)
    audio_only = (q=="audio")
    quality = None if audio_only else int(q)

    msg = bot.edit_message_text(
        "⏳ در حال دانلود...",
        call.message.chat.id,
        call.message.message_id
    )

    try:
        filepath, info = utils.ytdlp_download(link, DOWNLOAD_PATH, quality, audio_only)
        dur = info.get("duration", 0)
        thumb = utils.get_thumbnail(info)
        title = info.get("title","YouTube Video")
        
        size = os.path.getsize(filepath)
        if size > MAX_FILE_SIZE:
            bot.edit_message_text("❌ حجم فایل بیشتر از 50MB هست",call.message.chat.id,call.message.message_id)
            return

        cap = f"🎬 {title}\n⏱ {dur//60} دقیقه {dur%60} ثانیه"
        with open(filepath,'rb') as f:
            if audio_only:
                bot.send_audio(call.message.chat.id, f, caption=cap, thumb=thumb)
            else:
                bot.send_video(call.message.chat.id, f, caption=cap, thumb=thumb)
        bot.edit_message_text("✔ آماده شد!",call.message.chat.id,call.message.message_id)
    except Exception as e:
        bot.edit_message_text("❌ خطا: "+str(e),call.message.chat.id,call.message.message_id)

# ------------------- Unknown Message -------------------
@bot.message_handler(func=lambda m: True)
def unknown_message_handler(message):
    bot.reply_to(message,
                 "❌ ربات این پیام را نمی‌شناسد.\n\n"
                 "📌 لطفاً یک فایل صوتی، ویدئو، داکیومنت یا لینک SoundCloud/Youtube ارسال کنید.\n"
                 "برای راهنمایی بیشتر از /help استفاده کنید.")

# ------------------- Webhook -------------------
app = Flask(__name__)
WEBHOOK_URL = f"{os.getenv('RENDER_EXTERNAL_URL')}/webhook"

@app.route('/webhook', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_str = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
        return "OK", 200
    else:
        return "Unsupported Media", 403

@app.route('/')
def home():
    return "Bot is running (Webhook active)."

# ------------------- Set Webhook on start -------------------
try:
    bot.remove_webhook()
except:
    pass
bot.set_webhook(url=WEBHOOK_URL)
