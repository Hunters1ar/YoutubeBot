import asyncio
import os
import shutil
import uuid
from pathlib import Path
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, WebAppInfo

import downloader

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN or BOT_TOKEN == "your_telegram_bot_token_here":
    raise ValueError("Please set your BOT_TOKEN in the .env file")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

YOUTUBE_DOMAINS = ["youtube.com", "youtu.be", "www.youtube.com", "m.youtube.com"]

# In-memory dictionary to store video urls for callbacks
url_store = {}

def is_youtube_url(text: str) -> bool:
    try:
        return any(domain in text.lower() for domain in YOUTUBE_DOMAINS)
    except Exception:
        return False

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if Path("hello.webm").exists():
        await message.answer_sticker(FSInputFile("hello.webm"))
    await message.answer(
        "👋 **Welcome to YouTube Downloader Bot!** 🎬\n\n"
        "Send me any YouTube video, Short, or music link to download it as MP3 audio or MP4 video in high quality!",
        parse_mode="Markdown"
    )

@dp.message(F.text)
async def handle_url(message: types.Message):
    url = message.text.strip()
    if not is_youtube_url(url):
        return
        
    status_msg = await message.reply("⏳ Fetching video information...")
    
    try:
        info = await asyncio.to_thread(downloader.get_video_info, url)
        
        # Build Apisyu conversion keyboard with Telegram WebApps & Direct Links
        keyboard_buttons = [
            [
                InlineKeyboardButton(
                    text="🎵 Download MP3", 
                    web_app=WebAppInfo(url=info["mp3_url"])
                ),
                InlineKeyboardButton(
                    text="🎬 Download MP4", 
                    web_app=WebAppInfo(url=info["mp4_url"])
                )
            ],
            [
                InlineKeyboardButton(
                    text="⚡ All-in-One Converter", 
                    web_app=WebAppInfo(url=info["widget_url"])
                )
            ],
            [
                InlineKeyboardButton(
                    text="🖼️ High-Res Thumbnail", 
                    callback_data="dl_thumb"
                ),
                InlineKeyboardButton(
                    text="🌐 Browser Link", 
                    url=info["mp4_url"]
                )
            ]
        ]
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        # Safely escape title and channel
        title = info.get('title', 'YouTube Video').replace('*', '').replace('_', '')
        channel = info.get('channel', 'YouTube Channel').replace('*', '').replace('_', '')
        
        caption = (
            f"🎬 *{title}*\n"
            f"👤 *Channel:* {channel}\n\n"
            f"⚡ *Select download format below:*"
        )
        
        photo_msg = await message.answer_photo(
            photo=info["thumbnail"],
            caption=caption,
            parse_mode="Markdown",
            reply_markup=keyboard,
            reply_to_message_id=message.message_id
        )
        url_store[photo_msg.message_id] = url
        
        await status_msg.delete()
        
    except Exception as e:
        await status_msg.edit_text(f"❌ Failed to fetch video info:\n{str(e)[:500]}")

@dp.callback_query(F.data == "dl_thumb")
async def handle_thumb_callback(callback_query: types.CallbackQuery):
    msg_id = callback_query.message.message_id
    if msg_id not in url_store:
        await callback_query.answer("Session expired or URL not found.", show_alert=True)
        return
        
    url = url_store[msg_id]
    await callback_query.answer("Sending high-resolution thumbnail...")
    
    try:
        info = await asyncio.to_thread(downloader.get_video_info, url)
        if info.get("thumbnail"):
            await bot.send_document(
                chat_id=callback_query.message.chat.id,
                document=info["thumbnail"],
                caption=f"🖼️ Thumbnail for: *{info.get('title', 'Video')}*",
                parse_mode="Markdown",
                reply_to_message_id=callback_query.message.reply_to_message.message_id if callback_query.message.reply_to_message else None
            )
        else:
            await callback_query.answer("No thumbnail available.", show_alert=True)
    except Exception as e:
        await callback_query.answer(f"Error: {str(e)[:100]}", show_alert=True)

async def main():
    print("Starting bot with Apisyu integration...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
