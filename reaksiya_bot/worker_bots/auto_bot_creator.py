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

from pyrogram import Client
from pyrogram.errors import BadRequest, FloodWait, RPCError

try:
    from pyrogram.errors import UsernameOccupied
except ImportError:
    UsernameOccupied = None

# ════════════════════════════════════════════════
#  SOZLAMALAR
# ════════════════════════════════════════════════

API_ID   = 32003716
API_HASH = "fa740b9dfde98b4dc6e541d66f665815"

# ── Akkauntlar ro'yxati ───────────────────────────────────────────────────────
# Xohlagancha akkaunt qo'shish mumkin! Har biri uchun alohida session fayli.
# Misol: 5 ta akkaunt uchun session_string_1.txt ... session_string_5.txt
#
# Qo'shish uchun shunchaki quyidagi bloklarni ko'paytiring:
#   {"label": "Akkaunt-N", "session_file": "session_string_N.txt", "session_string": ""},
# ─────────────────────────────────────────────────────────────────────────────
ACCOUNTS = [
    {"label": "Akkaunt-1", "session_file": "session_string.txt",   "session_string": ""},
    {"label": "Akkaunt-2", "session_file": "session_string_2.txt", "session_string": ""},
    {"label": "Akkaunt-3", "session_file": "session_string_3.txt", "session_string": ""},
    # {"label": "Akkaunt-4", "session_file": "session_string_4.txt", "session_string": ""},
    # {"label": "Akkaunt-5", "session_file": "session_string_5.txt", "session_string": ""},
]

END_NUMBER  = 40    # Oxirgi bot raqami

# ── Kutish vaqtlari (soniya) ──────────────────
STEP_DELAY      = 12   # /newbot → name → username orasidagi pauza
DELAY_AFTER_BOT = 15   # Muvaffaqiyatli yaratilgan botdan keyingi pauza
ERROR_DELAY     = 12   # Xato yuz bergandan keyingi pauza

# FloodWait 10 000 s (≈2.8 soat) dan oshsa dastur to'xtatiladi
MAX_FLOOD_WAIT_SECONDS = 10_000

BOTFATHER_USERNAME    = "BotFather"
TOKENS_FILE           = "created_tokens.txt"
MAX_USERNAME_ATTEMPTS = 3

# ════════════════════════════════════════════════
#  REGEX
# ════════════════════════════════════════════════

TOKEN_REGEX = re.compile(
    r"Use this token to access the HTTP API:\s*([0-9]+:[A-Za-z0-9_-]+)"
)
RETRY_AFTER_REGEX = re.compile(
    r"too many attempts.*?try again in\s+(\d+)\s+seconds",
    re.IGNORECASE | re.DOTALL,
)
TOKENS_NUMBER_REGEX = re.compile(r"begzod_reaksiya_(\d+)_bot")


# ════════════════════════════════════════════════
#  YORDAMCHI FUNKSIYALAR
# ════════════════════════════════════════════════

def read_last_number_from_tokens() -> Optional[int]:
    path = Path(TOKENS_FILE)
    if not path.exists():
        return None
    numbers = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = TOKENS_NUMBER_REGEX.search(line)
        if m:
            numbers.append(int(m.group(1)))
    return max(numbers) if numbers else None


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


# ════════════════════════════════════════════════
#  ACCOUNT HOLATI
# ════════════════════════════════════════════════

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


# ════════════════════════════════════════════════
#  BOTFATHER BILAN MULOQOT
# ════════════════════════════════════════════════

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


# ════════════════════════════════════════════════
#  BOT YARATISH (bitta akkaunt bilan)
# ════════════════════════════════════════════════

