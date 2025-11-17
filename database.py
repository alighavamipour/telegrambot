import sqlite3
from config import DB_PATH
import os
from datetime import datetime

os.makedirs(os.path.dirname(DB_PATH) or '.', exist_ok=True)

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        first_name TEXT,
        last_name TEXT,
        username TEXT,
        vip INTEGER DEFAULT 0,
        created_at TEXT
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        local_path TEXT,
        tg_file_id TEXT,
        file_name TEXT,
        media_type TEXT,
        title TEXT,
        uploader_name TEXT,
        uploader_id INTEGER,
        posted INTEGER DEFAULT 0,
        channel_message_id INTEGER,
        created_at TEXT
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS pending_texts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        user_name TEXT,
        text TEXT,
        status TEXT DEFAULT 'pending',
        created_at TEXT
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        type TEXT,
        content TEXT,
        status TEXT DEFAULT 'pending',
        created_at TEXT
    )""")
    conn.commit()
    conn.close()

# users
def add_or_update_user(user_id, first_name, last_name, username):
    conn = get_conn(); c = conn.cursor()
    now = datetime.utcnow().isoformat()
    c.execute("INSERT OR IGNORE INTO users (user_id, first_name, last_name, username, created_at) VALUES (?,?,?,?,?)",
              (user_id, first_name, last_name, username, now))
    c.execute("UPDATE users SET first_name=?, last_name=?, username=? WHERE user_id=?",
              (first_name, last_name, username, user_id))
    conn.commit(); conn.close()

def set_vip(user_id, is_vip=1):
    conn = get_conn(); c = conn.cursor()
    c.execute("UPDATE users SET vip=? WHERE user_id=?", (1 if is_vip else 0, user_id))
    conn.commit(); conn.close()

def is_vip(user_id):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT vip FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone(); conn.close()
    return bool(row and row[0] == 1)

# posts: store local path (saved file), optional telegram file id
def add_post(local_path, tg_file_id, file_name, media_type, title, uploader_name, uploader_id):
    conn = get_conn(); c = conn.cursor()
    now = datetime.utcnow().isoformat()
    c.execute("""INSERT INTO posts (local_path, tg_file_id, file_name, media_type, title, uploader_name, uploader_id, created_at)
                 VALUES (?,?,?,?,?,?,?,?)""", (local_path, tg_file_id, file_name, media_type, title, uploader_name, uploader_id, now))
    conn.commit(); pid = c.lastrowid; conn.close(); return pid

def mark_posted(post_id, channel_message_id=None):
    conn = get_conn(); c = conn.cursor()
    c.execute("UPDATE posts SET posted=1, channel_message_id=? WHERE id=?", (channel_message_id, post_id))
    conn.commit(); conn.close()

def latest_posts(limit=10, media_type=None):
    conn = get_conn(); c = conn.cursor()
    if media_type:
        c.execute("SELECT id, local_path, tg_file_id, file_name, media_type, title, uploader_name, created_at FROM posts WHERE media_type=? ORDER BY id DESC LIMIT ?", (media_type, limit))
    else:
        c.execute("SELECT id, local_path, tg_file_id, file_name, media_type, title, uploader_name, created_at FROM posts ORDER BY id DESC LIMIT ?", (limit,))
    rows = c.fetchall(); conn.close(); return rows

def get_post(post_id):
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT id, local_path, tg_file_id, file_name, media_type, title, uploader_name FROM posts WHERE id=?", (post_id,))
    r = c.fetchone(); conn.close(); return r

# pending texts
def add_pending_text(user_id, user_name, text):
    conn = get_conn(); c = conn.cursor()
    now = datetime.utcnow().isoformat()
    c.execute("INSERT INTO pending_texts (user_id, user_name, text, created_at) VALUES (?,?,?,?)", (user_id, user_name, text, now))
    conn.commit(); pid = c.lastrowid; conn.close(); return pid

def get_pending_texts():
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT id, user_id, user_name, text, created_at FROM pending_texts WHERE status='pending' ORDER BY id ASC")
    r = c.fetchall(); conn.close(); return r

def set_pending_status(pending_id, status):
    conn = get_conn(); c = conn.cursor()
    c.execute("UPDATE pending_texts SET status=? WHERE id=?", (status, pending_id))
    conn.commit(); conn.close()

# requests
def add_request(user_id, req_type, content):
    conn = get_conn(); c = conn.cursor()
    now = datetime.utcnow().isoformat()
    c.execute("INSERT INTO requests (user_id, type, content, created_at) VALUES (?,?,?,?)", (user_id, req_type, content, now))
    conn.commit(); rid = c.lastrowid; conn.close(); return rid

def get_requests():
    conn = get_conn(); c = conn.cursor()
    c.execute("SELECT id, user_id, type, content, status, created_at FROM requests ORDER BY id DESC")
    r = c.fetchall(); conn.close(); return r
