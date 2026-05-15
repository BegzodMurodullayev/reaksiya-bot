import asyncio
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import sys
import os

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from pyrogram import Client
from pyrogram.errors import BadRequest, FloodWait, RPCError

try:
    from pyrogram.errors import UsernameOccupied
except ImportError:
    UsernameOccupied = None

from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(SCRIPT_DIR, "..", ".env")
load_dotenv(env_path)

API_ID     = int(os.getenv("API_ID", "0"))
API_HASH   = os.getenv("API_HASH", "")

if not API_ID or not API_HASH:
    print("❌ Xato: .env faylidan API_ID yoki API_HASH topilmadi!")
    sys.exit(1)

ACCOUNTS = [
    {"label": "Akkaunt-10", "session_file": os.path.join(SCRIPT_DIR, "..", "sessionbase", "session_string_10.txt"), "session_string": ""},
    {"label": "Akkaunt-11", "session_file": os.path.join(SCRIPT_DIR, "..", "sessionbase", "session_string_11.txt"), "session_string": ""},
    {"label": "Akkaunt-12", "session_file": os.path.join(SCRIPT_DIR, "..", "sessionbase", "session_string_12.txt"), "session_string": ""},
    {"label": "Akkaunt-13", "session_file": os.path.join(SCRIPT_DIR, "..", "sessionbase", "session_string_13.txt"), "session_string": ""},
    {"label": "Akkaunt-14", "session_file": os.path.join(SCRIPT_DIR, "..", "sessionbase", "session_string_14.txt"), "session_string": ""},
]

# ── QAYTA TIKLANADIGAN BOTLAR RO'YXATI ─────────────────────────────
TARGET_BOTS = [3, 4, 5, 6, 7, 27, 28, 30, 31, 33, 34, 36, 37]
# ──────────────────────────────────────────────────────────────────

STEP_DELAY      = 12
DELAY_AFTER_BOT = 15
ERROR_DELAY     = 12
MAX_FLOOD_WAIT_SECONDS = 10_000

BOTFATHER_USERNAME    = "BotFather"
NEW_TOKENS_FILE       = os.path.join(SCRIPT_DIR, "..", "new_recovered_tokens.txt")
MAX_USERNAME_ATTEMPTS = 3

TOKEN_REGEX = re.compile(
    r"Use this token to access the HTTP API:\s*([0-9]+:[A-Za-z0-9_-]+)"
)
RETRY_AFTER_REGEX = re.compile(
    r"too many attempts.*?try again in\s+(\d+)\s+seconds",
    re.IGNORECASE | re.DOTALL,
)


def load_saved_session(session_file: str) -> str:
    path = Path(session_file)
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def save_session(session_file: str, session_string: str) -> None:
    Path(session_file).write_text(session_string.strip(), encoding="utf-8")


def format_duration(total_seconds: float) -> str:
    total_seconds = int(total_seconds)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes   = divmod(minutes, 60)
    if hours:
        return f"{hours} soat {minutes} daqiqa {seconds} soniya"
    if minutes:
        return f"{minutes} daqiqa {seconds} soniya"
    return f"{seconds} soniya"


def message_text(message) -> str:
    return (message.text or message.caption or "").strip()


def parse_token(text: str) -> Optional[str]:
    m = TOKEN_REGEX.search(text)
    return m.group(1) if m else None


def parse_retry_after_seconds(text: str) -> Optional[int]:
    m = RETRY_AFTER_REGEX.search(text)
    return int(m.group(1)) if m else None


def is_username_taken_reply(text: str) -> bool:
    lowered = text.lower()
    return (
        "this username is already taken" in lowered
        or "sorry, this username is already taken" in lowered
        or "username is invalid" in lowered
        or "sorry, this username is invalid" in lowered
    )


def is_name_prompt(text: str) -> bool:
    return "please choose a name for your bot" in text.lower()


def is_username_prompt(text: str) -> bool:
    return "now let's choose a username for your bot" in text.lower()


class AccountState:
    def __init__(self, label: str):
        self.label       = label
        self.client      : Optional[Client] = None
        self.busy        = False
        self.flood_until : float = 0.0

    @property
    def is_available(self) -> bool:
        return not self.busy and time.monotonic() >= self.flood_until

    @property
    def flood_remaining(self) -> float:
        return max(0.0, self.flood_until - time.monotonic())

    def set_flood(self, seconds: int) -> None:
        self.flood_until = time.monotonic() + seconds
        finish = datetime.fromtimestamp(
            datetime.now().timestamp() + seconds
        ).strftime("%H:%M:%S")
        print(f"🚫 [{self.label}] FloodWait {seconds}s → {finish} gacha band.")


