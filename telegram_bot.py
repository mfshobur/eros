import asyncio
import json
import threading
import time
from pathlib import Path

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from agent import Agent
from config import load_config
from tools.base import load_tools, set_permission_callback, set_permission_mode
from tools.bash import set_confirm_callback
import memory.rooms as rooms

_USERS_FILE = Path.home() / ".local" / "share" / "eros" / "telegram_users.json"
_MODES_FILE = Path.home() / ".local" / "share" / "eros" / "telegram_modes.json"

_agents: dict[int, Agent] = {}
_user_modes: dict[int, str] = {}  # "auto" or "manual" per user
_pending: dict[int, threading.Event] = {}  # awaiting approval
_pending_result: dict[int, bool] = {}


def _load_users() -> set[int]:
    if _USERS_FILE.exists():
        return set(json.loads(_USERS_FILE.read_text()))
    return set()


def _save_users(users: set[int]) -> None:
    _USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _USERS_FILE.write_text(json.dumps(list(users)))


def _load_modes() -> dict[int, str]:
    if _MODES_FILE.exists():
        return {int(k): v for k, v in json.loads(_MODES_FILE.read_text()).items()}
    return {}


def _save_modes() -> None:
    _MODES_FILE.parent.mkdir(parents=True, exist_ok=True)
    _MODES_FILE.write_text(json.dumps(_user_modes))


def _get_agent(user_id: int, config: dict) -> Agent:
    if user_id not in _agents:
        agent = Agent(config)
        room = f"tg_{user_id}"
        agent.history = rooms.load_messages(room, config.get("max_history_turns", 50))
        _agents[user_id] = agent
    return _agents[user_id]


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config = context.bot_data["config"]
    secret = config.get("telegram", {}).get("pair_secret", "")
    user_id = update.effective_user.id
    users = _load_users()

    if user_id in users:
        await update.message.reply_text("Already paired. Send a message to start chatting.")
        return

    args = context.args
    if not args or args[0] != secret:
        await update.message.reply_text("Send /start <secret> to pair.")
        return

    users.add(user_id)
    _save_users(users)
    rooms.init()
    await update.message.reply_text("Paired. Send a message to start chatting.")


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id not in _load_users():
        await update.message.reply_text("Send /start <secret> to pair first.")
        return
    config = context.bot_data["config"]
    agent = _get_agent(user_id, config)
    agent.clear_history()
    rooms.clear_room(f"tg_{user_id}")
    await update.message.reply_text("History cleared.")


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id not in _load_users():
        await update.message.reply_text("Send /start <secret> to pair first.")
        return
    config = context.bot_data["config"]
    agent = _get_agent(user_id, config)
    if not context.args:
        await update.message.reply_text(f"Current model: {agent.model}\nUsage: /model <name>")
        return
    agent.switch_model(context.args[0])
    await update.message.reply_text(f"Switched to {agent.model}")


async def cmd_permissions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id not in _load_users():
        await update.message.reply_text("Send /start <secret> to pair first.")
        return
    current = _user_modes.get(user_id, "manual")
    new_mode = "manual" if current == "auto" else "auto"
    _user_modes[user_id] = new_mode
    _save_modes()
    await update.message.reply_text(f"Permission mode: {new_mode}")


