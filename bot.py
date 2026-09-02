import asyncio
import os
import shutil
import uuid
from pathlib import Path
from dotenv import load_dotenv

# Clean PM2 IPC channel variables that break child processes
os.environ.pop("NODE_CHANNEL_FD", None)

import aiohttp
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile,
    ReplyKeyboardMarkup,
    KeyboardButton,
)

import downloader

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN or BOT_TOKEN == "your_telegram_bot_token_here":
    raise ValueError("Please set your BOT_TOKEN in the .env file")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ─── Config ──────────────────────────────────────────────────────────────────
API_BASE = os.getenv("API_BASE", "https://api.hunterstar.uz")
TRACKS_PER_PAGE = 8

# ─── Persistent Telegram Audio Cache ──────────────────────────────────────────
AUDIO_CACHE_FILE = Path("audio_cache.json")
audio_file_id_cache: dict[str, str] = {}

def load_audio_cache():
    global audio_file_id_cache
    if AUDIO_CACHE_FILE.exists():
        try:
            with open(AUDIO_CACHE_FILE, "r", encoding="utf-8") as f:
                audio_file_id_cache = json.load(f)
        except Exception:
            audio_file_id_cache = {}

def save_audio_cache():
    try:
        with open(AUDIO_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(audio_file_id_cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

load_audio_cache()

# ─── In-memory cache ────────────────────────────────────────────────────────
tracks_cache: list[dict] = []          # [{ id, title, thumb, source }]
playlists_cache: list[str] = []        # unique playlist names
cache_lock = asyncio.Lock()
last_cache_time: float = 0
CACHE_TTL = 300  # 5 minutes (matches API cache)

# ─── Per-user state for search/filter ────────────────────────────────────────
user_state: dict[int, dict] = {}
# user_state[user_id] = { "filter": "All sources" | playlist_name,
#                          "search": "" | query,
#                          "page": 0 }


async def fetch_tracks() -> tuple[list[dict], list[str]]:
    """Fetch all tracks from hunterstar.uz API."""
    import time

    global tracks_cache, playlists_cache, last_cache_time

    async with cache_lock:
        now = time.time()
        if tracks_cache and (now - last_cache_time) < CACHE_TTL:
            return tracks_cache, playlists_cache

    async with aiohttp.ClientSession() as session:
        async with session.get(f"{API_BASE}/api/playlists") as resp:
            if resp.status != 200:
                raise Exception(f"API returned {resp.status}")
            data = await resp.json()

    if not data.get("ok"):
        raise Exception("API returned error")

    all_tracks = []
    all_sources = set()
    for playlist in data.get("playlists", []):
        source = playlist.get("title", "Unknown")
        all_sources.add(source)
        for video in playlist.get("videos", []):
            all_tracks.append({
                "id": video["id"],
                "title": video["title"],
                "thumb": video.get("thumb", ""),
                "source": source,
            })

    async with cache_lock:
        tracks_cache = all_tracks
        playlists_cache = sorted(all_sources)
        last_cache_time = __import__("time").time()

    return all_tracks, sorted(all_sources)


def get_user_state(user_id: int) -> dict:
    if user_id not in user_state:
        user_state[user_id] = {"filter": "All sources", "search": "", "page": 0}
    return user_state[user_id]


def filter_tracks(tracks: list[dict], state: dict) -> list[dict]:
    """Apply search query and playlist filter to tracks."""
    result = tracks

    # Filter by source/playlist
    if state["filter"] != "All sources":
        result = [t for t in result if t["source"] == state["filter"]]

    # Filter by search query
    if state["search"]:
        query = state["search"].lower()
        result = [t for t in result if query in t["title"].lower()]

    return result


def build_track_keyboard(tracks: list[dict], page: int, total_pages: int) -> InlineKeyboardMarkup:
    """Build inline keyboard with track buttons and pagination."""
    buttons = []

    start = page * TRACKS_PER_PAGE
    end = start + TRACKS_PER_PAGE
    page_tracks = tracks[start:end]

    for track in page_tracks:
        # Truncate title to fit Telegram's 64-char callback data limit
        title = track["title"]
        if len(title) > 35:
            title = title[:32] + "..."
        buttons.append([
            InlineKeyboardButton(
                text=f"🎵 {title}",
                callback_data=f"play_{track['id']}"
            )
        ])

    # Pagination row
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="◀️ Prev", callback_data=f"page_{page - 1}"))
    nav_row.append(InlineKeyboardButton(text=f"📄 {page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="Next ▶️", callback_data=f"page_{page + 1}"))
    buttons.append(nav_row)

    # Filter and search row
    buttons.append([
        InlineKeyboardButton(text="📂 Playlists", callback_data="show_playlists"),
        InlineKeyboardButton(text="🔍 Search", callback_data="search_prompt"),
        InlineKeyboardButton(text="🔄 Refresh", callback_data="refresh_catalog"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Persistent reply keyboard for quick access."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎵 Browse Music"), KeyboardButton(text="🔍 Search")],
            [KeyboardButton(text="📂 Playlists")],
        ],
        resize_keyboard=True,
    )


# ─── /start ──────────────────────────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if Path("hello.webm").exists():
        try:
            await message.answer_sticker(FSInputFile("hello.webm"))
        except Exception:
            pass

    await message.answer(
        "👋 **Welcome to Hunterstar Music!** 🎵\n\n"
        "Browse anime soundtracks from [hunterstar.uz](https://hunterstar.uz/playlist) "
        "and download them as MP3.\n\n"
        "🎵 **Browse Music** — view the full catalog\n"
        "🔍 **Search** — find a specific track\n"
        "📂 **Playlists** — browse by playlist",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
        disable_web_page_preview=True,
    )


# ─── Browse Music ────────────────────────────────────────────────────────────

@dp.message(Command("music"))
@dp.message(F.text == "🎵 Browse Music")
async def cmd_browse(message: types.Message):
    state = get_user_state(message.from_user.id)
    state["page"] = 0
    state["search"] = ""

    status_msg = await message.answer("⏳ Loading catalog...")

    try:
        tracks, _ = await fetch_tracks()
        filtered = filter_tracks(tracks, state)

        if not filtered:
            await status_msg.edit_text("😔 No tracks found in the catalog.")
            return

        total_pages = max(1, (len(filtered) + TRACKS_PER_PAGE - 1) // TRACKS_PER_PAGE)

        source_label = f" — {state['filter']}" if state["filter"] != "All sources" else ""
        header = f"🎵 **Hunterstar Music{source_label}**\n📀 {len(filtered)} tracks\n\nSelect a track to download as MP3:"

        keyboard = build_track_keyboard(filtered, 0, total_pages)
        await status_msg.edit_text(header, reply_markup=keyboard, parse_mode="Markdown")

    except Exception as e:
        await status_msg.edit_text(f"❌ Failed to load catalog:\n{str(e)[:300]}")


# ─── Search ──────────────────────────────────────────────────────────────────

@dp.message(Command("search"))
async def cmd_search(message: types.Message):
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "🔍 **Search Music**\n\nSend your search query:\n`/search <track name>`\n\n"
            "Example: `/search naruto`",
            parse_mode="Markdown",
        )
        return

    query = parts[1].strip()
    await _do_search(message, query)


@dp.message(F.text == "🔍 Search")
async def cmd_search_button(message: types.Message):
    state = get_user_state(message.from_user.id)
    state["search"] = "__AWAITING__"
    await message.answer(
        "🔍 **Search Music**\n\nType the track name you're looking for:",
        parse_mode="Markdown",
    )


@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: types.Message):
    state = get_user_state(message.from_user.id)

    # If user was prompted for search, treat this as a search query
    if state.get("search") == "__AWAITING__":
        await _do_search(message, message.text.strip())
        return


