# main.py
import os
import logging
from functools import wraps
import telebot
from telebot import types

from config import BOT_TOKEN, CHANNEL_ID, OWNER_ID, REQUIRED_CHANNELS, DB_PATH
import database
from utils_soundcloud import download_soundcloud

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)

# ساخت فولدر دیتا + دیتابیس
if not os.path.exists('data'):
    os.makedirs('data')
if not os.path.exists(DB_PATH):
    database.init_db()

# بررسی عضویت اجباری
def is_member(user_id):
    for ch in REQUIRED_CHANNELS:
        try:
            st = bot.get_chat_member(ch, user_id)
            if st.status in ['left', 'kicked']:
                return False
        except:
            return False
    return True

def require_member(fn):
    @wraps(fn)
    def wrapper(message):
        uid = message.from_user.id
        if not is_member(uid):
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton(
                'عضویت در کانال', url=f"https://t.me/{CHANNEL_ID[1:]}"
            ))
            bot.reply_to(message, "برای استفاده از ربات باید عضو کانال شوید.", reply_markup=kb)
            return
        return fn(message)
    return wrapper


# شروع ربات
@bot.message_handler(commands=['start','menu'])
def start(message):
    user = message.from_user
    database.add_user(user.id, user.first_name, user.last_name)

    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("🎵 آخرین آهنگ‌ها", "🎬 آخرین ویدئوها")
    kb.row("📥 دانلود از SoundCloud", "✉️ ارسال متن")
    kb.row("📢 درخواست تبلیغات", "آمار")

    bot.send_message(message.chat.id, "سلام، ربات مدیریت رسانه فعال شد.", reply_markup=kb)


# دریافت آهنگ
@bot.message_handler(content_types=['audio'])
@require_member
def audio_handler(message):

    user = message.from_user
    name = (user.first_name or "") + (" " + user.last_name if user.last_name else "")

    caption = f"{CHANNEL_ID}"

    post_id = database.add_post(
        tg_file_id=message.audio.file_id,
        media_type="audio",
        title=message.audio.title or "",
        uploader=name,
        uploader_id=user.id
    )

    # ارسال نسخه بررسی به مالک ربات
    bot.send_audio(OWNER_ID, message.audio.file_id, caption=f"نسخه بررسی: {name}")

    # ارسال در کانال
    sent = bot.send_audio(CHANNEL_ID, message.audio.file_id, caption=caption)

    database.mark_posted(post_id, sent.message_id)

    bot.reply_to(message, "آهنگ شما در کانال منتشر شد ✔️")


# دریافت ویدئو
@bot.message_handler(content_types=['video','document'])
@require_member
def video_handler(message):

    user = message.from_user
    name = (user.first_name or "")

    caption = f"{CHANNEL_ID}"

    file_id = message.video.file_id if message.content_type == "video" else message.document.file_id

    post_id = database.add_post(
        tg_file_id=file_id,
        media_type="video",
        title=message.caption or "",
        uploader=name,
        uploader_id=user.id
    )

    sent = bot.send_document(CHANNEL_ID, file_id, caption=caption)

    database.mark_posted(post_id, sent.message_id)

    bot.reply_to(message, "ویدئو شما در کانال منتشر شد ✔️")


# دانلود ساندکلود فقط برای OWNER
@bot.message_handler(commands=['sc'])
def sc_command(message):
    if message.from_user.id != OWNER_ID:
        bot.reply_to(message, "این دستور فقط برای مالک ربات است.")
        return

    bot.reply_to(message, "لینک SoundCloud را بفرست:")
    bot.register_next_step_handler(message, sc_download_step)


def sc_download_step(message):
    url = message.text.strip()
    msg = bot.reply_to(message, "در حال دانلود...")

    try:
        path = download_soundcloud(url)
        bot.send_document(OWNER_ID, open(path, 'rb'), caption="نسخه بررسی آماده شد.")
        bot.edit_message_text("دانلود انجام شد.", msg.chat.id, msg.message_id)
    except Exception as e:
        bot.edit_message_text(f"خطا: {e}", msg.chat.id, msg.message_id)



# آخرین آهنگ‌ها
@bot.message_handler(func=lambda m: m.text == "🎵 آخرین آهنگ‌ها")
def latest_audios(message):
    posts = database.get_latest("audio")
    if not posts:
        bot.reply_to(message, "هنوز آهنگی موجود نیست.")
        return

    for p in posts:
        pid, file_id, _, title, uploader, _ = p
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("دانلود", callback_data=f"dl|{pid}"))

        bot.send_audio(message.chat.id, file_id,
                       caption=f"{title}\nارسال توسط: {uploader}",
                       reply_markup=kb)



# دانلود پست
@bot.callback_query_handler(func=lambda c: c.data.startswith("dl"))
def dl_callback(call):
    pid = int(call.data.split("|")[1])
    file_id, mtype = database.get_file(pid)

    if mtype == "audio":
        bot.send_audio(call.from_user.id, file_id)
    else:
        bot.send_document(call.from_user.id, file_id)

    bot.answer_callback_query(call.id, "فایل برای شما ارسال شد.")




logger.info("Bot started.")

bot.infinity_polling()
