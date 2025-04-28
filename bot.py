import os  
import logging  
import requests  
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup  
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext  

# تنظیمات لاگین  
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)  
logger = logging.getLogger(__name__)  

# توکن تلگرام و آدرس API TON از متغیرهای محیطی  
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']  
TON_API_URL = os.environ['TON_API_URL']  

# تعریف پک‌های شماره  
packs = {  
    "basic": {  
        "price": 0.1,  
        "numbers": ["09120000001", "09120000002"],  
    },  
    "premium": {  
        "price": 0.2,  
        "numbers": ["09130000001", "09130000002"],  
    },  
    "ultimate": {  
        "price": 0.5,  
        "numbers": ["09140000001", "09140000002"],  
    },  
}  

def start(update: Update, context: CallbackContext):  
    update.message.reply_text('سلام! برای خرید پک شماره‌ها لطفاً دستور /buy را ارسال کنید.')  

def buy(update: Update, context: CallbackContext):  
    keyboard = []  
    for pack_name, pack_details in packs.items():  
        keyboard.append([InlineKeyboardButton(f"{pack_name.capitalize()} - ${pack_details['price']}", callback_data=pack_name)])  

    reply_markup = InlineKeyboardMarkup(keyboard)  
    update.message.reply_text('لطفاً یک پک را انتخاب کنید:', reply_markup=reply_markup)  

def button(update: Update, context: CallbackContext):  
    query = update.callback_query  
    query.answer()  

    pack_name = query.data  
    pack_details = packs[pack_name]  
    
    user_id = query.from_user.id  
    payment_url = f"{TON_API_URL}/create_payment?user_id={user_id}&amount={pack_details['price']}"  
    response = requests.get(payment_url)  

    if response.status_code == 200:  
        payment_data = response.json()  
        payment_address = payment_data.get("payment_address")  
        query.edit_message_text(text=f'لینک پرداخت برای پک {pack_name}: {payment_address}\nلطفاً پرداخت را انجام دهید.')  
    else:  
        query.edit_message_text(text='خطایی در ایجاد درخواست پرداخت پیش آمد.')  

def handle_payment_confirmation(update: Update, context: CallbackContext):  
    user_id = update.message.from_user.id  
    # اینجا باید کدی برای تایید پرداخت کاربر نوشته شود  
    if confirm_payment(user_id):  # تأیید پرداخت  
        # ارسال شماره‌ها بعد از تأیید پرداخت  
        query.edit_message_text(text='پرداخت شما تأیید شد. لیست شماره‌ها:')  
        # اینجا باید لیست شماره‌های مربوط به پک خریداری شده ارسال شود  
        # می‌توانید شماره‌ها را از packs با توجه به user_id بخوانید  
    else:  
        update.message.reply_text('پرداخت شما تأیید نشد.')  

def confirm_payment(user_id: int):  
    # اینجا باید کدی برای تأیید پرداخت نوشته شود  
    # مانند بررسی وضعیت پرداخت از API درگاه TON  
    return True  

def main():  
    updater = Updater(TELEGRAM_TOKEN)  
    dp = updater.dispatcher  

    # افزودن هندلرهای مختلف  
    dp.add_handler(CommandHandler("start", start))  
    dp.add_handler(CommandHandler("buy", buy))  
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_payment_confirmation))  
    dp.add_handler(MessageHandler(Filters.update.callback_query, button))  

    updater.start_polling()  
    updater.idle()  

if __name__ == '__main__':  
    main()  
