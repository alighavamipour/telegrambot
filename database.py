# database.py
import sqlite3
from config import DB_PATH


def connect():
    return sqlite3.connect(DB_PATH)


def init_db():
    conn = connect()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            first_name TEXT,
            last_name TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_file_id TEXT,
            media_type TEXT,
            title TEXT,
            uploader TEXT,
            uploader_id INTEGER,
            posted INTEGER DEFAULT 0
        )
    """)

    conn.commit()
    conn.close()


def add_user(uid, f, l):
    conn = connect()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users VALUES (?,?,?)", (uid, f, l))
    conn.commit()
    conn.close()


def add_post(tg_file_id, media_type, title, uploader, uploader_id):
    conn = connect()
    c = conn.cursor()
    c.execute("""
        INSERT INTO posts (tg_file_id, media_type, title, uploader, uploader_id)
        VALUES (?,?,?,?,?)
    """, (tg_file_id, media_type, title, uploader, uploader_id))
    conn.commit()
    pid = c.lastrowid
    conn.close()
    return pid


def mark_posted(pid, message_id):
    conn = connect()
    c = conn.cursor()
    c.execute("UPDATE posts SET posted=1 WHERE id=?", (pid,))
    conn.commit()
    conn.close()


def get_latest(media_type):
    conn = connect()
    c = conn.cursor()
    c.execute("SELECT * FROM posts WHERE media_type=? ORDER BY id DESC LIMIT 10", (media_type,))
    rows = c.fetchall()
    conn.close()
    return rows


def get_file(pid):
    conn = connect()
    c = conn.cursor()
    c.execute("SELECT tg_file_id, media_type FROM posts WHERE id=?", (pid,))
    r = c.fetchone()
    conn.close()
    return r
