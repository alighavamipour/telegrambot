import os, logging, time, re
import telebot
from telebot import types
from config import BOT_TOKEN, CHANNEL_ID, OWNER_ID, REQUIRED_CHANNELS, DOWNLOAD_PATH, DB_PATH
import database, utils
from functools import wraps
from flask import Flask, request


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)

database.init_db()
os.makedirs(DOWNLOAD_PATH, exist_ok=True)

MAX_FILE_SIZE = 50 * 1024 * 1024


#############################
# Inline Keyboards
#############################

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


#############################
# membership decorator
#############################
def require_membership(func):
    def wrapper(message, *args, **kwargs):
        if not utils.check_membership(bot, message.from_user.id):
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("عضویت", url=f"https://t.me/{CHANNEL_ID.lstrip('@')}"))
            bot.reply_to(message,"❌ عضو کانال شوید",reply_markup=kb)
            return
        return func(message,*args,**kwargs)
    return wrapper



#############################
# START
#############################

@bot.message_handler(commands=['start'])
def cmd_start(m):
    bot.send_message(m.chat.id,
                     "سلام!\n لینک یوتیوب ارسال کنید 👇")


##########################################
# YOUTUBE LINK HANDLER
##########################################

@bot.message_handler(func=lambda m: m.text and "youtube.com" in m.text.lower())
@require_membership
def yt_handler(message):

    link = message.text.strip()

    bot.reply_to(message,
                 "🎯 لطفاً کیفیت دلخواه را انتخاب کنید:",
                 reply_markup=build_quality_kb(link)
                 )


##########################################
# QUALITY CALLBACK
##########################################

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

        dur = info.get("duration")
        thumb = utils.get_thumbnail(info)

        title = info.get("title")
        
        size = os.path.getsize(filepath)
        if size > MAX_FILE_SIZE:
            bot.edit_message_text("❌ حجم فایل بیشتر از 50MB هست",call.message.chat.id,call.message.message_id)
            return

        cap = f"🎬 {title}\n" \
              f"⏱ {dur//60} دقیقه {dur%60} ثانیه"

        with open(filepath,'rb') as f:

            if audio_only:
                bot.send_audio(call.message.chat.id, f, caption=cap, thumb=thumb)
            else:
                bot.send_video(call.message.chat.id, f, caption=cap, thumb=thumb)

        bot.edit_message_text("✔ آماده شد!",call.message.chat.id,call.message.message_id)

    except Exception as e:
        bot.edit_message_text("❌ خطا"+str(e),call.message.chat.id,call.message.message_id)






#############################
# WEBHOOK
#############################

app = Flask(__name__)
WEBHOOK_URL = f"{os.getenv('RENDER_EXTERNAL_URL')}/webhook"

@app.route('/webhook',methods=['POST'])
def wh():
    update=telebot.types.Update.de_json(request.data.decode())
    bot.process_new_updates([update])
    return "OK",200

@app.route('/')
def home():
    return "OK"


if __name__=='__main__':
    bot.remove_webhook()
    bot.set_webhook(WEBHOOK_URL)
    app.run(host='0.0.0.0',port=int(os.environ.get("PORT",5000)))
