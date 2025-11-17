import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN') or os.getenv('TELEGRAM_BOT_TOKEN')
OWNER_ID = int(os.getenv('OWNER_ID', os.getenv('ADMIN_ID', '0') or 0))

# --- CHANNEL ID handling ---
raw_channel = os.getenv('CHANNEL_ID', '@voxxboxx').strip()

# اگر کانال عددی باشد (private)
if raw_channel.startswith('-100'):
    CHANNEL_ID = raw_channel

# اگر کانال public باشد با @
elif raw_channel.startswith('@'):
    CHANNEL_ID = raw_channel

# اگر نام بدون @ وارد شده باشد
else:
    CHANNEL_ID = '@' + raw_channel

# کانال‌های موردنیاز
REQUIRED_CHANNELS = [CHANNEL_ID]

# مسیرها
DATA_DIR = os.getenv('DATA_DIR', 'data')
DOWNLOAD_PATH = os.path.join(DATA_DIR, 'downloads')
DB_PATH = os.getenv('DB_PATH', os.path.join(DATA_DIR, 'bot.db'))
