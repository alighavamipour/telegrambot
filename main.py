import os, logging, time, re
import telebot
from telebot import types
from config import BOT_TOKEN, CHANNEL_ID, OWNER_ID, REQUIRED_CHANNELS, DOWNLOAD_PATH, DB_PATH
import database, utils
from functools import wraps
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3NoHeaderError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)
database.init_db()
os.makedirs(DOWNLOAD_PATH, exist_ok=True)

# ------------------- DECORATOR: REQUIRE MEMBERSHIP -------------------
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

# ------------------- START / HELP -------------------
@bot.message_handler(commands=['start','help'])
def cmd_start(m):
    msg = (
        "سلام! 👋\n"
        "این ربات اختصاصی کانال وکس باکس است.\n\n"
        "📌 قابلیت‌ها:\n"
        "🎵 دانلود و انتشار موسیقی از لینک‌ها (مثل SoundCloud)\n"
        "🎬 ارسال و انتشار ویدئو و فایل‌ها به کانال\n"
        "📥 حتی فایل‌های فوروارد شده نیز قابل انتشار هستند\n\n"
        "⚠️ حتما عضو کانال باشید تا بتوانید فایل ارسال کنید."
    )
    bot.send_message(m.chat.id, msg)

# ------------------- HELPERS -------------------
def get_file_info(message):
    """شناسایی file_id و file_name و media_type برای همه نوع فایل"""
    if message.content_type == 'audio':
        file_id = message.audio.file_id
        file_name = message.audio.file_name or message.audio.title or f"audio_{int(time.time())}.mp3"
        media_type = 'audio'
    elif message.content_type == 'voice':
        file_id = message.voice.file_id
        file_name = f"voice_{int(time.time())}.ogg"
        media_type = 'audio'
    elif message.content_type == 'video':
        file_id = message.video.file_id
        file_name = message.video.file_name or f"video_{int(time.time())}.mp4"
        media_type = 'video'
    elif message.content_type == 'document':
        file_id = message.document.file_id
        file_name = message.document.file_name or f"file_{int(time.time())}"
        media_type = 'document'
    else:
        return None, None, None
    return file_id, file_name, media_type

def add_channel_metadata(file_path, channel_name):
    """اضافه کردن اطلاعات کانال به متادیتای فایل صوتی"""
    try:
        try:
            audio = EasyID3(file_path)
        except ID3NoHeaderError:
            audio = EasyID3()
            audio.save(file_path)
            audio = EasyID3(file_path)

        title = audio.get('title', [os.path.basename(file_path)])[0]
        audio['title'] = title
        audio['artist'] = channel_name
        audio['comment'] = f"Published via {channel_name}"
        audio.save(file_path)
    except Exception as e:
        logger.warning("Cannot add metadata to audio file: %s", e)

# ------------------- MEDIA HANDLER -------------------
@bot.message_handler(content_types=['audio','video','document','voice'])
@require_membership
def media_handler(message):
    user = message.from_user
    uid = user.id
    database.add_or_update_user(uid, user.first_name or "", user.last_name or "", getattr(user, 'username', '') or "")

    file_id, file_name, media_type = get_file_info(message)
    if not file_id:
        bot.reply_to(message, "❌ نوع فایل پشتیبانی نمی‌شود.")
        return

    # download locally
    try:
        finfo = bot.get_file(file_id)
        data = bot.download_file(finfo.file_path)
        safe_name = re.sub(r'[^A-Za-z0-9\.\-_ء-ي ]', '_', file_name)
        local_path = os.path.join(DOWNLOAD_PATH, safe_name)
        with open(local_path, 'wb') as f:
            f.write(data)
    except Exception as e:
        logger.exception("download error: %s", e)
        bot.reply_to(message, "❌ خطا در دریافت فایل.")
        return

    # finalize audio file if audio
    if media_type == 'audio':
        utils.finalize_audio_file(local_path, file_name)
        add_channel_metadata(local_path, CHANNEL_ID)

    # caption
    caption = f"🎵 {file_name}\n📌 {utils.make_channel_caption(CHANNEL_ID)}"
    database.add_post(local_path, file_id, safe_name, media_type, file_name, utils.user_display_name(user), uid)

    # send to channel
    try:
        with open(local_path, 'rb') as fh:
            if media_type == 'audio':
                bot.send_audio(CHANNEL_ID, fh, caption=caption, title=file_name)
            elif media_type == 'video':
                bot.send_video(CHANNEL_ID, fh, caption=caption)
            else:
                bot.send_document(CHANNEL_ID, fh, caption=caption)
        bot.reply_to(message, "✅ فایل شما با موفقیت در کانال منتشر شد.")
    except Exception as e:
        logger.exception("post to channel error: %s", e)
        bot.reply_to(message, f"❌ خطا در ارسال به کانال: {e}")

# ------------------- SOUNDCLOUD HANDLER -------------------
@bot.message_handler(func=lambda m: isinstance(m.text, str) and 'soundcloud.com' in m.text.lower())
@require_membership
def sc_handler(message):
    user = message.from_user
    uid = user.id
    database.add_or_update_user(uid, user.first_name or "", user.last_name or "", getattr(user, 'username', '') or "")
    url = message.text.strip()
    bot.reply_to(message, "✅ لینک دریافت شد، لطفاً چند لحظه صبر کنید...")

    try:
        local_path, info = utils.download_with_ytdlp(url, outdir=DOWNLOAD_PATH)
        title = info.get('title', 'SoundCloud Track')
        utils.finalize_audio_file(local_path, title)
        add_channel_metadata(local_path, CHANNEL_ID)  # <-- اضافه کردن اطلاعات کانال

        caption = f"🎵 {title}\n📌 {utils.make_channel_caption(CHANNEL_ID)}"
        with open(local_path, 'rb') as fh:
            bot.send_audio(CHANNEL_ID, fh, caption=caption, title=title)

        bot.reply_to(message, "✅ فایل SoundCloud دانلود و در کانال منتشر شد.")
        database.add_post(local_path, None, os.path.basename(local_path), 'soundcloud', title, utils.user_display_name(user), uid)
    except Exception as e:
        logger.exception("SoundCloud download error: %s", e)
        bot.reply_to(message, f"❌ دانلود ناموفق: {e}")

# ------------------- START WEBHOOK -------------------
from flask import Flask, request

app = Flask(__name__)

WEBHOOK_URL = f"https://{os.getenv('RENDER_EXTERNAL_URL').replace('https://', '')}/webhook"

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

if __name__ == '__main__':
    try:
        bot.remove_webhook()
    except:
        pass
    bot.set_webhook(url=WEBHOOK_URL)
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