async def _do_search(message: types.Message, query: str):
    state = get_user_state(message.from_user.id)
    state["search"] = query
    state["page"] = 0

    status_msg = await message.answer(f"🔍 Searching for **{query}**...", parse_mode="Markdown")

    try:
        tracks, _ = await fetch_tracks()
        filtered = filter_tracks(tracks, state)

        if not filtered:
            await status_msg.edit_text(
                f"😔 No tracks found for **\"{query}\"**.\n\nTry a different search term.",
                parse_mode="Markdown",
            )
            return

        total_pages = max(1, (len(filtered) + TRACKS_PER_PAGE - 1) // TRACKS_PER_PAGE)
        header = f"🔍 **Search: \"{query}\"**\n📀 {len(filtered)} tracks found\n\nSelect a track to download:"

        keyboard = build_track_keyboard(filtered, 0, total_pages)
        await status_msg.edit_text(header, reply_markup=keyboard, parse_mode="Markdown")

    except Exception as e:
        await status_msg.edit_text(f"❌ Search failed:\n{str(e)[:300]}")


# ─── Playlists ───────────────────────────────────────────────────────────────

@dp.message(Command("playlists"))
@dp.message(F.text == "📂 Playlists")
async def cmd_playlists(message: types.Message):
    status_msg = await message.answer("⏳ Loading playlists...")

    try:
        tracks, sources = await fetch_tracks()

        buttons = []
        # "All sources" option
        buttons.append([InlineKeyboardButton(text="🌐 All Sources", callback_data="filter_all")])

        for source in sources:
            count = len([t for t in tracks if t["source"] == source])
            buttons.append([
                InlineKeyboardButton(
                    text=f"📂 {source} ({count})",
                    callback_data=f"filter_{source[:50]}"
                )
            ])

        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        await status_msg.edit_text(
            "📂 **Playlists**\n\nSelect a playlist to browse:",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )

    except Exception as e:
        await status_msg.edit_text(f"❌ Failed to load playlists:\n{str(e)[:300]}")


# ─── Callback: Pagination ────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("page_"))
async def handle_page(callback_query: types.CallbackQuery):
    await callback_query.answer()

    page = int(callback_query.data.split("_")[1])
    state = get_user_state(callback_query.from_user.id)
    state["page"] = page

    try:
        tracks, _ = await fetch_tracks()
        filtered = filter_tracks(tracks, state)
        total_pages = max(1, (len(filtered) + TRACKS_PER_PAGE - 1) // TRACKS_PER_PAGE)

        source_label = f" — {state['filter']}" if state["filter"] != "All sources" else ""
        search_label = f"\n🔍 Search: \"{state['search']}\"" if state["search"] and state["search"] != "__AWAITING__" else ""
        header = f"🎵 **Hunterstar Music{source_label}**{search_label}\n📀 {len(filtered)} tracks\n\nSelect a track to download:"

        keyboard = build_track_keyboard(filtered, page, total_pages)
        await callback_query.message.edit_text(header, reply_markup=keyboard, parse_mode="Markdown")

    except Exception as e:
        await callback_query.message.edit_text(f"❌ Error: {str(e)[:300]}")


# ─── Callback: Filter by playlist ────────────────────────────────────────────

@dp.callback_query(F.data.startswith("filter_"))
async def handle_filter(callback_query: types.CallbackQuery):
    await callback_query.answer()

    filter_value = callback_query.data[7:]  # Remove "filter_" prefix
    state = get_user_state(callback_query.from_user.id)

    if filter_value == "all":
        state["filter"] = "All sources"
    else:
        state["filter"] = filter_value

    state["page"] = 0

    try:
        tracks, _ = await fetch_tracks()
        filtered = filter_tracks(tracks, state)

        if not filtered:
            await callback_query.message.edit_text(
                f"😔 No tracks found in **{state['filter']}**.",
                parse_mode="Markdown",
            )
            return

        total_pages = max(1, (len(filtered) + TRACKS_PER_PAGE - 1) // TRACKS_PER_PAGE)
        source_label = f" — {state['filter']}" if state["filter"] != "All sources" else ""
        header = f"🎵 **Hunterstar Music{source_label}**\n📀 {len(filtered)} tracks\n\nSelect a track to download:"

        keyboard = build_track_keyboard(filtered, 0, total_pages)
        await callback_query.message.edit_text(header, reply_markup=keyboard, parse_mode="Markdown")

    except Exception as e:
        await callback_query.message.edit_text(f"❌ Error: {str(e)[:300]}")


# ─── Callback: Show playlists ────────────────────────────────────────────────

@dp.callback_query(F.data == "show_playlists")
async def handle_show_playlists(callback_query: types.CallbackQuery):
    await callback_query.answer()

    try:
        tracks, sources = await fetch_tracks()

        buttons = []
        buttons.append([InlineKeyboardButton(text="🌐 All Sources", callback_data="filter_all")])

        for source in sources:
            count = len([t for t in tracks if t["source"] == source])
            buttons.append([
                InlineKeyboardButton(
                    text=f"📂 {source} ({count})",
                    callback_data=f"filter_{source[:50]}"
                )
            ])

        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        await callback_query.message.edit_text(
            "📂 **Playlists**\n\nSelect a playlist to browse:",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )

    except Exception as e:
        await callback_query.message.edit_text(f"❌ Error: {str(e)[:300]}")


# ─── Callback: Search prompt ─────────────────────────────────────────────────

@dp.callback_query(F.data == "search_prompt")
async def handle_search_prompt(callback_query: types.CallbackQuery):
    await callback_query.answer()
    state = get_user_state(callback_query.from_user.id)
    state["search"] = "__AWAITING__"
    await callback_query.message.reply(
        "🔍 **Search Music**\n\nType the track name you're looking for:",
        parse_mode="Markdown",
    )


# ─── Callback: Refresh catalog ───────────────────────────────────────────────

@dp.callback_query(F.data == "refresh_catalog")
async def handle_refresh(callback_query: types.CallbackQuery):
    global last_cache_time
    last_cache_time = 0  # Force cache refresh
    await callback_query.answer("🔄 Refreshing...")

    state = get_user_state(callback_query.from_user.id)
    state["page"] = 0

    try:
        tracks, _ = await fetch_tracks()
        filtered = filter_tracks(tracks, state)
        total_pages = max(1, (len(filtered) + TRACKS_PER_PAGE - 1) // TRACKS_PER_PAGE)

        source_label = f" — {state['filter']}" if state["filter"] != "All sources" else ""
        header = f"🎵 **Hunterstar Music{source_label}**\n📀 {len(filtered)} tracks\n\nSelect a track to download:"

        keyboard = build_track_keyboard(filtered, 0, total_pages)
        await callback_query.message.edit_text(header, reply_markup=keyboard, parse_mode="Markdown")

    except Exception as e:
        await callback_query.message.edit_text(f"❌ Refresh failed: {str(e)[:300]}")


# ─── Callback: No-op (page indicator) ────────────────────────────────────────

@dp.callback_query(F.data == "noop")
async def handle_noop(callback_query: types.CallbackQuery):
    await callback_query.answer()


# ─── Callback: Play / Download Track ─────────────────────────────────────────

@dp.callback_query(F.data.startswith("play_"))
async def handle_play(callback_query: types.CallbackQuery):
    video_id = callback_query.data[5:]  # Remove "play_" prefix

    try:
        await callback_query.answer("⏳ Starting download...")
    except Exception:
        pass

    # Find track info from cache (fetch if empty)
    global tracks_cache
    if not tracks_cache:
        try:
            await fetch_tracks()
        except Exception:
            pass

    track_title = "Audio Track"
    track_thumb = ""
    track_source = "Hunterstar Radio"
    for t in tracks_cache:
        if t["id"] == video_id:
            track_title = t["title"]
            track_thumb = t["thumb"]
            track_source = t["source"]
            break

    # Instant send if already cached on Telegram!
    if video_id in audio_file_id_cache:
        try:
            await bot.send_audio(
                chat_id=callback_query.message.chat.id,
                audio=audio_file_id_cache[video_id],
                title=track_title,
                performer=track_source,
                caption=f"🎵 *{track_title}*\n📂 {track_source}",
                parse_mode="Markdown",
            )
            return
        except Exception:
            # In case cached file_id expired, proceed to download
            pass

    # Send status message
    status_msg = await callback_query.message.reply(
        f"⏳ **Downloading:**\n🎵 {track_title}\n\nPlease wait...",
        parse_mode="Markdown",
    )

    # Send downloading sticker if available
    sticker_msg = None
    if Path("downloading.webm").exists():
        try:
            sticker_msg = await bot.send_sticker(
                chat_id=callback_query.message.chat.id,
                sticker=FSInputFile("downloading.webm"),
            )
        except Exception:
            pass

    job_id = str(uuid.uuid4())
    job_dir = Path("downloads") / job_id
    progress_dict = {}

    try:
        dl_task = asyncio.create_task(
            asyncio.to_thread(
                downloader.download_audio_by_id, video_id, job_id, progress_dict
            )
        )

        # Live progress updater (gentle polling to avoid Telegram rate-limits)
        last_text = ""
        while not dl_task.done():
            await asyncio.sleep(4)
            if job_id in progress_dict:
                progress_text = progress_dict[job_id]
                new_text = f"⏳ **Downloading:**\n🎵 {track_title}\n\n{progress_text}"
                if new_text != last_text:
                    try:
                        await status_msg.edit_text(new_text, parse_mode="Markdown")
                        last_text = new_text
                    except Exception:
                        pass

        filepath = dl_task.result()

        if not filepath or not filepath.exists():
            await status_msg.edit_text("❌ Download failed — file not found.")
            return

        # Upload to Telegram
        try:
            await status_msg.edit_text(
                f"📤 **Uploading to Telegram...**\n🎵 {track_title}",
                parse_mode="Markdown",
            )
        except Exception:
            pass

        file = FSInputFile(path=filepath)

        sent_msg = await bot.send_audio(
            chat_id=callback_query.message.chat.id,
            audio=file,
            title=track_title,
            performer=track_source,
            caption=f"🎵 *{track_title}*\n📂 {track_source}",
            parse_mode="Markdown",
            thumbnail=None,  # Telegram will use embedded MP3 art if available
        )

        # Save Telegram file_id to persistent cache for instant delivery next time
        if sent_msg and sent_msg.audio:
            audio_file_id_cache[video_id] = sent_msg.audio.file_id
            save_audio_cache()

        # Clean up status message
        try:
            await status_msg.delete()
        except Exception:
            pass

    except Exception as e:
        try:
            await status_msg.edit_text(f"❌ Download failed:\n{str(e)[:500]}")
        except Exception:
            pass
    finally:
        if job_dir.exists():
            shutil.rmtree(job_dir, ignore_errors=True)
        if sticker_msg:
            try:
                await sticker_msg.delete()
            except Exception:
                pass


# ─── Main ─────────────────────────────────────────────────────────────────────

async def main():
    print("[Music] Starting Hunterstar Music Bot...")
    print(f"[API] {API_BASE}")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
