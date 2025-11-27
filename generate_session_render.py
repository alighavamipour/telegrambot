from pyrogram import Client

API_ID = 32923
API_HASH = "69f10fcac82f9ac6617b401cfb97e675"

app = Client("userbot.session", api_id=API_ID, api_hash=API_HASH)

app.start()
print("Session ساخته شد ✅")
app.stop()