async def fetch_latest_botfather_reply(
    app: Client,
    state: AccountState,
    after_message_id: int,
    timeout: int = 60,
):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            replies = []
            async for msg in app.get_chat_history(BOTFATHER_USERNAME, limit=10):
                if msg.id <= after_message_id:
                    break
                if msg.outgoing:
                    continue
                replies.append(msg)
            if replies:
                return max(replies, key=lambda m: m.id)
        except FloodWait as err:
            if err.value >= MAX_FLOOD_WAIT_SECONDS:
                raise
            state.set_flood(err.value)
            raise
        await asyncio.sleep(1.2)
    raise TimeoutError("BotFather javobi kelmadi.")


async def send_message_safe(app: Client, state: AccountState, chat_id: str, text: str):
    while True:
        try:
            return await app.send_message(chat_id, text)
        except FloodWait as err:
            if err.value >= MAX_FLOOD_WAIT_SECONDS:
                raise
            state.set_flood(err.value)
            raise


async def create_one_bot(state: AccountState, bot_number: int) -> dict:
    app = state.client
    username_attempts = 0

    while username_attempts < MAX_USERNAME_ATTEMPTS:
        bot_name = f"Reaksiya {bot_number}"
        username = f"begzod_reaksiya_{bot_number}_bot"
        print(f"  🔄 [{state.label}] Qayta yaratilmoqda: {username}")

        try:
            # ── /newbot ──────────────────────────────
            sent = await send_message_safe(app, state, BOTFATHER_USERNAME, "/newbot")
            reply = await fetch_latest_botfather_reply(app, state, sent.id)
            reply_text = message_text(reply)

            ra = parse_retry_after_seconds(reply_text)
            if ra:
                if ra >= MAX_FLOOD_WAIT_SECONDS:
                    return {"success": False, "flood": True, "flood_seconds": ra, "fatal": True}
                state.set_flood(ra)
                return {"success": False, "flood": True, "flood_seconds": ra}

            if not is_name_prompt(reply_text):
                raise BadRequest(f"/newbot javobi kutilmagan: {reply_text[:80]}")

            await asyncio.sleep(STEP_DELAY)

            # ── Bot nomi ─────────────────────────────
            sent = await send_message_safe(app, state, BOTFATHER_USERNAME, bot_name)
            reply = await fetch_latest_botfather_reply(app, state, sent.id)
            reply_text = message_text(reply)

            ra = parse_retry_after_seconds(reply_text)
            if ra:
                if ra >= MAX_FLOOD_WAIT_SECONDS:
                    return {"success": False, "flood": True, "flood_seconds": ra, "fatal": True}
                state.set_flood(ra)
                return {"success": False, "flood": True, "flood_seconds": ra}

            if not is_username_prompt(reply_text):
                raise BadRequest(f"Nomdan keyin javob kutilmagan: {reply_text[:80]}")

            await asyncio.sleep(STEP_DELAY)

            # ── Username ─────────────────────────────
            sent = await send_message_safe(app, state, BOTFATHER_USERNAME, username)
            reply = await fetch_latest_botfather_reply(app, state, sent.id, timeout=90)
            reply_text = message_text(reply)

            ra = parse_retry_after_seconds(reply_text)
            if ra:
                if ra >= MAX_FLOOD_WAIT_SECONDS:
                    return {"success": False, "flood": True, "flood_seconds": ra, "fatal": True}
                state.set_flood(ra)
                return {"success": False, "flood": True, "flood_seconds": ra}

            token = parse_token(reply_text)
            if token:
                return {
                    "success":  True,
                    "number":   bot_number,
                    "username": username,
                    "token":    token,
                }

            if is_username_taken_reply(reply_text):
                print(f"  ❌ [{state.label}] Username HALI HAM BAND yoki xato: {username} ({reply_text[:50]})")
                return {"success": False, "flood": False, "error": "Username taken"}

            raise BadRequest(f"Token topilmadi. Javob: {reply_text[:80]}")

        except FloodWait as err:
            if err.value >= MAX_FLOOD_WAIT_SECONDS:
                state.set_flood(err.value)
                return {"success": False, "flood": True, "flood_seconds": err.value, "fatal": True}
            state.set_flood(err.value)
            return {"success": False, "flood": True, "flood_seconds": err.value}

        except BadRequest as err:
            print(f"  ❌ [{state.label}] BadRequest: {err}")
            return {"success": False, "flood": False, "error": str(err)}

        except TimeoutError as err:
            print(f"  ❌ [{state.label}] Timeout: {err}")
            return {"success": False, "flood": False, "error": str(err)}

        except Exception as err:
            print(f"  ❌ [{state.label}] {type(err).__name__}: {err}")
            return {"success": False, "flood": False, "error": str(err)}

    return {"success": False, "flood": False, "error": "Max retry tugadi."}


