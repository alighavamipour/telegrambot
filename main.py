import telebot
from telebot import types
from config import BOT_TOKEN, OWNER_ID, CHANNEL_ID, ADMIN_IDS
from database import add_user, is_vip, add_post
from utils import check_membership, generate_caption, download_file, main_menu
import os

bot = telebot.TeleBot(BOT_TOKEN)

# پیام استارت و چک عضویت
@bot.message_handler(commands=["start"])
def start(message):
    add_user(message.from_user.id, message.from_user.first_name, message.from_user.last_name, message.from_user.username)
    if not check_membership(bot, message.from_user.id):
        bot.send_message(message.chat.id, f"برای استفاده از ربات ابتدا باید عضو کانال شوید: {CHANNEL_ID}")
        return
    bot.send_message(message.chat.id, "سلام! به ربات خوش آمدید.", reply_markup=main_menu())

# دریافت فایل
@bot.message_handler(content_types=["audio", "video", "document"])
def receive_file(message):
    vip = is_vip(message.from_user.id)
    file_info = None
    if message.content_type == "audio":
        file_info = bot.get_file(message.audio.file_id)
        file_name = message.audio.file_name or "audio.mp3"
    elif message.content_type == "video":
        file_info = bot.get_file(message.video.file_id)
        file_name = message.video.file_name or "video.mp4"
    else:
        file_info = bot.get_file(message.document.file_id)
        file_name = message.document.file_name
    
    downloaded_file = bot.download_file(file_info.file_path)
    save_path = os.path.join("downloads", file_name)
    with open(save_path, "wb") as f:
        f.write(downloaded_file)

    caption = generate_caption(message.from_user, vip)
    add_post(message.from_user.id, file_name, message.content_type, caption)

    if vip or message.from_user.id == OWNER_ID:
        bot.send_message(message.chat.id, f"فایل شما با موفقیت ثبت شد و در کانال ارسال می‌شود.")
        bot.send_document(CHANNEL_ID, open(save_path, "rb"), caption=caption)
    else:
        bot.send_message(message.chat.id, f"فایل شما ثبت شد. منتظر تایید ادمین بمانید.")

# منو
@bot.message_handler(func=lambda m: True)
def menu(message):
    text = message.text
    if text == "🎵 آخرین آهنگ‌ها":
        bot.send_message(message.chat.id, "آخرین آهنگ‌ها: ...")
    elif text == "🎬 آخرین فیلم‌ها":
        bot.send_message(message.chat.id, "آخرین فیلم‌ها: ...")
    elif text == "📥 دانلود":
        bot.send_message(message.chat.id, "برای دانلود لینک بدهید.")
    else:
        bot.send_message(message.chat.id, "گزینه نامعتبر!")

# اجرای ربات
if __name__ == "__main__":
    print("Bot started...")
    bot.infinity_polling()
