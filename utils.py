from telebot import TeleBot

bot = None  # Will be set in main

def set_bot(t):
    global bot
    bot = t

def check_membership(user_id, channel_id):
    try:
        member = bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except:
        return False

def clean_caption(caption):
    # Remove mentions, hashtags, links
    import re
    caption = re.sub(r'@\w+', '', caption)
    caption = re.sub(r'#\w+', '', caption)
    caption = re.sub(r'http\S+', '', caption)
    return caption.strip()

def is_owner(user_id, owner_id):
    return user_id == owner_id