async def create_one_bot(state: AccountState, bot_number: int) -> dict:
    app = state.client
    candidate_number  = bot_number
    username_attempts = 0

    while username_attempts < MAX_USERNAME_ATTEMPTS:
        bot_name = f"Reaksiya {candidate_number}"
        username = f"begzod_reaksiya_{candidate_number}_bot"
        print(f"  🔄 [{state.label}] Yaratilmoqda: {username}")

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
                    "number":   candidate_number,
                    "username": username,
                    "token":    token,
                }

            if is_username_taken_reply(reply_text):
                username_attempts += 1
                print(
                    f"  ⚠️ [{state.label}] Username band: {username} "
                    f"({username_attempts}/{MAX_USERNAME_ATTEMPTS})"
                )
                candidate_number += 1
                await asyncio.sleep(STEP_DELAY)
                continue

            raise BadRequest(f"Token topilmadi. Javob: {reply_text[:80]}")

        except FloodWait as err:
            if err.value >= MAX_FLOOD_WAIT_SECONDS:
                state.set_flood(err.value)
                return {"success": False, "flood": True, "flood_seconds": err.value, "fatal": True}
            state.set_flood(err.value)
            return {"success": False, "flood": True, "flood_seconds": err.value}

        except BadRequest as err:
            print(f"  ❌ [{state.label}] BadRequest: {err}")
            return {"success": False, "flood": False, "next_number": candidate_number + 1, "error": str(err)}

        except TimeoutError as err:
            print(f"  ❌ [{state.label}] Timeout: {err}")
            return {"success": False, "flood": False, "next_number": candidate_number + 1, "error": str(err)}

        except Exception as err:
            if UsernameOccupied and isinstance(err, UsernameOccupied):
                username_attempts += 1
                candidate_number += 1
                await asyncio.sleep(STEP_DELAY)
                continue
            print(f"  ❌ [{state.label}] {type(err).__name__}: {err}")
            return {"success": False, "flood": False, "next_number": candidate_number + 1, "error": str(err)}

    return {"success": False, "flood": False, "next_number": candidate_number, "error": "Max retry tugadi."}


# ════════════════════════════════════════════════
#  ROUND-ROBIN SCHEDULER
#  1→2→3→1→2→3  (flood wait bo'lsa keyingisiga o'tadi)
# ════════════════════════════════════════════════

async def pick_roundrobin(
    states: list[AccountState],
    rr_index: int,
) -> tuple[AccountState, int]:
    """
    Round-robin tartibda keyingi akkauntni qaytaradi.

    Mantiq:
      - rr_index'dan boshlab barcha akkauntlarni aylanib chiqadi
      - Bo'sh (flood yo'q) birinchi akkauntni tanlaydi
      - Barchasi flood wait'da bo'lsa → eng tez ochiladigan
        akkaunt kutiladi va u qaytariladi
    Qaytaradi: (tanlangan AccountState, keyingi rr_index)
    """
    n = len(states)

    # Bir turda bo'sh akkaunt qidirish
    for i in range(n):
        idx = (rr_index + i) % n
        if states[idx].is_available:
            next_idx = (idx + 1) % n
            return states[idx], next_idx

    # Barchasi band → eng tez bo'shadigan akkauntni kutish
    soonest = min(states, key=lambda s: s.flood_remaining)
    wait_sec = soonest.flood_remaining
    idx = states.index(soonest)

    print(
        f"⏳ Barcha {n} ta akkaunt kutishda. "
        f"[{soonest.label}] {wait_sec:.0f}s da bo'shaydi..."
    )
    # Har 30 soniyada xabar chiqaramiz, toki interfeys muzlab qolmasin
    while soonest.flood_remaining > 0:
        await asyncio.sleep(min(soonest.flood_remaining + 0.5, 30))

    next_idx = (idx + 1) % n
    return states[idx], next_idx


# ════════════════════════════════════════════════
#  ASOSIY
# ════════════════════════════════════════════════

