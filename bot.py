import json
import logging
import os
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from steam_api import filter_by_genre, get_new_releases, get_trending

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
NOTIFY_HOUR = int(os.getenv("NOTIFY_HOUR", "9"))
STATE_FILE = Path(__file__).parent / "state.json"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"seen_ids": [], "chat_ids": [], "filters": {}}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def get_filter(state: dict, chat_id: int) -> str | None:
    return state.get("filters", {}).get(str(chat_id))


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------

def format_releases(games: list[dict], genre_filter: str | None = None) -> str:
    if not games:
        if genre_filter:
            return f'No new "{genre_filter}" releases found right now.'
        return "No new releases found right now."

    header = f"🎮 New Steam Releases"
    if genre_filter:
        header += f" — {genre_filter}"
    header += "!\n"

    lines = [header]
    for game in games:
        discount = game["discount_percent"]
        discount_str = f" (-{discount}%)" if discount else ""
        lines.append(f"• {game['name']} — {game['price_usd']}{discount_str}")
        lines.append(f"  🔗 {game['url']}\n")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    state = load_state()

    if chat_id not in state["chat_ids"]:
        state["chat_ids"].append(chat_id)
        save_state(state)
        msg = (
            "You are now subscribed to daily Steam new-release notifications!\n"
            f"Updates will arrive every day at {NOTIFY_HOUR:02d}:00.\n\n"
            "Use /latest for an immediate list, or /help for all commands."
        )
    else:
        msg = "You are already subscribed. Use /latest to see current new releases."

    await update.message.reply_text(msg)


async def cmd_latest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = load_state()
    chat_id = update.effective_chat.id
    genre = get_filter(state, chat_id)

    await update.message.reply_text(
        f"Fetching Steam new releases{f' ({genre})' if genre else ''}..."
    )
    try:
        games = await get_new_releases(with_genres=bool(genre))
        if genre:
            games = filter_by_genre(games, genre)
        await update.message.reply_text(format_releases(games[:10], genre))
    except Exception as exc:
        logger.error("Error fetching releases: %s", exc)
        await update.message.reply_text("Could not fetch Steam data right now. Try again later.")


async def cmd_trending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = load_state()
    chat_id = update.effective_chat.id
    genre = get_filter(state, chat_id)

    await update.message.reply_text(
        f"Fetching trending Steam games{f' ({genre})' if genre else ''}..."
    )
    try:
        games = await get_trending(with_genres=bool(genre))
        if genre:
            games = filter_by_genre(games, genre)
        header = "🔥 Trending on Steam"
        if genre:
            header += f" — {genre}"
        header += "!\n"
        lines = [header]
        for i, game in enumerate(games[:10], start=1):
            discount = game["discount_percent"]
            discount_str = f" (-{discount}%)" if discount else ""
            lines.append(f"{i}. {game['name']} — {game['price_usd']}{discount_str}")
            lines.append(f"   🔗 {game['url']}\n")
        await update.message.reply_text("\n".join(lines) if len(lines) > 1 else f'No trending "{genre}" games found right now.')
    except Exception as exc:
        logger.error("Error fetching trending: %s", exc)
        await update.message.reply_text("Could not fetch Steam data right now. Try again later.")


async def cmd_filter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    state = load_state()
    args = context.args

    # /filter with no args — show current filter
    if not args:
        current = get_filter(state, chat_id)
        if current:
            await update.message.reply_text(
                f'Your current genre filter is: "{current}"\n\n'
                "Use /filter clear to remove it, or /filter <genre> to change it.\n"
                "Example genres: Action, RPG, Strategy, Indie, Adventure, Simulation, Sports"
            )
        else:
            await update.message.reply_text(
                "You have no genre filter set — you receive all new releases.\n\n"
                "Set one with /filter <genre>\n"
                "Example: /filter Action\n"
                "Example genres: Action, RPG, Strategy, Indie, Adventure, Simulation, Sports"
            )
        return

    genre = " ".join(args)

    # /filter clear — remove filter
    if genre.lower() == "clear":
        state.setdefault("filters", {}).pop(str(chat_id), None)
        save_state(state)
        await update.message.reply_text("Genre filter cleared. You will now receive all new releases.")
        return

    # /filter <genre> — set filter
    state.setdefault("filters", {})[str(chat_id)] = genre
    save_state(state)
    await update.message.reply_text(
        f'Genre filter set to "{genre}".\n'
        "/latest and daily notifications will now only show matching games."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "Available commands:\n"
        "/start          — Subscribe to daily new-release notifications\n"
        "/latest         — Show current Steam new releases (top 10)\n"
        "/trending       — Show current Steam top sellers (top 10)\n"
        "/filter <genre> — Filter results by genre (e.g. Action, RPG, Indie)\n"
        "/filter clear   — Remove your genre filter\n"
        "/filter         — Show your current filter\n"
        "/help           — Show this help message"
    )
    await update.message.reply_text(text)


# ---------------------------------------------------------------------------
# Scheduled daily notification
# ---------------------------------------------------------------------------

async def daily_notify(app: Application) -> None:
    state = load_state()
    if not state["chat_ids"]:
        logger.info("No subscribers — skipping daily notify.")
        return

    # Determine if any subscriber has a genre filter
    filters = state.get("filters", {})
    any_filter = any(str(cid) in filters for cid in state["chat_ids"])

    try:
        games = await get_new_releases(with_genres=any_filter)
    except Exception as exc:
        logger.error("Scheduler: failed to fetch releases: %s", exc)
        return

    seen = set(state["seen_ids"])
    new_games = [g for g in games if g["id"] not in seen]

    if not new_games:
        logger.info("Scheduler: no new games to notify.")
        return

    for chat_id in state["chat_ids"]:
        genre = filters.get(str(chat_id))
        games_to_send = filter_by_genre(new_games, genre) if genre else new_games
        if not games_to_send:
            continue
        try:
            await app.bot.send_message(
                chat_id=chat_id,
                text=format_releases(games_to_send[:10], genre),
            )
        except Exception as exc:
            logger.warning("Could not send to %s: %s", chat_id, exc)

    state["seen_ids"] = list(seen | {g["id"] for g in new_games})
    save_state(state)
    logger.info(
        "Scheduler: notified %d chat(s) about %d new game(s).",
        len(state["chat_ids"]),
        len(new_games),
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")

    scheduler = AsyncIOScheduler()

    async def post_init(app: Application) -> None:
        scheduler.add_job(
            daily_notify,
            trigger="cron",
            hour=NOTIFY_HOUR,
            minute=0,
            kwargs={"app": app},
        )
        scheduler.start()
        logger.info("Scheduler started — daily notify at %02d:00.", NOTIFY_HOUR)

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("latest", cmd_latest))
    app.add_handler(CommandHandler("trending", cmd_trending))
    app.add_handler(CommandHandler("filter", cmd_filter))
    app.add_handler(CommandHandler("help", cmd_help))

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
