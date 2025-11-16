import os

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))
CHANNEL_LINK = os.environ.get("CHANNEL_LINK", "https://t.me/YourChannel")
DOWNLOAD_PATH = os.environ.get("DOWNLOAD_PATH", "data/downloads")
STATS_FILE = os.environ.get("STATS_FILE", "data/stats.json")
