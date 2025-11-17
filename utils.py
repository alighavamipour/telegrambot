import re, os, logging
from config import DOWNLOAD_PATH, REQUIRED_CHANNELS, CHANNEL_ID
from datetime import datetime

logger = logging.getLogger(__name__)
os.makedirs(DOWNLOAD_PATH, exist_ok=True)

# ------------------- CLEAN CAPTION -------------------
def clean_caption(text):
    if not text:
        return ""
    t = re.sub(r'@\w+', '', text)
    t = re.sub(r'http\S+', '', t)
    t = re.sub(r'#\w+', '', t)
    return t.strip()

# ------------------- USER DISPLAY NAME -------------------
def user_display_name(user):
    fn = user.first_name or ""
    ln = user.last_name or ""
    return (fn + (" " + ln if ln else "")).strip() or "ناشناس"

# ------------------- MAKE CHANNEL CAPTION -------------------
def make_channel_caption(channel_id, song_title=None):
    """ کپشن جذاب برای فایل‌های منتشر شده """
    base_title = f"🎵 {song_title}" if song_title else ""
    base = f"{base_title}\n📢 کانال ما: https://t.me/{channel_id.lstrip('@')}\n✨ از شنیدن لذت ببرید!"
    return base

# ------------------- CHECK MEMBERSHIP -------------------
def check_membership(bot, user_id):
    try:
        for ch in REQUIRED_CHANNELS:
            member = bot.get_chat_member(ch, user_id)
            if member.status not in ['left', 'kicked']:
                return True
        return False
    except Exception as e:
        logging.error("Membership check failed: %s", e)
        return False

# ============================================================
#                  YT-DLP UNIVERSAL DOWNLOADER
# ============================================================
from yt_dlp import YoutubeDL

def download_with_ytdlp(url, outdir=DOWNLOAD_PATH, filename_prefix=None):
    os.makedirs(outdir, exist_ok=True)
    outtmpl = os.path.join(outdir, (filename_prefix or '%(id)s') + '.%(ext)s')

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
        return fname, info

# ============================================================
#                 AUTO METADATA (FULL ID3 TAGGING)
# ============================================================
from mutagen.id3 import (
    ID3, TIT2, TALB, TPE1, TPE2, COMM, TCON,
    ID3NoHeaderError
)

CHANNEL_TAG = "@voxxboxx"

def auto_metadata(mp3_path, title=None):
    """ نوشتن خودکار متادیتای mp3 با اسم واقعی آهنگ """
    try:
        if not mp3_path.lower().endswith('.mp3'):
            return False

        try:
            tags = ID3(mp3_path)
        except ID3NoHeaderError:
            tags = ID3()

        song_title = title or "Audio"

        # ---------------- SET FULL METADATA ----------------
        tags["TIT2"] = TIT2(encoding=3, text=song_title)  # Song title
        tags["TPE1"] = TPE1(encoding=3, text=CHANNEL_TAG)  # Artist
        tags["TALB"] = TALB(encoding=3, text=CHANNEL_TAG)  # Album
        tags["TPE2"] = TPE2(encoding=3, text=CHANNEL_TAG)  # Performer
        tags["COMM"] = COMM(
            encoding=3, lang="eng", desc="Comment",
            text=f"🎵 {song_title} — دانلود شده از {CHANNEL_TAG}"
        )
        tags["TCON"] = TCON(encoding=3, text="Music")       # Genre

        tags.save(mp3_path)
        return True

    except Exception as e:
        logger.exception("ID3 write failed: %s", e)
        return False

# ============================================================
#            AUTO APPLY METADATA AFTER ANY DOWNLOAD
# ============================================================
def finalize_audio_file(path, title=None):
    """
    هر فایلی که دانلود شد → اگر mp3 بود، اتوماتیک متادیتا بزن
    title: اسم واقعی آهنگ
    """
    if path.lower().endswith(".mp3"):
        auto_metadata(path, title=title)
    return path
