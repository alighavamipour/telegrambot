import telebot
from telebot import types
import os
import logging

# ====== تنظیمات ======
API_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHANNEL_USERNAME = "@YourChannel"  # آدرس کانال
ADMIN_ID = int(os.environ.get("ADMIN_ID", 123456789))

bot = telebot.TeleBot(API_TOKEN)
telebot.logger.setLevel(logging.INFO)

# ====== رفع مشکل Conflict 409 ======
bot.remove_webhook()
logging.info("Webhook removed, polling started...")

# ====== دیتابیس ساده ======
users_pending_posts = {}  # پیام‌های منتظر تایید {user_id: message_text}

# ====== بررسی عضویت ======
def is_member(user_id):
    try:
        member = bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status in ['member', 'creator', 'administrator']
    except Exception:
        return False

# ====== منو اصلی ======
def main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("🎵 آخرین آهنگ‌ها", "🎬 آخرین فیلم‌ها")
    markup.row("📥 SoundCloud Downloader", "✉️ ارسال پیام")
    markup.row("📢 درخواست تبلیغات")
    return markup

# ====== شروع ربات ======
@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(message.chat.id, "سلام! به ربات خوش آمدید.", reply_markup=main_menu())

# ====== مدیریت ارسال‌ها ======
@bot.message_handler(content_types=['text', 'audio', 'video', 'document'])
def handle_message(message):
    user_id = message.from_user.id

    if not is_member(user_id):
        bot.send_message(user_id, f"برای استفاده از ربات باید عضو کانال {CHANNEL_USERNAME} باشید.")
        return

    if message.content_type == 'text':
        text = message.text
        if text == "🎵 آخرین آهنگ‌ها":
            bot.send_message(user_id, f"آخرین آهنگ‌ها در کانال {CHANNEL_USERNAME} 🎵")
        elif text == "🎬 آخرین فیلم‌ها":
            bot.send_message(user_id, f"آخرین فیلم‌ها در کانال {CHANNEL_USERNAME} 🎬")
        elif text == "📥 SoundCloud Downloader":
            bot.send_message(user_id, "لینک SoundCloud خود را ارسال کنید تا دانلود شود.")
        elif text == "✉️ ارسال پیام":
            bot.send_message(user_id, "پیام خود را ارسال کنید تا پس از تایید مدیر منتشر شود.")
        elif text == "📢 درخواست تبلیغات":
            bot.send_message(user_id, "لطفاً متن درخواست تبلیغات خود را ارسال کنید.")
        return

    # فایل‌ها (audio/video/document)
    if message.content_type in ['audio', 'video', 'document']:
        file_id = None
        caption = ""
        if message.content_type == 'audio':
            file_id = message.audio.file_id
        elif message.content_type == 'video':
            file_id = message.video.file_id
        else:
            file_id = message.document.file_id

        # مدیریت ارسال توسط مدیر یا کاربر
        if user_id == ADMIN_ID:
            caption = f"دانلود شده از {CHANNEL_USERNAME}"
            try:
                if message.content_type == 'audio':
                    bot.send_audio(CHANNEL_USERNAME, file_id, caption=caption)
                elif message.content_type == 'video':
                    bot.send_video(CHANNEL_USERNAME, file_id, caption=caption)
                else:
                    bot.send_document(CHANNEL_USERNAME, file_id, caption=caption)
                bot.send_message(user_id, "با موفقیت ارسال شد ✅")
            except Exception as e:
                bot.send_message(user_id, f"خطا در ارسال: {e}")
        else:
            caption = f"ارسال شده توسط {message.from_user.first_name}"
            # ذخیره پیام برای تایید
            users_pending_posts[user_id] = (file_id, message.content_type, caption)
            bot.send_message(user_id, "پیام شما برای تایید مدیر ثبت شد ✅")
            bot.send_message(ADMIN_ID, f"پیام جدید برای تایید از {message.from_user.first_name} ({user_id})")

# ====== تایید پیام توسط مدیر ======
@bot.message_handler(func=lambda m: m.from_user.id == ADMIN_ID)
def admin_approve(message):
    if message.text.startswith("تایید "):
        try:
            user_id = int(message.text.split()[1])
            if user_id in users_pending_posts:
                file_id, content_type, caption = users_pending_posts[user_id]
                if content_type == 'audio':
                    bot.send_audio(CHANNEL_USERNAME, file_id, caption=caption)
                elif content_type == 'video':
                    bot.send_video(CHANNEL_USERNAME, file_id, caption=caption)
                else:
                    bot.send_document(CHANNEL_USERNAME, file_id, caption=caption)
                bot.send_message(user_id, "پیام شما منتشر شد ✅")
                del users_pending_posts[user_id]
        except Exception as e:
            bot.send_message(ADMIN_ID, f"خطا در تایید پیام: {e}")

# ====== Polling ======
bot.infinity_polling()
