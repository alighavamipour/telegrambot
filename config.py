# config.py
BOT_TOKEN = "YOUR_BOT_TOKEN"
CHANNEL_ID = "@YOUR_CHANNEL_USERNAME" # مثل: @MyMusicChannel
OWNER_ID = 123456789 # عدد آیدی تلگرام شما (عدد)
DB_PATH = "data/bot.db"
# اگر می‌خواهید عضویت در چند کانال چک شود، می‌توانید اینجا لیست کنید
REQUIRED_CHANNELS = [CHANNEL_ID]
# محدودیت‌ها
MAX_AUDIO_MB = 50
MAX_VIDEO_MB = 500

---


## دیتابیس — فایل `database.py`
```python
# database.py
import sqlite3
from datetime import datetime
from config import DB_PATH


SCHEMA = [
'''
CREATE TABLE IF NOT EXISTS users (
user_id INTEGER PRIMARY KEY,
first_name TEXT,
last_name TEXT,
is_vip INTEGER DEFAULT 0,
created_at TEXT
)
''',
'''
CREATE TABLE IF NOT EXISTS posts (
id INTEGER PRIMARY KEY AUTOINCREMENT,
tg_file_id TEXT,
media_type TEXT,
title TEXT,
uploader_name TEXT,
uploader_id INTEGER,
posted INTEGER DEFAULT 0,
channel_message_id INTEGER,
created_at TEXT
)
''',
'''
CREATE TABLE IF NOT EXISTS pending_texts (
id INTEGER PRIMARY KEY AUTOINCREMENT,
user_id INTEGER,
user_name TEXT,
text TEXT,
status TEXT DEFAULT 'pending',
created_at TEXT
)
''',
]


def get_conn():
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
return conn


def init_db():
conn = get_conn()
c = conn.cursor()
for q in SCHEMA:
c.execute(q)
conn.commit()
conn.close()


# users
def add_or_update_user(user_id, first_name, last_name):
conn = get_conn()
c = conn.cursor()
now = datetime.utcnow().isoformat()
c.execute('''INSERT OR IGNORE INTO users (user_id, first_name, last_name, created_at) VALUES (?,?,?,?)''', (user_id, first_name, last_name, now))
c.execute('''UPDATE users SET first_name=?, last_name=? WHERE user_id=?''', (first_name, last_name, user_id))
conn.commit()
conn.close()


def set_vip(user_id, is_vip=1):
conn = get_conn()
c = conn.cursor()
c.execute('UPDATE users SET is_vip=? WHERE user_id=?', (1 if is_vip else 0, user_id))
conn.commit()
conn.close()


def is_vip(user_id):
conn = get_conn()
c = conn.cursor()
c.execute('SELECT is_vip FROM users WHERE user_id=?', (user_id,))
r = c.fetchone()
conn.close()
return bool(r and r[0]==1)
conn.close()
