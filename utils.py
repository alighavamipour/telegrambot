import re, os, logging
from config import DOWNLOAD_PATH, REQUIRED_CHANNELS, CHANNEL_ID
from yt_dlp import YoutubeDL
from mutagen.id3 import ID3, TIT2, TALB, TPE1, TPE2, COMM, TCON, ID3NoHeaderError

logger = logging.getLogger(__name__)
os.makedirs(DOWNLOAD_PATH, exist_ok=True)

CHANNEL_TAG = CHANNEL_ID if CHANNEL_ID.startswith("@") else f"@{CHANNEL_ID}"

# ------------------- CLEAN CAPTION -------------------
def clean_caption(text):
    """
    حذف تگ‌ها، لینک‌ها و هشتگ‌ها از متن
    """
    if not text:
        return ""
    t = re.sub(r'@\w+', '', text)
    t = re.sub(r'http\S+', '', t)
    t = re.sub(r'#\w+', '', t)
    return t.strip()

# ------------------- USER DISPLAY NAME -------------------
def user_display_name(user):
    """
    ساخت نام نمایش برای کاربر
    """
    fn = user.first_name or ""
    ln = user.last_name or ""
    return (fn + (" " + ln if ln else "")).strip() or "ناشناس"

# ------------------- MAKE CHANNEL CAPTION -------------------
def make_channel_caption(channel_id=None):
    """
    لینک کانال برای کپشن
    """
    ch = channel_id or CHANNEL_ID
    return f"https://t.me/{ch.lstrip('@')}"

# ------------------- CHECK MEMBERSHIP -------------------
def check_membership(bot, user_id):
    """
    بررسی عضویت کاربر در کانال‌های مورد نیاز
    """
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
    """
    دانلود فایل صوتی از لینک‌ها (مثل SoundCloud) با yt-dlp
    """
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
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        fname = ydl.prepare_filename(info)
        title_safe = re.sub(r'[^A-Za-z0-9\.\-_ء-ي ]', '_', info.get('title', 'audio')).strip()
        ext = os.path.splitext(fname)[1]
        safe_fname = os.path.join(outdir, f"{title_safe}{ext}")
        os.makedirs(os.path.dirname(safe_fname), exist_ok=True)
        if safe_fname != fname and os.path.exists(fname):
            os.replace(fname, safe_fname)
        return safe_fname, info

# ------------------- AUTO METADATA -------------------
def auto_metadata(mp3_path, title=None):
    """
    اضافه کردن خودکار تگ‌های ID3 به فایل MP3
    """
    try:
        if not mp3_path.lower().endswith('.mp3'):
            return False
        try:
            tags = ID3(mp3_path)
        except ID3NoHeaderError:
            tags = ID3()

        tags["TIT2"] = TIT2(encoding=3, text=title or "Audio")
        tags["TPE1"] = TPE1(encoding=3, text=CHANNEL_TAG)
        tags["TALB"] = TALB(encoding=3, text=CHANNEL_TAG)
        tags["TPE2"] = TPE2(encoding=3, text=CHANNEL_TAG)
        tags["COMM"] = COMM(encoding=3, lang="eng", desc="Comment",
                            text=f"🎵 Downloaded from {CHANNEL_TAG}")
        tags["TCON"] = TCON(encoding=3, text="Music")
        tags.save(mp3_path)
        return True
    except Exception as e:
        logger.exception("ID3 write failed: %s", e)
        return False

# ------------------- FINALIZE AUDIO FILE -------------------
def finalize_audio_file(path, title=None):
    """
    فایل mp3 را آماده انتشار می‌کند:
    - متادیتا می‌زند
    - نام فایل را با عنوان آهنگ هماهنگ می‌کند
    """
    if path.lower().endswith(".mp3"):
        auto_metadata(path, title)
        dir_path = os.path.dirname(path)
        ext = os.path.splitext(path)[1]
        title_safe = re.sub(r'[^A-Za-z0-9\.\-_ء-ي ]', '_', title or 'audio').strip()
        new_path = os.path.join(dir_path, f"{title_safe}{ext}")
        os.makedirs(dir_path, exist_ok=True)
        if new_path != path and os.path.exists(path):
            os.replace(path, new_path)
            path = new_path
    return path