async def cmd_thinking(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id not in _load_users():
        await update.message.reply_text("Send /start <secret> to pair first.")
        return
    config = context.bot_data["config"]
    agent = _get_agent(user_id, config)
    agent.show_thinking = not agent.show_thinking
    state = "on" if agent.show_thinking else "off"
    await update.message.reply_text(f"Thinking display: {state}")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "/start <secret> — pair with the bot\n"
        "/clear — clear conversation history\n"
        "/model <name> — switch model\n"
        "/permissions — toggle auto/manual tool approval\n"
        "/thinking — toggle reasoning display\n"
        "/help — show this message"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id not in _load_users():
        await update.message.reply_text("Send /start <secret> to pair first.")
        return

    config = context.bot_data["config"]
    agent = _get_agent(user_id, config)
    user_input = update.message.text or ""
    room = f"tg_{user_id}"

    mode = _user_modes.get(user_id, "manual")
    set_permission_mode(mode)

    loop = asyncio.get_event_loop()

    # Lazy bubble: created on first token only if no tool has fired yet.
    # This ensures tool notification bubbles always appear above the response bubble.
    msg_holder = [None]
    bubble_creating = [False]  # prevents concurrent creation (asyncio is single-threaded)
    bubble_ready = asyncio.Event()  # set once msg_holder[0] is assigned
    tools_fired = [False]

    buffer: list[str] = []
    edit_version = [0]
    last_edit_at = [0.0]
    streaming = [True]
    _EDIT_INTERVAL = 0.5

    def on_token(text: str) -> None:
        if not streaming[0]:
            return
        buffer.append(text)
        edit_version[0] += 1
        my_version = edit_version[0]
        now = time.monotonic()

        async def _edit() -> None:
            if not streaming[0]:
                return
            if tools_fired[0]:
                return
            if edit_version[0] != my_version:
                return
            if msg_holder[0] is None:
                if bubble_creating[0]:
                    return
                bubble_creating[0] = True
                msg_holder[0] = await update.message.reply_text("...")
                bubble_ready.set()
            if edit_version[0] != my_version:
                return
            try:
                await msg_holder[0].edit_text("".join(buffer) + " ▌")
            except Exception:
                pass

        if now - last_edit_at[0] >= _EDIT_INTERVAL:
            last_edit_at[0] = now
            asyncio.run_coroutine_threadsafe(_edit(), loop)
        else:
            async def _catchup() -> None:
                await asyncio.sleep(_EDIT_INTERVAL)
                await _edit()
            asyncio.run_coroutine_threadsafe(_catchup(), loop)

    def on_tool_call(name: str, args: dict) -> None:
        tools_fired[0] = True
        edit_version[0] += 1

        hint = next((str(v) for v in args.values() if v), "")
        label = f"🔧 {name}: {hint}" if hint else f"🔧 {name}"

        async def _delete_and_notify() -> None:
            # Wait for any in-flight bubble creation before deleting
            if bubble_creating[0] and not bubble_ready.is_set():
                try:
                    await asyncio.wait_for(bubble_ready.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
            # Delete pre-tool streaming bubble so tool notification becomes first
            if msg_holder[0] is not None:
                try:
                    await msg_holder[0].delete()
                except Exception:
                    pass
                msg_holder[0] = None
            buffer.clear()
            await update.message.reply_text(label)
            # Reset streaming state so post-tool tokens create a fresh bubble
            edit_version[0] += 1
            tools_fired[0] = False
            bubble_creating[0] = False
            bubble_ready.clear()

        asyncio.run_coroutine_threadsafe(_delete_and_notify(), loop)

    _SILENT_TOOLS = {"web_search", "web_fetch", "list_dir", "read_file"}

    def on_tool_result(name: str, result: str) -> None:
        if name in _SILENT_TOOLS or result.strip() == "Cancelled by user.":
            return
        preview = result[:300] + ("..." if len(result) > 300 else "")
        asyncio.run_coroutine_threadsafe(update.message.reply_text(f"🔧 {name}: {preview}"), loop)

    def permission_callback(tool_name: str, args: dict, preview: str) -> bool:
        if _user_modes.get(user_id, "manual") != "manual":
            return True
        event = threading.Event()
        _pending[user_id] = event
        _pending_result[user_id] = False
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Allow", callback_data=f"approve:{user_id}"),
                InlineKeyboardButton("Always Allow", callback_data=f"always:{user_id}"),
                InlineKeyboardButton("Deny", callback_data=f"deny:{user_id}"),
            ]
        ])
        asyncio.run_coroutine_threadsafe(
            update.message.reply_text(
                f"🔧 Allow {tool_name}?\n{preview}",
                reply_markup=keyboard,
            ),
            loop,
        )
        event.wait(timeout=60)
        return _pending_result.get(user_id, False)

    set_permission_callback(permission_callback)
    set_confirm_callback(lambda prompt, default=False: permission_callback("bash", {}, prompt))

    try:
        response, _, _ = await asyncio.to_thread(
            agent.chat,
            user_input,
            on_token=on_token,
            on_tool_call=on_tool_call,
            on_tool_result=on_tool_result,
        )
    except Exception as e:
        if msg_holder[0] is not None:
            await msg_holder[0].edit_text(f"Error: {e}")
        else:
            await update.message.reply_text(f"Error: {e}")
        return

    streaming[0] = False
    edit_version[0] += 1
    # Wait for any in-flight bubble creation or deletion before final write
    if bubble_creating[0] and not bubble_ready.is_set():
        try:
            await asyncio.wait_for(bubble_ready.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            pass
    else:
        await asyncio.sleep(0.05)
    final = "".join(buffer).strip() or response or "(no response)"
    if msg_holder[0] is not None:
        # No tools fired — bubble was created during streaming, edit it in place
        try:
            await msg_holder[0].edit_text(final, parse_mode="Markdown")
        except Exception:
            await msg_holder[0].edit_text(final)
    else:
        # Tools fired — bubble was deleted; send new bubble (appears after tool notifications)
        try:
            await update.message.reply_text(final, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(final)
    rooms.save_turn(room, agent.model, user_input, final)



async def handle_approval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    data = query.data  # "approve:<user_id>" or "deny:<user_id>"
    action, uid_str = data.split(":")
    uid = int(uid_str)

    if action == "always":
        _user_modes[uid] = "auto"
        _save_modes()
        _pending_result[uid] = True
    else:
        _pending_result[uid] = action == "approve"
    if uid in _pending:
        _pending[uid].set()
        del _pending[uid]

    if action == "always":
        label = "✅ Always Allowed (switched to auto)"
    elif action == "approve":
        label = "✅ Allowed"
    else:
        label = "❌ Denied"
    try:
        await query.edit_message_text(f"{query.message.text}\n\n{label}")
    except Exception:
        pass


_BOT_COMMANDS = [
    BotCommand("start", "Pair with the bot"),
    BotCommand("clear", "Clear conversation history"),
    BotCommand("model", "Switch model (e.g. /model ollama/llama3.2)"),
    BotCommand("permissions", "Toggle auto/manual tool approval"),
    BotCommand("thinking", "Toggle reasoning display on/off"),
    BotCommand("help", "Show available commands"),
]


async def _post_init(app: Application) -> None:
    await app.bot.set_my_commands(_BOT_COMMANDS)


def _build_app(config: dict):
    token = config.get("telegram", {}).get("token", "")
    app = Application.builder().token(token).post_init(_post_init).build()
    app.bot_data["config"] = config
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("permissions", cmd_permissions))
    app.add_handler(CommandHandler("thinking", cmd_thinking))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message, block=False))
    app.add_handler(CallbackQueryHandler(handle_approval))
    return app


