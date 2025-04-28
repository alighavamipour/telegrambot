import os  
import re  
import logging  
from pytube import YouTube  
from telegram import Update  
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes  

# تنظیمات لاگ  
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)  

# توکن ربات تلگرام از محیط  
API_TOKEN = os.getenv("7888950891:AAG_hzMdSJGYH83D1qr7kVHd6NPk7OstVGY")  

# الگوی regex برای شناسایی لینک‌های یوتیوب  
YOUTUBE_REGEX = r'(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})'  

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):  
    await update.message.reply_text("سلام! لطفاً لینک یوتیوب را ارسال کنید.")  

async def download_video(update: Update, context: ContextTypes.DEFAULT_TYPE):  
    url = update.message.text  

    if re.match(YOUTUBE_REGEX, url):  
        await update.message.reply_text("در حال دانلود ویدیو...")  
        try:  
            # دانلود ویدیو  
            yt = YouTube(url)  
            stream = yt.streams.get_highest_resolution()  
            stream.download(filename='video.mp4')  
            # ارسال ویدیو به کاربر  
            with open('video.mp4', 'rb') as video:  
                await context.bot.send_video(chat_id=update.message.chat_id, video=video)  
        except Exception as e:  
            await update.message.reply_text(f"خطا در دانلود ویدیو: {str(e)}")  
    else:  
        await update.message.reply_text("لطفاً یک لینک یوتیوب معتبر ارسال کنید.")  

def main():  
    # ساختار ربات  
    application = ApplicationBuilder().token(API_TOKEN).build()  

    # نصب هندلرها  
    application.add_handler(CommandHandler('start', start))  
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_video))  

    # شروع ربات  
    application.run_polling()  

if __name__ == '__main__':  
    main()  
