import os, logging, time, re
import telebot
from telebot import types
from config import BOT_TOKEN, CHANNEL_ID, OWNER_ID, REQUIRED_CHANNELS, DOWNLOAD_PATH, DB_PATH
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

# main keyboard
def main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("🎵 آخرین آهنگ‌ها", "🎬 آخرین فیلم‌ها")
    kb.row("📥 دانلود از SoundCloud", "✍ ارسال برای انتشار")
    kb.row("📢 درخواست تبلیغات", "آمار")
    return kb

@bot.message_handler(commands=['start','menu'])
def cmd_start(m):
    user = m.from_user
    database.add_or_update_user(user.id, user.first_name or "", user.last_name or "", getattr(user, 'username', '') or "")
    bot.send_message(m.chat.id, "سلام! من ربات مدیریت مدیا هستم. از منو استفاده کن.", reply_markup=main_keyboard())

# -------- receive audio/video/document ----------
@bot.message_handler(content_types=['audio','video','document'])
@require_membership
def media_handler(message):
    user = message.from_user
    uid = user.id
    database.add_or_update_user(uid, user.first_name or "", user.last_name or "", getattr(user, 'username', '') or "")
    vip = database.is_vip(uid) or (uid == OWNER_ID)

    # check file size for documents
    if message.content_type == 'document' and message.document.file_size > 50*1024*1024:
        bot.reply_to(message, "❌ فایل خیلی بزرگ است و نمی‌توان آن را دانلود کرد.")
        return

    # identify file info and local save name
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

    # download file content locally
    try:
        finfo = bot.get_file(file_id)
        data = bot.download_file(finfo.file_path)
        safe_name = re.sub(r'[^A-Za-z0-9\.\-_ء-ي ]', '_', file_name)
        local_path = os.path.join(DOWNLOAD_PATH, safe_name)
        with open(local_path, 'wb') as f:
            f.write(data)
    except Exception as e:
        logger.exception("download error: %s", e)
        bot.reply_to(message, "خطا در دریافت فایل. دوباره تلاش کنید.")
        return

    channel_link = utils.make_channel_caption(CHANNEL_ID)
    caption = f"{channel_link}\nID: {CHANNEL_ID}"
    uploader_name = utils.user_display_name(user)

    # write ID3 tag if mp3
    if media_type == 'audio' and local_path.lower().endswith('.mp3'):
        try:
            utils.write_id3_channel_tag(local_path, CHANNEL_ID)
        except Exception as e:
            logger.exception("ID3 tagging failed: %s", e)

    pid = database.add_post(local_path, file_id, file_name, media_type, "", uploader_name, uid)

    if vip:
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
            bot.reply_to(message, f"خطا در ارسال به کانال: {e}")
    else:
        msg = bot.reply_to(message, "آیا می‌خواهید این پست با نام خودتان منتشر شود؟ اگر نه 'انصراف'.")
        def ask_name_handler(reply):
            name = (reply.text or "").strip()
            if not name or name.lower() == 'انصراف':
                bot.send_message(reply.chat.id, "فایل برای بررسی به ادمین ارسال می‌شود.")
                try:
                    bot.send_message(OWNER_ID, f"فایل جدید از {uploader_name} برای بررسی.")
                    bot.send_document(OWNER_ID, open(local_path, 'rb'), caption=caption)
                except Exception as e:
                    logger.exception("notify owner error: %s", e)
                    bot.send_message(reply.chat.id, "خطا در ارسال فایل برای ادمین.")
                return
            conn = database.get_conn()
            cur = conn.cursor()
            cur.execute("UPDATE posts SET title=? WHERE id=?", (name, pid))
            conn.commit(); conn.close()
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("✅ تایید و پست", callback_data=f"approve_post|{pid}"),
                   types.InlineKeyboardButton("❌ رد", callback_data=f"reject_post|{pid}"))
            bot.send_message(OWNER_ID, f"درخواست انتشار از {uploader_name} — نام: {name}", reply_markup=kb)
            bot.send_document(OWNER_ID, open(local_path, 'rb'), caption=f"پیشنمایش: {caption}\nارسال‌کننده: {name}")
            bot.send_message(reply.chat.id, "درخواست شما ثبت شد. بعد از تایید مدیر منتشر خواهد شد.")
        bot.register_next_step_handler(msg, ask_name_handler)

# -------- SoundCloud handler ----------
@bot.message_handler(func=lambda m: isinstance(m.text, str) and 'soundcloud.com' in m.text.lower())
@require_membership
def sc_handler(message):
    user = message.from_user
    uid = user.id
    database.add_or_update_user(uid, user.first_name or "", user.last_name or "", getattr(user, 'username', '') or "")
    text = message.text.strip()
    urls = re.findall(r'(https?://\S+)', text)
    url = urls[0] if urls else text
    bot.reply_to(message, "✅ لینک دریافت شد، در حال پردازش...")

    try:
        local_path, info = utils.download_with_ytdlp(url, outdir=DOWNLOAD_PATH, filename_prefix=f"{uid}_sc")
        if local_path.lower().endswith('.mp3'):
            try: utils.write_id3_channel_tag(local_path, CHANNEL_ID)
            except: pass

        if uid == OWNER_ID or database.is_vip(uid):
            with open(local_path, 'rb') as fh:
                sent = bot.send_audio(CHANNEL_ID, fh, caption=f"{utils.make_channel_caption(CHANNEL_ID)}\nID: {CHANNEL_ID}")
            bot.reply_to(message, "✅ فایل SoundCloud دانلود و منتشر شد.")
        else:
            pid = database.add_post(local_path, None, os.path.basename(local_path), 'soundcloud', info.get('title',''), utils.user_display_name(user), uid)
            bot.send_message(OWNER_ID, f"📥 کاربر {utils.user_display_name(user)} لینک SoundCloud فرستاده — بررسی لازم:")
            bot.send_document(OWNER_ID, open(local_path, 'rb'))
            bot.reply_to(message, "نسخه دانلود شده برای بررسی به ادمین ارسال شد.")
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
