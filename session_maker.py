from pyrogram import Client
import asyncio
import os

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH"))

async def main():
    async with Client("gen_session", api_id=API_ID, api_hash=API_HASH) as app:
        print("\n\nSESSION_STRING:")
        print(await app.export_session_string())
        print("\n\nCopy this and update SESSION_STRING in Render.\n\n")

asyncio.run(main())