async def pick_roundrobin(
    states: list[AccountState],
    rr_index: int,
) -> tuple[AccountState, int]:
    n = len(states)

    for i in range(n):
        idx = (rr_index + i) % n
        if states[idx].is_available:
            next_idx = (idx + 1) % n
            return states[idx], next_idx

    soonest = min(states, key=lambda s: s.flood_remaining)
    wait_sec = soonest.flood_remaining
    idx = states.index(soonest)

    print(
        f"⏳ Barcha {n} ta akkaunt kutishda. "
        f"[{soonest.label}] {wait_sec:.0f}s da bo'shaydi..."
    )
    while soonest.flood_remaining > 0:
        await asyncio.sleep(min(soonest.flood_remaining + 0.5, 30))

    next_idx = (idx + 1) % n
    return states[idx], next_idx

def append_token_to_new_file(username: str, token: str):
    path = Path(NEW_TOKENS_FILE)
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{username} = {token}\n")

async def main() -> None:
    print(f"🎯 Reja: O'chgan botlarni tiklash ({len(TARGET_BOTS)} ta bot: {TARGET_BOTS})")
    print()

    states: list[AccountState] = []
    for cfg in ACCOUNTS:
        st = AccountState(cfg["label"])
        eff_session = cfg["session_string"].strip() or load_saved_session(cfg["session_file"])

        client = Client(
            cfg["label"].replace(" ", "_").lower() + "_rec",
            api_id=API_ID,
            api_hash=API_HASH,
            session_string=eff_session or None,
            in_memory=True,
        )
        await client.start()
        me = await client.get_me()
        print(f"👤 [{cfg['label']}] Login: @{me.username or me.first_name}")

        exported = await client.export_session_string()
        save_session(cfg["session_file"], exported)
        print(f"💾 [{cfg['label']}] Session saqlandi: {cfg['session_file']}")

        st.client = client
        states.append(st)

    n_accounts = len(states)
    print(f"\n✅ {n_accounts} ta akkaunt ulandi. Qayta tiklash boshlanmoqda...\n")

    started_at     = datetime.now()
    success_count  = 0
    error_count    = 0
    fatal_accounts : set[str] = set()
    rr_index       = 0
    
    target_index = 0

    try:
        while target_index < len(TARGET_BOTS):
            current_number = TARGET_BOTS[target_index]
            label = f"{target_index + 1}/{len(TARGET_BOTS)}"

            live_states = [s for s in states if s.label not in fatal_accounts]
            if not live_states:
                print("⛔ Barcha akkauntlar fatal flood waitga tushdi. To'xtatilmoqda.")
                break

            live_rr = rr_index % len(live_states)
            state, new_live_rr = await pick_roundrobin(live_states, live_rr)
            rr_index = new_live_rr

            state.busy = True
            print(f"\n[{label}] → [{state.label}] Bot #{current_number} ni tiklash boshlandi")
            result = await create_one_bot(state, current_number)
            state.busy = False

            if result.get("success"):
                success_count += 1
                append_token_to_new_file(result['username'], result['token'])
                print(f"✅ [{label}] Muvaffaqiyatli: {result['username']} → {result['token']}")
                target_index += 1
                if target_index < len(TARGET_BOTS):
                    await asyncio.sleep(DELAY_AFTER_BOT)

            elif result.get("flood"):
                if result.get("fatal"):
                    fatal_accounts.add(state.label)
                    print(f"⛔ [{state.label}] {result['flood_seconds']}s > limit → chiqarildi.")
                continue

            else:
                error_count += 1
                print(f"⚠️ [{label}] Xatolik tufayli bu botni tashlab o'tamiz: {result.get('error','?')}")
                target_index += 1
                await asyncio.sleep(ERROR_DELAY)

    except KeyboardInterrupt:
        print("\n🛑 Foydalanuvchi to'xtatdi.")
    except RPCError as err:
        print(f"❌ RPC xatosi: {type(err).__name__}: {err}")
    finally:
        for st in states:
            try:
                if st.client and st.client.is_connected:
                    await st.client.stop()
            except Exception:
                pass

    finished_at = datetime.now()
    duration = finished_at - started_at
    print()
    print(f"🎉 Tugadi!  ✅ {success_count} ta tiklandi  ⚠️ {error_count} ta xato")
    print(
        f"🕒 {started_at.strftime('%H:%M:%S')} → {finished_at.strftime('%H:%M:%S')} "
        f"| Jami: {format_duration(duration.total_seconds())}"
    )


if __name__ == "__main__":
    asyncio.run(main())
