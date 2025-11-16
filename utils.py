from telebot import types
from config import CHANNEL_ID, OWNER_ID
import os
import requests

def check_membership(bot, user_id):
    try:
        member = bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status != "left"
    except:
        return False

def generate_caption(user, vip=False):
    if vip:
        return f"📢 پست VIP توسط {user.first_name} {user.last_name}"
    else:
        return f"📢 کانال ما: {CHANNEL_ID}\nارسال‌کننده: {user.first_name} {user.last_name}"

def download_file(url, filename):
    r = requests.get(url, stream=True)
    path = os.path.join("downloads", filename)
    with open(path, "wb") as f:
        for chunk in r.iter_content(1024):
            f.write(chunk)
    return path

def main_menu():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row("🎵 آخرین آهنگ‌ها", "🎬 آخرین فیلم‌ها")
    keyboard.row("📥 دانلود")
    return keyboard
