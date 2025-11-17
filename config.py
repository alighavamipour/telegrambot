import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN') or os.getenv('TELEGRAM_BOT_TOKEN')
CHANNEL_ID = os.getenv('CHANNEL_ID', '@voxxboxx')   # default to @voxxboxx
OWNER_ID = int(os.getenv('OWNER_ID', os.getenv('ADMIN_ID', '0') or 0))

# کانال(ها) الزامی برای عضویت
REQUIRED_CHANNELS = [CHANNEL_ID]

# مسیر ذخیره محلی فایل‌ها و دیتابیس
DATA_DIR = os.getenv('DATA_DIR', 'data')
DOWNLOAD_PATH = os.path.join(DATA_DIR, 'downloads')
DB_PATH = os.getenv('DB_PATH', os.path.join(DATA_DIR, 'bot.db'))
