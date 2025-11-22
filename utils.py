import re, os, logging
from config import DOWNLOAD_PATH, REQUIRED_CHANNELS, CHANNEL_ID
from yt_dlp import YoutubeDL
from mutagen.id3 import ID3, TIT2, TALB, TPE1, TPE2, COMM, TCON, ID3NoHeaderError

logger = logging.getLogger(__name__)
os.makedirs(DOWNLOAD_PATH, exist_ok=True)

CHANNEL_TAG = CHANNEL_ID if CHANNEL_ID.startswith("@") else f"@{CHANNEL_ID}"

# ------------------- CLEAN CAPTION -------------------
def clean_caption(text):
    if not text: return ""
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
def make_channel_caption(channel_id=None):
    ch = channel_id or CHANNEL_ID
    return f"https://t.me/{ch.lstrip('@')}"


# ------------------- CHECK MEMBERSHIP -------------------
def check_membership(bot, user_id):
    try:
        for ch in REQUIRED_CHANNELS:
            member = bot.get_chat_member(ch, user_id)
            if member.status not in ['left', 'kicked']:
                return True
        return False
    except Exception as e:
        logger.error("Membership check failed: %s", e)
        return False


#################################
#     NEW DOWNLOADER
#################################

def ytdlp_download(url, outdir, quality=None, audio_only=False):
    os.makedirs(outdir, exist_ok=True)

    opts = {
        "outtmpl": f"{outdir}/%(title)s.%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": False
    }

    if audio_only:
        opts['format'] = 'bestaudio/best'
        opts['postprocessors'] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3"
        }]
    else:
        if quality:
            opts['format'] = f"bestvideo[height<={quality}]+bestaudio/best"
        else:
            opts['format'] = "bestvideo+bestaudio/best"

    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        file = ydl.prepare_filename(info)

    return file, info


def get_thumbnail(info):
    try:
        if "thumbnail" in info:
            return info['thumbnail']
    except:
        pass
    return None