def run_in_thread(config: dict) -> threading.Thread:
    """Start the Telegram bot in a background daemon thread."""
    _user_modes.update(_load_modes())

    def _run() -> None:
        try:
            app = _build_app(config)
            app.run_polling(stop_signals=None)
        except Exception as e:
            print(f"[telegram] bot error: {e}")

    t = threading.Thread(target=_run, daemon=True, name="eros-telegram")
    t.start()
    return t


def setup_interactive(config: dict) -> bool:
    """Prompt for token + pair_secret, save to config.yaml, start bot. Returns True if configured."""
    from config import save_telegram_config
    try:
        answer = input("Telegram bot is not configured. Set it up? [y/N]: ").strip().lower()
        if answer != "y":
            return False
        token = input("Bot token (from BotFather): ").strip()
        from rich.console import Console
        from rich.panel import Panel
        Console().print(Panel(
            "A pair secret is a password that controls who can use your bot.\n"
            "To start chatting, users must open your bot in Telegram and send:\n\n"
            "  [bold]/start <your-secret>[/bold]\n\n"
            "Only users who know the secret will be allowed in.\n"
            "Keep it private and only share it with people you trust.",
            title="What is a pair secret?",
            border_style="dim",
        ))
        secret = input("Pair secret: ").strip()
        if not token:
            print("No token entered, skipping.")
            return False
        save_telegram_config(token, secret)
        config.setdefault("telegram", {})["token"] = token
        config["telegram"]["pair_secret"] = secret
        print("Saved. Telegram bot starting in background...")
        run_in_thread(config)
        return True
    except (KeyboardInterrupt, EOFError):
        return False


def main() -> None:
    config = load_config()
    token = config.get("telegram", {}).get("token", "")
    if not token:
        raise SystemExit("telegram.token not set in config.yaml")
    rooms.init()
    load_tools(config.get("tools_enabled", []))
    print(f"eros Telegram bot starting (model: {config['model']})")
    _user_modes.update(_load_modes())
    _build_app(config).run_polling()


if __name__ == "__main__":
    main()
