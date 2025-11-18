MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

# ------------------- HELPERS -------------------
def get_file_info(message):
    """شناسایی file_id و file_name و media_type برای همه نوع فایل"""
    if message.content_type == 'audio':
        file_id = message.audio.file_id
        file_name = message.audio.file_name or message.audio.title or f"audio_{int(time.time())}.mp3"
        media_type = 'audio'
        file_size = getattr(message.audio, 'file_size', None)
    elif message.content_type == 'voice':
        file_id = message.voice.file_id
        file_name = f"voice_{int(time.time())}.ogg"
        media_type = 'audio'
        file_size = getattr(message.voice, 'file_size', None)
    elif message.content_type == 'video':
        file_id = message.video.file_id
        file_name = message.video.file_name or f"video_{int(time.time())}.mp4"
        media_type = 'video'
        file_size = getattr(message.video, 'file_size', None)
    elif message.content_type == 'document':
        file_id = message.document.file_id
        file_name = message.document.file_name or f"file_{int(time.time())}"
        media_type = 'document'
        file_size = getattr(message.document, 'file_size', None)
    else:
        return None, None, None, None
    return file_id, file_name, media_type, file_size

def add_channel_metadata(file_path, channel_name):
    """اضافه کردن اطلاعات کانال به متادیتای فایل صوتی"""
    from mutagen.easyid3 import EasyID3
    from mutagen.id3 import ID3NoHeaderError
    try:
        try:
            audio = EasyID3(file_path)
        except ID3NoHeaderError:
            audio = EasyID3()
            audio.save(file_path)
            audio = EasyID3(file_path)

        title = audio.get('title', [os.path.basename(file_path)])[0]
        audio['title'] = title
        audio['artist'] = channel_name
        audio['comment'] = f"Published via {channel_name}"
        audio.save(file_path)
    except Exception as e:
        logger.warning("Cannot add metadata to audio file: %s", e)

def extract_soundcloud_link(text):
    """جدا کردن لینک SoundCloud از متن اضافی"""
    import re
    pattern = r'(https?://(?:www\.)?soundcloud\.com/[^\s]+)'
    match = re.search(pattern, text)
    if match:
        return match.group(1)
    return None

# ------------------- MEDIA HANDLER -------------------
@bot.message_handler(content_types=['audio','video','document','voice'])
@require_membership
def media_handler(message):
    user = message.from_user
    uid = user.id
    database.add_or_update_user(uid, user.first_name or "", user.last_name or "", getattr(user, 'username', '') or "")

    file_id, file_name, media_type, file_size = get_file_info(message)
    if not file_id:
        bot.reply_to(message, "❌ نوع فایل پشتیبانی نمی‌شود.")
        return

    # بررسی حجم فایل
    if file_size and file_size > MAX_FILE_SIZE:
        bot.reply_to(message, f"❌ حجم فایل بیشتر از 50MB است ({file_size/1024/1024:.2f}MB) و نمی‌توان آن را پردازش کرد.")
        return

    # پیام اولیه به کاربر
    processing_msg = bot.reply_to(message, "📥 فایل دریافت شد و در حال پردازش است… لطفاً صبر کنید.")

    # دانلود فایل
    try:
        finfo = bot.get_file(file_id)
        data = bot.download_file(finfo.file_path)
        safe_name = re.sub(r'[^A-Za-z0-9\.\-_ء-ي ]', '_', file_name)
        local_path = os.path.join(DOWNLOAD_PATH, safe_name)
        with open(local_path, 'wb') as f:
            f.write(data)
    except Exception as e:
        logger.exception("download error: %s", e)
        bot.edit_message_text("❌ خطا در دریافت فایل.", processing_msg.chat.id, processing_msg.message_id)
        return

    # finalize audio file if audio
    if media_type == 'audio':
        utils.finalize_audio_file(local_path, file_name)
        add_channel_metadata(local_path, CHANNEL_ID)

    # caption
    caption = f"🎵 {file_name}\n📌 {utils.make_channel_caption(CHANNEL_ID)}"
    database.add_post(local_path, file_id, safe_name, media_type, file_name, utils.user_display_name(user), uid)

    # send to channel
    try:
        with open(local_path, 'rb') as fh:
            if media_type == 'audio':
                bot.send_audio(CHANNEL_ID, fh, caption=caption, title=file_name)
            elif media_type == 'video':
                bot.send_video(CHANNEL_ID, fh, caption=caption)
            else:
                bot.send_document(CHANNEL_ID, fh, caption=caption)
        bot.edit_message_text(f"✅ فایل شما با موفقیت در کانال منتشر شد.\n📌 برای مشاهده به کانال مراجعه کنید.", processing_msg.chat.id, processing_msg.message_id)
    except Exception as e:
        logger.exception("post to channel error: %s", e)
        bot.edit_message_text(f"❌ خطا در ارسال به کانال: {e}", processing_msg.chat.id, processing_msg.message_id)

# ------------------- SOUNDCLOUD HANDLER -------------------
@bot.message_handler(func=lambda m: isinstance(m.text, str) and 'soundcloud.com' in m.text.lower())
@require_membership
def sc_handler(message):
    user = message.from_user
    uid = user.id
    database.add_or_update_user(uid, user.first_name or "", user.last_name or "", getattr(user, 'username', '') or "")

    link = extract_soundcloud_link(message.text)
    if not link:
        bot.reply_to(message, "❌ لینک SoundCloud معتبر پیدا نشد.")
        return

    processing_msg = bot.reply_to(message, "📥 لینک دریافت شد و در حال دانلود فایل… لطفاً صبر کنید.")

    try:
        local_path, info = utils.download_with_ytdlp(link, outdir=DOWNLOAD_PATH)
        title = info.get('title', 'SoundCloud Track')
        utils.finalize_audio_file(local_path, title)
        add_channel_metadata(local_path, CHANNEL_ID)

        caption = f"🎵 {title}\n📌 {utils.make_channel_caption(CHANNEL_ID)}"
        with open(local_path, 'rb') as fh:
            bot.send_audio(CHANNEL_ID, fh, caption=caption, title=title)

        bot.edit_message_text(f"✅ فایل SoundCloud دانلود و در کانال منتشر شد.\n📌 برای مشاهده به کانال مراجعه کنید.", processing_msg.chat.id, processing_msg.message_id)
        database.add_post(local_path, None, os.path.basename(local_path), 'soundcloud', title, utils.user_display_name(user), uid)
    except Exception as e:
        logger.exception("SoundCloud download error: %s", e)
        bot.edit_message_text(f"❌ دانلود ناموفق: {e}", processing_msg.chat.id, processing_msg.message_id)

# ------------------- UNKNOWN MESSAGE HANDLER -------------------
@bot.message_handler(func=lambda m: True)
def unknown_message_handler(message):
    bot.reply_to(message,
                 "❌ ربات این پیام را نمی‌شناسد.\n\n"
                 "📌 لطفاً یک فایل صوتی، ویدئو، داکیومنت یا لینک SoundCloud ارسال کنید.\n"
                 "برای راهنمایی بیشتر از /help استفاده کنید.")
