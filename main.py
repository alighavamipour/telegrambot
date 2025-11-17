import os, logging, time, re
import telebot
from telebot import types
from config import BOT_TOKEN, CHANNEL_ID, DOWNLOAD_PATH, DB_PATH
import database, utils
from functools import wraps

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)

# init DB and folders
database.init_db()
os.makedirs(DOWNLOAD_PATH, exist_ok=True)

# decorator: require membership
def require_membership(func):
    @wraps(func)
    def wrapper(message, *args, **kwargs):
        uid = message.from_user.id
        try:
            if not utils.check_membership(bot, uid):
                kb = types.InlineKeyboardMarkup()
                kb.add(types.InlineKeyboardButton(
                    "عضویت در کانال",
                    url=f"https://t.me/{CHANNEL_ID.lstrip('@')}"
                ))
                bot.reply_to(message, "برای استفاده از ربات باید عضو کانال شوید.", reply_markup=kb)
                return
        except Exception as e:
            logger.exception("membership check failed: %s", e)
            bot.reply_to(message, "❌ خطا در بررسی عضویت. دوباره تلاش کنید.")
            return
        return func(message, *args, **kwargs)
    return wrapper

# -------- راهنمای شروع --------
@bot.message_handler(commands=['start'])
def cmd_start(m):
    text = (
        "سلام! 👋\n\n"
        "این ربات می‌تواند فایل‌های صوتی و لینک‌های SoundCloud را دانلود و در کانال منتشر کند 🎵\n"
        "📌 نحوه کار:\n"
        "1️⃣ لینک SoundCloud یا فایل صوتی خود را اینجا ارسال کنید.\n"
        "2️⃣ ربات فایل را دانلود می‌کند و کپشن جذاب با نام آهنگ ایجاد می‌کند.\n"
        "3️⃣ فایل در کانال منتشر خواهد شد ✅\n\n"
        "توجه: لطفاً عضو کانال باشید تا ربات فایل‌ها را دریافت کند."
    )
    bot.send_message(m.chat.id, text)

# -------- دریافت فایل‌ها ----------
@bot.message_handler(content_types=['audio','video','document'])
@require_membership
def media_handler(message):
    user = message.from_user
    uid = user.id
    database.add_or_update_user(uid, user.first_name or "", user.last_name or "", getattr(user, 'username', '') or "")

    # شناسایی نوع فایل
    if message.content_type == 'audio':
        file_id = message.audio.file_id
        file_name = message.audio.title or f"audio_{int(time.time())}.mp3"
        media_type = 'audio'
    elif message.content_type == 'video':
        file_id = message.video.file_id
        file_name = message.video.file_name or f"video_{int(time.time())}.mp4"
        media_type = 'video'
    else:
        file_id = message.document.file_id
        file_name = message.document.file_name or f"file_{int(time.time())}"
        media_type = 'document'

    # دانلود فایل
    try:
        finfo = bot.get_file(file_id)
        data = bot.download_file(finfo.file_path)
        safe_name = re.sub(r'[^A-Za-z0-9\.\-_ء-ي ]', '_', file_name)
        local_path = os.path.join(DOWNLOAD_PATH, safe_name)
        with open(local_path, 'wb') as f:
            f.write(data)
    except Exception as e:
        logger.exception("download error: %s", e)
        bot.reply_to(message, "❌ خطا در دریافت فایل. دوباره تلاش کنید.")
        return

    # کپشن جذاب
    title = os.path.splitext(file_name)[0]
    caption = (
        f"🎵 آهنگ جدید: {title}\n"
        f"💌 ارسال شده توسط: {user.first_name}\n"
        f"🔗 کانال ما: {CHANNEL_ID}\n\n"
        f"از ربات استفاده کنید و همیشه موسیقی‌های جدید دریافت کنید! 🎧"
    )

    # نوشتن ID3 اگر mp3
    if media_type == 'audio' and local_path.lower().endswith('.mp3'):
        try:
            utils.write_id3_channel_tag(local_path, CHANNEL_ID, title=title)
        except Exception as e:
            logger.exception("ID3 tagging failed: %s", e)

    # ذخیره در دیتابیس
    pid = database.add_post(local_path, file_id, file_name, media_type, "", utils.user_display_name(user), uid)

    # ارسال به کانال
    try:
        if media_type == 'audio':
            with open(local_path, 'rb') as fh:
                sent = bot.send_audio(CHANNEL_ID, fh, caption=caption)
        elif media_type == 'video':
            with open(local_path, 'rb') as fh:
                sent = bot.send_video(CHANNEL_ID, fh, caption=caption)
        else:
            with open(local_path, 'rb') as fh:
                sent = bot.send_document(CHANNEL_ID, fh, caption=caption)
        database.mark_posted(pid, getattr(sent, 'message_id', None))
        bot.reply_to(message, "✅ فایل شما با موفقیت در کانال منتشر شد.")
    except Exception as e:
        logger.exception("post to channel error: %s", e)
        bot.reply_to(message, f"❌ خطا در ارسال به کانال: {e}")

# -------- لینک SoundCloud ----------
@bot.message_handler(func=lambda m: isinstance(m.text, str) and 'soundcloud.com' in m.text.lower())
@require_membership
def sc_handler(message):
    user = message.from_user
    uid = user.id
    database.add_or_update_user(uid, user.first_name or "", user.last_name or "", getattr(user, 'username', '') or "")
    url = message.text.strip()
    bot.reply_to(message, "✅ لینک دریافت شد، در حال پردازش...")

    try:
        local_path, info = utils.download_with_ytdlp(url, outdir=DOWNLOAD_PATH)
        title = info.get('title', f"soundcloud_{int(time.time())}")
        ext = os.path.splitext(local_path)[1] or '.mp3'
        safe_name = re.sub(r'[^A-Za-z0-9\.\-_ء-ي ]', '_', f"{title}{ext}")
        final_path = os.path.join(DOWNLOAD_PATH, safe_name)
        os.rename(local_path, final_path)

        # نوشتن ID3 و متن کانال
        if final_path.lower().endswith('.mp3'):
            try:
                utils.write_id3_channel_tag(final_path, CHANNEL_ID, title=title)
            except Exception as e:
                logger.exception("ID3 tagging failed: %s", e)

        # کپشن جذاب
        caption = (
            f"🎧 آهنگ جدید از SoundCloud 🎧\n"
            f"🎵 عنوان: {title}\n"
            f"💌 ارسال شده توسط: {user.first_name}\n"
            f"🔗 کانال ما: {CHANNEL_ID}\n\n"
            f"از ربات استفاده کنید و همیشه آهنگ‌های جدید را دریافت کنید! 🎶"
        )

        # ارسال به کانال
        with open(final_path, 'rb') as fh:
            sent = bot.send_audio(CHANNEL_ID, fh, caption=caption)
        bot.reply_to(message, "✅ فایل SoundCloud دانلود و منتشر شد.")
        database.add_post(final_path, None, safe_name, 'soundcloud', title, utils.user_display_name(user), uid)
    except Exception as e:
        logger.exception("SoundCloud download error: %s", e)
        bot.reply_to(message, f"❌ دانلود ناموفق: {e}")

# -------- safe startup ----------
if __name__ == '__main__':
    try:
        try: bot.remove_webhook()
        except: pass
        logger.info("Webhook removed (if any). Starting polling...")
        bot.infinity_polling(timeout=60, long_polling_timeout=60)
    except Exception as e:
        logger.exception("Fatal bot error: %s", e)
        raise
