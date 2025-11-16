import sqlite3
from config import DOWNLOAD_PATH
import os

if not os.path.exists(DOWNLOAD_PATH):
    os.makedirs(DOWNLOAD_PATH)

conn = sqlite3.connect("bot.db", check_same_thread=False)
cursor = conn.cursor()

# ایجاد جداول
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    first_name TEXT,
    last_name TEXT,
    username TEXT,
    vip INTEGER DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS posts (
    post_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    file_name TEXT,
    file_type TEXT,
    caption TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS requests (
    request_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    type TEXT,
    content TEXT,
    status TEXT DEFAULT 'pending',
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")

conn.commit()

def add_user(user_id, first_name, last_name, username):
    cursor.execute("INSERT OR IGNORE INTO users VALUES (?,?,?,?,?)", (user_id, first_name, last_name, username, 0))
    conn.commit()

def is_vip(user_id):
    cursor.execute("SELECT vip FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    return row and row[0] == 1

def add_post(user_id, file_name, file_type, caption):
    cursor.execute("INSERT INTO posts (user_id, file_name, file_type, caption) VALUES (?,?,?,?)",
                   (user_id, file_name, file_type, caption))
    conn.commit()
