import re, os, logging
from config import DOWNLOAD_PATH, REQUIRED_CHANNELS, CHANNEL_ID
from yt_dlp import YoutubeDL
from mutagen.id3 import ID3, TIT2, TALB, TPE1, TPE2, COMM, TCON, ID3NoHeaderError

logger = logging.getLogger(__name__)
os.makedirs(DOWNLOAD_PATH, exist_ok=True)

CHANNEL_TAG = CHANNEL_ID if CHANNEL_ID.startswith("@") else f"@{CHANNEL_ID}"

# ------------------- CLEAN CAPTION -------------------
def clean_caption(text):
    """حذف تگ‌ها، لینک‌ها و هشتگ‌ها از متن"""
    if not text:
        return ""
    t = re.sub(r'@\w+', '', text)
    t = re.sub(r'http\S+', '', t)
    t = re.sub(r'#\w+', '', t)
    return t.strip()

# ------------------- USER DISPLAY NAME -------------------
def user_display_name(user):
    """ساخت نام نمایش برای کاربر"""
    fn = user.first_name or ""
    ln = user.last_name or ""
    return (fn + (" " + ln if ln else "")).strip() or "ناشناس"

# ------------------- MAKE CHANNEL CAPTION -------------------
def make_channel_caption(channel_id=None):
    """لینک کانال برای کپشن"""
    ch = channel_id or CHANNEL_ID
    return f"https://t.me/{ch.lstrip('@')}"

# ------------------- CHECK MEMBERSHIP -------------------
def check_membership(bot, user_id):
    """بررسی عضویت کاربر در کانال‌های مورد نیاز"""
    try:
        for ch in REQUIRED_CHANNELS:
            member = bot.get_chat_member(ch, user_id)
            if member.status not in ['left', 'kicked']:
                return True
        return False
    except Exception as e:
        logger.error("Membership check failed: %s", e)
        return False

# ------------------- DOWNLOAD WITH YT-DLP -------------------
def download_with_ytdlp(url, outdir=DOWNLOAD_PATH, filename_prefix=None):
    """دانلود فایل صوتی از لینک‌ها (مثل SoundCloud) با yt-dlp"""
    os.makedirs(outdir, exist_ok=True)
    outtmpl = os.path.join(outdir, '%(title)s.%(ext)s')
    opts = {
        'format': 'bestaudio/best',
        'outtmpl': outtmpl,
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': False,
    }
    with YoutubeD
