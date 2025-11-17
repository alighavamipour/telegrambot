import re, os, logging
from config import DOWNLOAD_PATH, REQUIRED_CHANNELS, CHANNEL_ID
from datetime import datetime

logger = logging.getLogger(__name__)
os.makedirs(DOWNLOAD_PATH, exist_ok=True)

def clean_caption(text):
    if not text:
        return ""
    t = re.sub(r'@\w+', '', text)                # remove mentions
    t = re.sub(r'http\S+', '', t)                # remove links
    t = re.sub(r'#\w+', '', t)                   # remove hashtags
    return t.strip()

def user_display_name(user):
    fn = user.first_name or ""
    ln = user.last_name or ""
    return (fn + (" " + ln if ln else "")).strip() or "ناشناس"

def make_channel_caption(channel_id):
    # clickable link (t.me) and visible identifier on new line
    if channel_id.startswith("@"):
        return f"https://t.me/{channel_id.lstrip('@')}"
    return str(channel_id)

def check_membership(bot, user_id):
    try:
        for ch in REQUIRED_CHANNELS:
            st = bot.get_chat_member(ch, user_id)
            if st.status in ['left', 'kicked']:
                return False
        return True
    except Exception as e:
        logger.exception("membership check failed: %s", e)
        return False

# --- yt-dlp downloader (works for SoundCloud and many sources) ---
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

# --- Mutagen: write ID3 tags so car players show channel id ---
from mutagen.easyid3 import EasyID3
from mutagen.mp3 import MP3
def write_id3_channel_tag(mp3_path, channel_id='@voxxboxx'):
    try:
        if not mp3_path.lower().endswith('.mp3'):
            return False
        audio = MP3(mp3_path, ID3=EasyID3)
        # set album/artist/comment fields to include channel id
        try:
            audio.add_tags()
        except Exception:
            pass
        audio_tags = {}
        # preserve title/artist if exist
        if 'title' in audio:
            audio_tags['title'] = audio['title'][0]
        else:
            audio_tags['title'] = os.path.basename(mp3_path)
        audio_tags['artist'] = audio.get('artist', [channel_id])[0]
        # put channel id in comment
        audio['comment'] = [f'Channel: {channel_id}']
        # ensure artist includes channel id for visibility
        audio['artist'] = [audio_tags['artist']]
        audio.save()
        return True
    except Exception as e:
        logger.exception("ID3 write failed: %s", e)
        return False