async def main() -> None:
    # ── Boshlang'ich raqamni aniqlash ────────────
    last_in_file = read_last_number_from_tokens()
    if last_in_file is not None:
        start_number = last_in_file + 1
        print(f"📄 {TOKENS_FILE} da oxirgi bot: #{last_in_file}  →  #{start_number} dan boshlanadi")
    else:
        start_number = 1
        print(f"📄 {TOKENS_FILE} topilmadi yoki bo'sh  →  #1 dan boshlanadi")

    if start_number > END_NUMBER:
        print(f"✅ Barcha botlar ({END_NUMBER} ta) allaqachon yaratilgan. Chiqilmoqda.")
        return

    total_targets = END_NUMBER - start_number + 1
    print(f"🎯 Reja: #{start_number} → #{END_NUMBER}  ({total_targets} ta bot)")
    print(f"🔄 Round-robin rejimi: {len(ACCOUNTS)} ta akkaunt navbatma-navbat ishlaydi")
    print()

    # ── Akkauntlarni ishga tushirish ─────────────
    states: list[AccountState] = []
    for cfg in ACCOUNTS:
        st = AccountState(cfg["label"])
        eff_session = cfg["session_string"].strip() or load_saved_session(cfg["session_file"])

        client = Client(
            cfg["label"].replace(" ", "_").lower(),
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
    print(f"\n✅ {n_accounts} ta akkaunt ulandi. Round-robin boshlanmoqda...\n")

    started_at     = datetime.now()
    current_number = start_number
    success_count  = 0
    error_count    = 0
    fatal_accounts : set[str] = set()
    rr_index       = 0          # ← round-robin ko'rsatkichi

    try:
        with open(TOKENS_FILE, "a", encoding="utf-8") as token_file:
            progress = 0
            while current_number <= END_NUMBER:
                progress += 1
                label = f"{progress}/{total_targets}"

                # Faqat hayot akkauntlar
                live_states = [s for s in states if s.label not in fatal_accounts]
                if not live_states:
                    print("⛔ Barcha akkauntlar fatal flood waitga tushdi. To'xtatilmoqda.")
                    break

                # ── ROUND-ROBIN: navbatdagi akkauntni ol ──────────────────
                # rr_index live_states ichidagi tartibga moslashtiriladi
                live_rr = rr_index % len(live_states)
                state, new_live_rr = await pick_roundrobin(live_states, live_rr)
                # Keyingi iteratsiya uchun indeksni yangilaymiz
                rr_index = new_live_rr
                # ─────────────────────────────────────────────────────────

                state.busy = True
                print(f"\n[{label}] → [{state.label}] #{current_number}")
                result = await create_one_bot(state, current_number)
                state.busy = False

                if result.get("success"):
                    success_count += 1
                    token_line = f"{result['username']} = {result['token']}\n"
                    token_file.write(token_line)
                    token_file.flush()
                    print(f"✅ [{label}] {result['username']} → {result['token']}")
                    current_number = result["number"] + 1
                    if current_number <= END_NUMBER:
                        await asyncio.sleep(DELAY_AFTER_BOT)

                elif result.get("flood"):
                    if result.get("fatal"):
                        fatal_accounts.add(state.label)
                        print(f"⛔ [{state.label}] {result['flood_seconds']}s > limit → chiqarildi.")
                    # current_number o'zgarmaydi — qayta urinish
                    # Lekin rr_index allaqachon yangilangan → keyingi akkauntdan urinadi
                    continue

                else:
                    error_count += 1
                    current_number = result.get("next_number", current_number + 1)
                    print(f"⚠️ [{label}] O'tkazib yuborildi: {result.get('error','?')}")
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
    print(f"🎉 Tugadi!  ✅ {success_count} ta  ⚠️ {error_count} ta xato")
    print(
        f"🕒 {started_at.strftime('%H:%M:%S')} → {finished_at.strftime('%H:%M:%S')} "
        f"| Jami: {format_duration(duration.total_seconds())}"
    )


if __name__ == "__main__":
    asyncio.run(main())