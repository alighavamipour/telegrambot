import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID"))
CHANNEL_ID = os.getenv("CHANNEL_ID")
ADMIN_IDS = [OWNER_ID]  # می‌تونی اضافه کنی چند ادمین

# مسیر ذخیره فایل‌های دریافتی
DOWNLOAD_PATH = "downloads"
