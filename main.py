import os
import logging
from functools import wraps
import telebot
from telebot import types
from datetime import datetime

from config import BOT_TOKEN, CHANNEL_ID, OWNER_ID, REQUIRED_CHANNELS, DB_PATH
import database
from utils_soundcloud import download_soundcloud
from yt_dlp import YoutubeDL

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)

# ایجاد پوشه data و دیتابیس اگر وجود نداشته باشد
if not os.path.exists('data'):
    os.makedirs('data')
if not os.path.exists(DB_PATH):
    database.init_db()

# ---------------------------
# Decorator عضویت اجباری
# ---------------------------
def member_required(func):
    @wraps(func)
    def wrapper(message, *args, **kwargs):
        user_id = message.from_user.id

        # پیام فوروارد شده
        if message.forward_from is not None:
            bot.reply_to(message, "⚠ لطفاً فایل را مستقیم برای ربات ارسال کنید، فوروارد نکنید.")
            return

        # بررسی عضویت در کانال
        for channel in REQUIRED_CHANNELS:
            try:
                member = bot.get_chat_member(channel, user_id)
                if member.status in ['left', 'kicked']:
                    bot.reply_to(message, f"⚠ برای استفاده از ربات باید عضو کانال {channel} باشید.")
                    return
            except Exception as e:
                logger.warning(f"Cannot check member {user_id} in {channel}: {e}")
                bot.reply_to(message, "⚠ مشکلی در بررسی عضویت شما پیش آمد. لطفاً دوباره امتحان کنید.")
                return

        # کاربر عضو کانال → اجرای تابع اصلی
        return func(message, *args, **kwargs)
    return wrapper

# ---------------------------
# منوی اصلی
# ---------------------------
def main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("🎵 آخرین آهنگ‌ها", "🎬 آخرین فیلم‌ها")
    markup.row("✍ ارسال پیام/توئیت", "📥 دانلود SoundCloud")
    markup.row("📢 درخواست تبلیغات")
    return markup

# ---------------------------
# شروع / منو
# ---------------------------
@bot.message_handler(commands=['start', 'menu'])
def send_welcome(message):
    bot.send_message(
        message.chat.id,
        "سلام! من ربات مدیریت کانال تو هستم.\nلطفاً یکی از گزینه‌های منو را انتخاب کنید.",
        reply_markup=main_menu()
    )

# ---------------------------
# مدیریت آهنگ و فیلم
# ---------------------------
@bot.message_handler(content_types=['audio', 'video', 'document'])
@member_required
def handle_media(message):
    user = message.from_user
    content_type = message.content_type

    # فایل دانلود می‌شود
    file_id = None
    if content_type == 'audio':
        file_id = message.audio.file_id
    elif content_type == 'video':
        file_id = message.video.file_id
    elif content_type == 'document':
        file_id = message.document.file_id

    try:
        file_info = bot.get_file(file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        filename = os.path.join('data', file_info.file_path.split('/')[-1])
        with open(filename, 'wb') as f:
            f.write(downloaded_file)

        # تعیین کپشن و آیکون
        if content_type == 'audio':
            caption = f"🎵 آهنگ از کانال ما: t.me/{CHANNEL_ID}"
        else:
            caption = f"🎬 فیلم از کانال ما: t.me/{CHANNEL_ID}"

        # ذخیره اطلاعات در دیتابیس
        database.save_media(user.username or user.first_name, filename, content_type, caption, datetime.now())

        # پست در کانال (برای کاربران عادی فقط اسمشون)
        bot.send_message(
            CHANNEL_ID,
            f"{caption}\nارسال شده توسط: {user.first_name}"
        )

        bot.reply_to(message, f"✅ فایل دریافت و در کانال منتشر شد: {filename}")

    except Exception as e:
        logger.error(f"Error handling media: {e}")
        bot.reply_to(message, "⚠ مشکلی پیش آمد، دوباره امتحان کنید.")

# ---------------------------
# SoundCloud
# ---------------------------
@bot.message_handler(func=lambda msg: 'soundcloud.com' in msg.text.lower())
@member_required
def handle_soundcloud(message):
    try:
        bot.reply_to(message, "⏳ دانلود SoundCloud شروع شد...")
        file_path = download_soundcloud(message.text)
        bot.reply_to(message, f"✅ دانلود انجام شد: {file_path}")
    except Exception as e:
        logger.error(f"Error downloading SoundCloud: {e}")
        bot.reply_to(message, "⚠ مشکلی در دانلود SoundCloud پیش آمد.")

# ---------------------------
# ارسال پیام/توئیت کاربران با تایید
# ---------------------------
@bot.message_handler(func=lambda msg: msg.text and msg.text.startswith("✍"))
@member_required
def handle_user_post(message):
    user = message.from_user
    database.save_pending_post(user.username or user.first_name, message.text)
    bot.reply_to(message, "✅ پیام شما ثبت شد و پس از تایید مدیر در کانال منتشر خواهد شد.")

# ---------------------------
# پست‌های تایید نشده (برای مدیر)
# ---------------------------
@bot.message_handler(commands=['pending'])
def pending_posts(message):
    if message.from_user.id != OWNER_ID:
        return
    posts = database.get_pending_posts()
    for post_id, username, text in posts:
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("✅ تایید", callback_data=f"approve_{post_id}"),
            types.InlineKeyboardButton("❌ رد", callback_data=f"reject_{post_id}")
        )
        bot.send_message(message.chat.id, f"{username}:\n{text}", reply_markup=markup)

# ---------------------------
# Callback تایید/رد
# ---------------------------
@bot.callback_query_handler(func=lambda call: call.data.startswith(("approve_", "reject_")))
def callback_approve(call):
    action, post_id = call.data.split("_")
    post_id = int(post_id)
    if call.from_user.id != OWNER_ID:
        bot.answer_callback_query(call.id, "❌ فقط مدیر می‌تواند این کار را انجام دهد.")
        return
    if action == "approve":
        post = database.get_post(post_id)
        bot.send_message(CHANNEL_ID, f"{post[2]}\nارسال شده توسط: {post[1]}")
        database.mark_post_done(post_id)
        bot.answer_callback_query(call.id, "✅ پست تایید شد و منتشر شد.")
    else:
        database.mark_post_done(post_id)
        bot.answer_callback_query(call.id, "❌ پست رد شد.")

# ---------------------------
# شروع ربات
# ---------------------------
logger.info("Bot started.")
bot.infinity_polling()
