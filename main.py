import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from database import init_db, add_user, get_user
from utils import check_membership, clean_caption, is_owner
import os
import sqlite3

# --- Environment Variables ---
BOT_TOKEN = os.environ.get('BOT_TOKEN')
CHANNEL_ID = os.environ.get('CHANNEL_ID')
OWNER_ID = int(os.environ.get('OWNER_ID'))

bot = telebot.TeleBot(BOT_TOKEN)

# --- Database setup ---
init_db()

# --- Start / Help commands ---
@bot.message_handler(commands=['start', 'help'])
def start(message):
    user_id = message.from_user.id
    add_user(user_id, message.from_user.first_name, message.from_user.last_name)
    
    if not check_membership(user_id, CHANNEL_ID):
        bot.send_message(message.chat.id,
                         f"برای استفاده از ربات باید عضو کانال ما شوید: t.me/{CHANNEL_ID}")
        return

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🎵 آخرین آهنگ‌ها", callback_data="latest_songs"))
    markup.add(InlineKeyboardButton("🎬 آخرین فیلم‌ها", callback_data="latest_movies"))
    bot.send_message(message.chat.id, "سلام! من ربات شما هستم.", reply_markup=markup)


# --- Handling media ---
@bot.message_handler(content_types=['audio', 'document', 'video'])
def handle_media(message):
    user_id = message.from_user.id
    
    if not check_membership(user_id, CHANNEL_ID):
        bot.send_message(message.chat.id, "لطفاً ابتدا عضو کانال شوید.")
        return

    caption = f"کانال ما: t.me/{CHANNEL_ID}"
    filename = None

    if message.audio:
        file_info = bot.get_file(message.audio.file_id)
        filename = message.audio.file_name
        downloaded_file = bot.download_file(file_info.file_path)
    elif message.video:
        file_info = bot.get_file(message.video.file_id)
        filename = message.video.file_name
        downloaded_file = bot.download_file(file_info.file_path)
    elif message.document:
        file_info = bot.get_file(message.document.file_id)
        filename = message.document.file_name
        downloaded_file = bot.download_file(file_info.file_path)

    # Save file locally
    with open(filename, 'wb') as f:
        f.write(downloaded_file)

    # Forward to channel (owner can send directly)
    if is_owner(user_id, OWNER_ID):
        bot.send_message(message.chat.id, "فایل شما برای کانال ارسال شد.")
        bot.send_document(CHANNEL_ID, open(filename, 'rb'), caption=caption)
    else:
        # For regular users, store or send to owner for review
        bot.send_message(OWNER_ID, f"کاربر {message.from_user.first_name} {message.from_user.last_name} ارسال کرده:")
        bot.send_document(OWNER_ID, open(filename, 'rb'), caption=caption)
        bot.send_message(message.chat.id, "فایل شما برای بررسی به ادمین ارسال شد.")

# --- Callback for inline keyboard ---
@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    if call.data == "latest_songs":
        bot.answer_callback_query(call.id, "لیست آخرین آهنگ‌ها آماده شد.")
        # Logic to show latest songs
    elif call.data == "latest_movies":
        bot.answer_callback_query(call.id, "لیست آخرین فیلم‌ها آماده شد.")
        # Logic to show latest movies

# --- Run bot ---
bot.remove_webhook()
bot.infinity_polling()
