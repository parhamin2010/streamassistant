import json
import logging
import os
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from steam_api import get_new_releases

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
    return {"seen_ids": [], "chat_ids": []}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------

def format_releases(games: list[dict]) -> str:
    if not games:
        return "No new releases found right now."

    lines = ["🎮 New Steam Releases!\n"]
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
    await update.message.reply_text("Fetching Steam new releases...")
    try:
        games = await get_new_releases()
        await update.message.reply_text(format_releases(games[:10]))
    except Exception as exc:
        logger.error("Error fetching releases: %s", exc)
        await update.message.reply_text("Could not fetch Steam data right now. Try again later.")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "Available commands:\n"
        "/start   — Subscribe to daily new-release notifications\n"
        "/latest  — Show current Steam new releases (top 10)\n"
        "/help    — Show this help message"
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

    try:
        games = await get_new_releases()
    except Exception as exc:
        logger.error("Scheduler: failed to fetch releases: %s", exc)
        return

    seen = set(state["seen_ids"])
    new_games = [g for g in games if g["id"] not in seen]

    if not new_games:
        logger.info("Scheduler: no new games to notify.")
        return

    msg = format_releases(new_games[:10])
    for chat_id in state["chat_ids"]:
        try:
            await app.bot.send_message(chat_id=chat_id, text=msg)
        except Exception as exc:
            logger.warning("Could not send to %s: %s", chat_id, exc)

    state["seen_ids"] = list(seen | {g["id"] for g in new_games})
    save_state(state)
    logger.info("Scheduler: notified %d chat(s) about %d new game(s).", len(state["chat_ids"]), len(new_games))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set. Copy .env.example to .env and fill it in.")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("latest", cmd_latest))
    app.add_handler(CommandHandler("help", cmd_help))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        daily_notify,
        trigger="cron",
        hour=NOTIFY_HOUR,
        minute=0,
        kwargs={"app": app},
    )
    scheduler.start()
    logger.info("Scheduler started — daily notify at %02d:00.", NOTIFY_HOUR)

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
