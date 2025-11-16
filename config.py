import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
OWNER_ID = int(os.getenv("OWNER_ID"))
REQUIRED_CHANNELS = [CHANNEL_ID]
DB_PATH = "data/bot.db"
