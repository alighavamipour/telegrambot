# main.py
post_id = database.add_post(message.audio.file_id, 'audio', message.audio.title or '', uploader_name, user.id)


# send a copy to owner for review of SoundCloud-origin files (requirement)
try:
# send a private copy to owner
bot.send_audio(OWNER_ID, message.audio.file_id, caption=f"نسخه بررسی: {uploader_name}")
except Exception as e:
logger.warning('Could not send PV to owner: %s', e)


# decide if user is owner (VIP capabilities): if owner or VIP then can post directly
if user.id == OWNER_ID or database.is_vip(user.id):
try:
sent = bot.send_audio(chat_id=CHANNEL_ID, audio=message.audio.file_id, caption=new_caption)
database.mark_posted(post_id, getattr(sent, 'message_id', None) or None)
bot.reply_to(message, 'آهنگ شما با موفقیت پست شد.')
except Exception as e:
bot.reply_to(message, f'خطا در ارسال به کانال: {e}')
else:
# normal user: post on channel but with uploader name shown
try:
sent = bot.send_audio(chat_id=CHANNEL_ID, audio=message.audio.file_id, caption=new_caption + f"
ارسال‌شده توسط: {uploader_name}")
database.mark_posted(post_id, getattr(sent, 'message_id', None) or None)
bot.reply_to(message, 'آهنگ شما ثبت شد و در کانال منتشر شد.')
except Exception as e:
bot.reply_to(message, f'خطا در ارسال به کانال: {e}')


# handle video/document
@bot.message_handler(content_types=['video','document'])
@require_membership
def handle_video(message):
user = message.from_user
uploader_name = (user.first_name or '') + (' ' + (user.last_name or '') if user.last_name else '')
uploader_name = uploader_name.strip() or 'ناشناس'
new_caption = f"{CHANNEL_ID}"


# choose sending method
file_id = None
if message.content_type == 'video':
file_id = message.video.file_id
media_type = 'video'
else:
file_id = message.document.file_id
media_type = 'video'


post_id = database.add_post(file_id, media_type, message.caption or '', uploader_name, user.id)


try:
sent = bot.send_document(chat_id=CHANNEL_ID, data=file_id, caption=new_caption)
database.mark_posted(post_id, getattr(sent, 'message_id', None) or None)
bot.reply_to(message, 'ویدئو/سریال شما در کانال منتشر شد.')
except Exception as e:
bot.reply_to(message, f'خطا در ارسال: {e}')


# SoundCloud downloader (Owner only triggers)
@bot.message_handler(commands=['sc'])
def sc_download_cmd(message):
if message.from_user.id != OWNER_ID:
bot.reply_to(message, 'این قابلیت فقط برای مالک ربات فعال است.')
return
bot.reply_to(message, 'لطفاً لینک S
