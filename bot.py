import os  
import logging  
import requests  
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup  
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, CallbackContext  

# تنظیمات لاگین  
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)  
logger = logging.getLogger(__name__)  

# توکن تلگرام و آدرس API TON از متغیرهای محیطی  
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']  
TON_API_URL = os.environ['TON_API_URL']  
TON_API_KEY = os.environ['TON_API_KEY']  # کلید API اضافه شد  

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
    payment_url = f"{TON_API_URL}/createPayment"  # URL ایجاد پرداخت  
    headers = {  
        "x-api-key": TON_API_KEY,  
        "Content-Type": "application/json"  
    }  
    payload = {  
        "user_id": user_id,  
        "amount": pack_details['price']  
    }  

    # ارسال درخواست به API TonCenter  
    response = requests.post(payment_url, headers=headers, json=payload)  

    if response.status_code == 200:  
        payment_data = response.json()  
        payment_address = payment_data.get("payment_address")  
        query.edit_message_text(text=f'لینک پرداخت برای پک {pack_name}: {payment_address}\nلطفاً پرداخت را انجام دهید.')  
    else:  
        query.edit_message_text(text='خطایی در ایجاد درخواست پرداخت پیش آمد.')  

def handle_payment_confirmation(update: Update, context: CallbackContext):  
    user_id = update.message.from_user.id  
    # تأیید پرداخت  
    if confirm_payment(user_id):  
        pack_name = "basic"  # فرض بر این است که پک انتخاب شده "basic" است  
        numbers = packs[pack_name]['numbers']  
        update.message.reply_text(f'پرداخت شما تأیید شد. لیست شماره‌ها:\n{", ".join(numbers)}')  
    else:  
        update.message.reply_text('پرداخت شما تأیید نشد.')  

def confirm_payment(user_id: int):  
    # منطق تأیید پرداخت  
    return True  

async def main():  
    application = Application.builder().token(TELEGRAM_TOKEN).build()  # استفاده از Application به جای Updater  

    # افزودن هندلرها  
    application.add_handler(CommandHandler("start", start))  
    application.add_handler(CommandHandler("buy", buy))  
    application.add_handler(CallbackQueryHandler(button))  # استفاده از CallbackQueryHandler برای مدیریت callback  
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_payment_confirmation))  

    await application.initialize()  
    await application.start_polling()  # استفاده از run_polling به جای start_polling  
    await application.idle()  

if __name__ == '__main__':  
    import asyncio  
    asyncio.run(main())  # اجرای main به صورت asyncio  
