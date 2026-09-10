"""
Real-time Telegram Bot Service & Polling Manager
Handles Telegram Long Polling, Webhook processing, Gemini AI responses,
Knowledge Base integration, Typing indicator, and Inline Keyboards.
"""
import asyncio
import logging
import re
import httpx
import aiosqlite
from typing import Optional, Dict

from app.core.config import settings
from app.core.database import DB_PATH

logger = logging.getLogger("telegram_service")

# ─── Continuation Keywords ────────────────────────────────────────────────────
# When user sends one of these short messages, we inject conversational context
CONTINUATION_KEYWORDS = {
    # Arabic
    "نعم", "آه", "أه", "اه", "ايه", "أيه", "إيه",
    "اكمل", "أكمل", "تابع", "استمر", "كمّل", "كمل",
    "اشرح", "اشرح أكثر", "اشرح اكثر", "وضح", "وضح أكثر", "وضح اكثر",
    "ماذا تقصد", "ماذا تقصد؟", "كيف؟", "لماذا؟", "لماذا",
    "ثم", "ثم ماذا", "وماذا بعد", "وبعدين", "وبعدها",
    "صح", "صحيح", "فاهم", "فهمت", "مفهوم", "وضح لي",
    "عطني مثال", "أعطني مثال", "مثال", "أكثر", "اكثر",
    "تفصيل", "بالتفصيل", "شرح أكثر", "شرح اكثر",
    # English
    "yes", "yeah", "yep", "ok", "okay", "sure", "go on",
    "continue", "next", "more", "explain", "elaborate",
    "tell me more", "and then", "what else", "keep going",
    "i see", "got it", "understood", "example", "show me",
}


def is_continuation(text: str) -> bool:
    """Detect if a message is a short continuation/follow-up rather than a new question."""
    clean = text.strip().lower().rstrip("؟?!. ")
    # Direct keyword match
    if clean in CONTINUATION_KEYWORDS:
        return True
    # Very short messages (≤ 12 chars) that don't look like a real question
    if len(clean) <= 12 and "?" not in clean and "؟" not in clean:
        # Only if it starts with a known keyword
        for kw in CONTINUATION_KEYWORDS:
            if clean.startswith(kw):
                return True
    return False


# ─── Message Splitting ────────────────────────────────────────────────────────

def split_long_message(text: str, max_len: int = 4000) -> list[str]:
    """
    Split a long message into chunks ≤ max_len characters.
    Tries to split at paragraph breaks, then sentences, then raw char limit.
    """
    if len(text) <= max_len:
        return [text]

    chunks = []
    while len(text) > max_len:
        # Try splitting at last double newline before limit
        split_pos = text.rfind("\n\n", 0, max_len)
        if split_pos == -1:
            # Try single newline
            split_pos = text.rfind("\n", 0, max_len)
        if split_pos == -1:
            # Try last sentence ending
            for punct in (".", "!", "?", "؟", "،", "،\n"):
                pos = text.rfind(punct, 0, max_len)
                if pos != -1:
                    split_pos = pos + 1
                    break
        if split_pos <= 0:
            split_pos = max_len

        chunks.append(text[:split_pos].strip())
        text = text[split_pos:].strip()

    if text:
        chunks.append(text)

    return chunks


# ─── Cleanup Old Messages ─────────────────────────────────────────────────────

async def cleanup_old_messages(db: aiosqlite.Connection, conv_id: int, max_msgs: int = 30):
    """Delete oldest messages when a conversation exceeds max_msgs messages."""
    await db.execute("""
        DELETE FROM messages
        WHERE conversation_id = ?
          AND id NOT IN (
              SELECT id FROM messages
              WHERE conversation_id = ?
              ORDER BY id DESC
              LIMIT ?
          )
    """, (conv_id, conv_id, max_msgs))
    await db.commit()


# ─── Bot Username Cache ───────────────────────────────────────────────────────

_bot_username_cache: Dict[int, str] = {}


async def get_bot_username(token: str, bot_id: int) -> str:
    """Fetch and cache bot's Telegram username via getMe API."""
    if bot_id in _bot_username_cache:
        return _bot_username_cache[bot_id]
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://api.telegram.org/bot{token}/getMe", timeout=5
            )
            if resp.status_code == 200:
                data = resp.json().get("result", {})
                username = data.get("username", "")
                if username:
                    _bot_username_cache[bot_id] = username
                    return username
    except Exception:
        pass
    return ""


# ─── Format for Telegram HTML ─────────────────────────────────────────────────

def format_for_telegram(text: str) -> str:
    """
    Convert Gemini markdown output to Telegram-compatible HTML format.
    Safely converts code blocks (<pre><code>), inline code, bold, italic,
    headings, lists, and dividers without crashing Telegram's parser.
    """
    if not text:
        return ""

    # 1. Extract and protect multi-line code blocks
    code_blocks = []
    def replace_code_block(match):
        lang = (match.group(1) or "").strip().lower()
        code = match.group(2)
        escaped_code = (
            code.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
        )
        idx = len(code_blocks)
        if lang:
            code_blocks.append(f'<pre><code class="language-{lang}">{escaped_code}</code></pre>')
        else:
            code_blocks.append(f'<pre>{escaped_code}</pre>')
        return f"\x00CODEBLOCK_{idx}\x00"

    text = re.sub(r'```([a-zA-Z0-9_+-]*)\n?(.*?)```', replace_code_block, text, flags=re.DOTALL)

    # 2. Extract and protect inline code `code`
    inline_codes = []
    def replace_inline_code(match):
        c = match.group(1)
        escaped = (
            c.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
        )
        idx = len(inline_codes)
        inline_codes.append(f'<code>{escaped}</code>')
        return f"\x00INLINECODE_{idx}\x00"

    text = re.sub(r'`([^`\n]+)`', replace_inline_code, text)

    # 3. HTML-escape normal text
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # 4. Headings → bold
    def replace_heading(match):
        title = match.group(2).strip()
        return f"\n\n<b>{title}</b>\n"

    text = re.sub(r'^(#{1,6})\s+(.+)$', replace_heading, text, flags=re.MULTILINE)

    # 5. Horizontal rules → elegant divider
    text = re.sub(r'^\s*[-*_]{3,}\s*$', '\n━━━━━━━━━━━━━━━━━━━━\n', text, flags=re.MULTILINE)

    # 6. Bold
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__(.+?)__', r'<b>\1</b>', text)

    # 7. Italic
    text = re.sub(r'(?<!\w)\*([^*\n]+)\*(?!\w)', r'<i>\1</i>', text)
    text = re.sub(r'(?<!\w)_([^_\n]+)_(?!\w)', r'<i>\1</i>', text)

    # 8. Bullet lists
    text = re.sub(r'^\s*[-*]\s+', '• ', text, flags=re.MULTILINE)

    # 9. Restore protected blocks
    for i, c in enumerate(inline_codes):
        text = text.replace(f"\x00INLINECODE_{i}\x00", c)
    for i, b in enumerate(code_blocks):
        text = text.replace(f"\x00CODEBLOCK_{i}\x00", b)

    # 10. Normalize blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ─── Telegram API Helpers ─────────────────────────────────────────────────────

async def send_chat_action(token: str, chat_id: str, action: str = "typing"):
    """Send a chat action (typing indicator) to Telegram."""
    url = f"https://api.telegram.org/bot{token}/sendChatAction"
    async with httpx.AsyncClient() as client:
        try:
            await client.post(url, json={"chat_id": chat_id, "action": action}, timeout=5)
        except Exception:
            pass


async def send_telegram_message(
    token: str,
    chat_id: str,
    text: str,
    reply_markup: dict = None,
    parse_mode: str = "HTML",
    reply_to_message_id: Optional[int] = None
):
    """
    Send text message(s) to Telegram chat.
    Automatically splits messages that exceed Telegram's 4096-char limit.
    """
    if not text or not text.strip():
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    parts = split_long_message(text, max_len=4000)

    async with httpx.AsyncClient() as client:
        for i, part in enumerate(parts):
            # Only attach reply_markup and reply_to to the LAST part
            is_last = (i == len(parts) - 1)

            payload = {
                "chat_id": chat_id,
                "text": part,
                "parse_mode": parse_mode,
            }
            if is_last and reply_markup:
                payload["reply_markup"] = reply_markup
            if i == 0 and reply_to_message_id:
                payload["reply_to_message_id"] = reply_to_message_id

            try:
                resp = await client.post(url, json=payload, timeout=15)
                if resp.status_code != 200:
                    # Fallback: strip HTML and retry as plain text
                    plain = re.sub(r'<[^>]+>', '', part)
                    fallback = {"chat_id": chat_id, "text": plain}
                    if is_last and reply_markup:
                        fallback["reply_markup"] = reply_markup
                    if i == 0 and reply_to_message_id:
                        fallback["reply_to_message_id"] = reply_to_message_id
                    await client.post(url, json=fallback, timeout=15)
            except Exception as e:
                logger.error(f"Failed to send Telegram message to {chat_id}: {e}")

            # Small delay between parts to avoid flood limits
            if not is_last:
                await asyncio.sleep(0.4)


# ─── Keyboards ────────────────────────────────────────────────────────────────

def build_main_keyboard(bot: dict) -> dict:
    """Build a persistent reply keyboard for the bot."""
    return {
        "keyboard": [
            [{"text": "💬 تواصل معنا"}, {"text": "🌐 عن الخدمة"}],
            [{"text": "✨ بدء جديد"}, {"text": "🆘 مساعدة"}],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False,
    }


def build_start_inline_keyboard(add_group_btn: bool = False, bot_username: str = "") -> dict:
    """Build inline keyboard for /start welcome message."""
    rows = [
        [
            {"text": "🚀 ابدأ المحادثة", "callback_data": "start_chat"},
            {"text": "🌐 عن الخدمة", "callback_data": "about"},
        ],
        [
            {"text": "💬 تواصل معنا", "callback_data": "contact"},
        ],
    ]
    # Add "Add to Group" button only for bot owner
    if add_group_btn and bot_username:
        rows.append([{
            "text": "➕ أضف البوت لمجموعتك",
            "url": f"https://t.me/{bot_username}?startgroup=true&admin=post_messages+delete_messages+restrict_members"
        }])
    return {"inline_keyboard": rows}


# ─── AI Response ──────────────────────────────────────────────────────────────

async def get_ai_response(
    bot: dict,
    user_message: str,
    chat_history: list,
    knowledge: list,
    db: aiosqlite.Connection,
    is_cont: bool = False
) -> str:
    """Generate AI response using Gemini REST API."""
    if not settings.GEMINI_API_KEY:
        return "أهلاً بك! شكراً لتواصلك معنا."

    # Use stable aliases that always point to the latest available models
    STABLE_MODELS = {"gemini-flash-latest", "gemini-flash-lite-latest", "gemini-pro-latest"}
    candidate_models = ["gemini-flash-latest", "gemini-flash-lite-latest", "gemini-pro-latest"]
    stored_model = bot.get("ai_model") or ""
    # If stored model is deprecated, fall back to stable alias
    selected_model = stored_model if stored_model in STABLE_MODELS else "gemini-flash-latest"
    models_to_try = [selected_model] + [m for m in candidate_models if m != selected_model]

    # Build system prompt
    system_text = (
        bot.get("system_prompt")
        or "أنت مساعد ذكي ومفيد وودود. أجب باللغة العربية بدقة. "
           "استخدم التنسيق الجيد مع نقاط وعناوين عند الحاجة."
    )
    if knowledge:
        kb_text = "\n\n".join([f"**{k['title']}**\n{k['content']}" for k in knowledge[:5]])
        system_text += (
            f"\n\n=== قاعدة المعرفة والمعلومات المتاحة ===\n{kb_text}\n\n"
            "استخدم هذه المعلومات للإجابة على استفسارات المستخدمين."
        )

    # Handle keyboard shortcut messages
    shortcuts = {
        "💬 تواصل معنا": "💬 <b>للتواصل معنا:</b>\n\nيمكنك مراسلتنا مباشرة وسيرد عليك فريقنا في أقرب وقت ممكن. شكراً لاهتمامك! 🙏",
        "تواصل معنا":    "💬 <b>للتواصل معنا:</b>\n\nيمكنك مراسلتنا مباشرة وسيرد عليك فريقنا في أقرب وقت ممكن. شكراً لاهتمامك! 🙏",
        "🌐 عن الخدمة":  "🌐 <b>عن خدمتنا:</b>\n\nنحن نقدم خدمة ذكاء اصطناعي متطورة للرد على استفساراتكم على مدار الساعة. 🤖✨",
        "عن الخدمة":     "🌐 <b>عن خدمتنا:</b>\n\nنحن نقدم خدمة ذكاء اصطناعي متطورة للرد على استفساراتكم على مدار الساعة. 🤖✨",
        "🆘 مساعدة":     "🆘 <b>المساعدة:</b>\n\nاكتب استفسارك وسأقوم بالإجابة عليه فوراً. أنا هنا لمساعدتك! 😊",
        "مساعدة":        "🆘 <b>المساعدة:</b>\n\nاكتب استفسارك وسأقوم بالإجابة عليه فوراً. أنا هنا لمساعدتك! 😊",
        "✨ بدء جديد":   "✨ حسناً، لنبدأ من جديد!\n\nكيف يمكنني مساعدتك اليوم؟ 😊",
        "بدء جديد":      "✨ حسناً، لنبدأ من جديد!\n\nكيف يمكنني مساعدتك اليوم؟ 😊",
    }
    if user_message in shortcuts:
        return shortcuts[user_message]

    # Build conversation history
    # Use more history for continuation messages
    history_limit = 6 if is_cont else 4
    contents = []
    for msg in chat_history[-history_limit:]:
        role = "user" if msg.get("role") == "user" else "model"
        content = msg.get("content", "").strip()
        if content:
            contents.append({"role": role, "parts": [{"text": content}]})

    # Build user prompt with context injection for continuations
    if is_cont:
        user_prompt = (
            f"تعليمات النظام:\n{system_text}\n\n"
            f"رسالة المستخدم:\n{user_message}\n\n"
            "[تعليمة داخلية: المستخدم يريد الاستمرار في الموضوع السابق أو الاستفسار عنه. "
            "اعتمد على سياق المحادثة الأخيرة وأكمل الشرح أو أجب على استفساره بشكل طبيعي ومتسلسل. "
            "لا تبدأ موضوعاً جديداً.]"
        )
    else:
        user_prompt = f"تعليمات النظام:\n{system_text}\n\nرسالة المستخدم:\n{user_message}"

    contents.append({"role": "user", "parts": [{"text": user_prompt}]})

    payload = {
        "contents": contents,
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 2048,   # ← رفعنا من 800 إلى 2048
        }
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        for model_name in models_to_try:
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model_name}:generateContent?key={settings.GEMINI_API_KEY}"
            )
            try:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts and "text" in parts[0]:
                            return parts[0]["text"].strip()
                elif resp.status_code in (404, 400, 410):
                    logger.warning(f"Gemini {model_name} failed {resp.status_code}: {resp.text[:120]}")
                    continue  # try next model
                else:
                    logger.warning(f"Gemini {model_name} failed {resp.status_code}: {resp.text[:120]}")
            except httpx.TimeoutException as e:
                logger.warning(f"Timeout querying Gemini {model_name}: {e}")
            except Exception as e:
                logger.warning(f"Error querying Gemini {model_name}: {e}")

    return "أهلاً بك! تلقينا استفسارك وسيقوم أحد ممثلي الخدمة بالرد عليك قريباً. 🙏"


# ─── Main Update Processor ────────────────────────────────────────────────────

async def process_telegram_update(bot: dict, update: dict, db: aiosqlite.Connection):
    """Process a single Telegram update (from Webhook or Polling)."""

    # ── Callback queries (inline keyboard buttons) ──
    if "callback_query" in update:
        await _handle_callback_query(bot, update["callback_query"], db)
        return

    # ── Bot added/removed from group (my_chat_member) ──
    if "my_chat_member" in update:
        await _handle_my_chat_member(bot, update["my_chat_member"], db)
        return

    message = update.get("message") or update.get("edited_message")
    if not message:
        return

    # ── New members joined a group ──
    if message.get("new_chat_members"):
        await _handle_new_chat_members(bot, message, db)
        return

    text = message.get("text", "").strip()
    chat = message.get("chat", {})
    chat_id = str(chat.get("id", ""))
    chat_type = chat.get("type", "private")
    is_group = chat_type in ("group", "supergroup")
    message_id = message.get("message_id")
    chat_title = chat.get("title", "") or ""

    from_user = message.get("from") or {}
    first_name = from_user.get("first_name", "")
    last_name = from_user.get("last_name", "")
    full_name = f"{first_name} {last_name}".strip()
    chat_name = chat_title if is_group else (full_name or chat.get("first_name") or chat_id)
    chat_username = from_user.get("username") or chat.get("username", "")
    telegram_user_id = str(from_user.get("id", ""))

    if not text or not chat_id:
        return

    bot_id = bot["id"]
    token = bot["token"]
    allowed_group_id = str(bot.get("allowed_group_id") or "").strip()
    group_trigger = (bot.get("group_trigger") or "botrun").strip().lower()
    clean_lower_text = text.lower().strip()

    # ── Group Binding Commands ──
    if is_group:
        is_bind_cmd = (
            clean_lower_text in ("/bind", "/setgroup", "botrun bind", "botrun ربط")
            or clean_lower_text.startswith("/bind@")
            or clean_lower_text.startswith("/setgroup@")
        )
        if is_bind_cmd:
            await db.execute("""
                UPDATE bots
                SET allowed_group_id = ?, allowed_group_title = ?, updated_at = datetime('now')
                WHERE id = ?
            """, (chat_id, chat_title or f"مجموعة #{chat_id}", bot_id))
            await db.commit()
            bot["allowed_group_id"] = chat_id
            bot["allowed_group_title"] = chat_title

            confirm_msg = (
                f"🔒 <b>تم ربط البوت بهذه المجموعة بنجاح!</b>\n\n"
                f"🏢 المجموعة: <b>{chat_title or 'هذه المجموعة'}</b>\n"
                f"🆔 المعرّف: <code>{chat_id}</code>\n"
                f"⚡ كلمة التفعيل: <b>{group_trigger}</b>\n\n"
                f"من الآن فصاعداً سيعمل البوت حصرياً هنا. اكتب "
                f"<b>{group_trigger}</b> متبوعاً بسؤالك وسأجيب فوراً. 🤖✨"
            )
            await send_chat_action(token, chat_id, "typing")
            await send_telegram_message(token, chat_id, confirm_msg, reply_to_message_id=message_id)
            return

        is_unbind_cmd = (
            clean_lower_text in ("/unbind", "botrun unbind", "botrun إلغاء الربط")
            or clean_lower_text.startswith("/unbind@")
        )
        if is_unbind_cmd:
            if allowed_group_id and allowed_group_id == chat_id:
                await db.execute("""
                    UPDATE bots
                    SET allowed_group_id = NULL, allowed_group_title = NULL, updated_at = datetime('now')
                    WHERE id = ?
                """, (bot_id,))
                await db.commit()
                bot["allowed_group_id"] = None
                bot["allowed_group_title"] = None
                await send_telegram_message(
                    token, chat_id,
                    "🔓 <b>تم فك ارتباط البوت بهذه المجموعة.</b>\n"
                    "يمكن إعادة الربط في أي وقت بالأمر <code>/bind</code>.",
                    reply_to_message_id=message_id
                )
            return

        # Group filtering
        if allowed_group_id and allowed_group_id != chat_id:
            return

        if not allowed_group_id:
            if group_trigger in clean_lower_text:
                await send_telegram_message(
                    token, chat_id,
                    "⚠️ <b>هذا البوت غير مربوط بهذه المجموعة بعد!</b>\n\n"
                    "لربط البوت اكتب الأمر:\n<code>/bind</code>",
                    reply_to_message_id=message_id
                )
            return

        if group_trigger not in clean_lower_text:
            return

        # Extract actual question
        extracted_query = re.sub(re.escape(group_trigger), '', text, flags=re.IGNORECASE).strip()
        extracted_query = extracted_query.lstrip(":,- ").strip()

        if not extracted_query:
            await send_telegram_message(
                token, chat_id,
                f"👋 أهلاً! اكتب استفسارك بعد كلمة <b>{group_trigger}</b> وسأجيبك فوراً. 😊\n\n"
                f"💡 <i>مثال:</i> <code>{group_trigger} ما هي خدماتكم؟</code>",
                reply_to_message_id=message_id
            )
            return

        user_query_for_ai = extracted_query
    else:
        user_query_for_ai = text

    # ── Conversation Management ──
    async with db.execute(
        "SELECT * FROM conversations WHERE bot_id = ? AND chat_id = ?", (bot_id, chat_id)
    ) as c:
        conv = await c.fetchone()

    if conv:
        conv_id = conv["id"]
        await db.execute("""
            UPDATE conversations
            SET messages_count = messages_count + 1,
                last_message = ?,
                chat_name = COALESCE(?, chat_name),
                chat_username = COALESCE(?, chat_username),
                last_activity = datetime('now')
            WHERE id = ?
        """, (text[:200], chat_name, chat_username, conv_id))
    else:
        cursor = await db.execute("""
            INSERT INTO conversations (bot_id, chat_id, chat_name, chat_username, messages_count, last_message, last_activity)
            VALUES (?, ?, ?, ?, 1, ?, datetime('now'))
        """, (bot_id, chat_id, chat_name, chat_username, text[:200]))
        conv_id = cursor.lastrowid

    # Save incoming message
    await db.execute(
        "INSERT INTO messages (conversation_id, role, content) VALUES (?, 'user', ?)",
        (conv_id, text)
    )
    await db.commit()

    # Auto-cleanup: keep only last 30 messages
    await cleanup_old_messages(db, conv_id, max_msgs=30)

    # ── /start Command ──
    if not is_group and text.startswith("/start"):
        welcome = (
            bot.get("welcome_message")
            or f"مرحباً بك! 👋\n\nأنا <b>{bot.get('name', 'SmartBot')}</b>، مساعدك الذكي.\n\nكيف يمكنني مساعدتك اليوم؟ 😊"
        )

        await db.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, 'assistant', ?)",
            (conv_id, welcome)
        )
        await db.execute(
            "UPDATE conversations SET messages_count = messages_count + 1, last_activity = datetime('now') WHERE id = ?",
            (conv_id,)
        )
        await db.commit()

        await send_chat_action(token, chat_id, "typing")
        await asyncio.sleep(0.3)

        # Check if the sender is the bot owner to show "Add to Group" button
        is_owner = False
        bot_username = ""
        async with db.execute("SELECT user_id FROM bots WHERE id = ?", (bot_id,)) as c:
            bot_row = await c.fetchone()
        if bot_row and telegram_user_id:
            owner_user_id = bot_row["user_id"]
            # Save telegram_id to owner's user row on first /start (auto-link)
            try:
                await db.execute(
                    "UPDATE users SET telegram_id = ? WHERE id = ? AND (telegram_id IS NULL OR telegram_id = '')",
                    (telegram_user_id, owner_user_id)
                )
                await db.commit()
            except Exception:
                pass

            # Check if this Telegram user ID matches the owner's saved telegram_id
            async with db.execute(
                "SELECT telegram_id FROM users WHERE id = ?", (owner_user_id,)
            ) as c:
                owner_row = await c.fetchone()
            if owner_row and owner_row["telegram_id"] and str(owner_row["telegram_id"]) == telegram_user_id:
                is_owner = True
                bot_username = await get_bot_username(token, bot_id)

        await send_telegram_message(
            token, chat_id,
            format_for_telegram(welcome),
            reply_markup=build_main_keyboard(bot),
        )
        await asyncio.sleep(0.4)
        await send_telegram_message(
            token, chat_id,
            "اختر أحد الخيارات أو اكتب استفسارك مباشرة:",
            reply_markup=build_start_inline_keyboard(
                add_group_btn=is_owner,
                bot_username=bot_username
            ),
        )
        return

    # ── Generate AI Response with persistent typing indicator ──
    async with db.execute(
        "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT 8",
        (conv_id,)
    ) as c:
        history = list(reversed([dict(r) for r in await c.fetchall()]))

    async with db.execute(
        "SELECT title, content FROM knowledge_base WHERE bot_id = ? AND is_active = 1",
        (bot_id,)
    ) as c:
        knowledge = [dict(r) for r in await c.fetchall()]

    # Detect if this is a continuation message
    cont = is_continuation(user_query_for_ai)

    # Persistent typing indicator: keeps refreshing every 4s while AI is thinking
    stop_typing = asyncio.Event()
    async def keep_typing():
        while not stop_typing.is_set():
            await send_chat_action(token, chat_id, "typing")
            try:
                await asyncio.wait_for(asyncio.shield(asyncio.sleep(4)), timeout=4)
            except Exception:
                pass

    typing_task = asyncio.create_task(keep_typing())
    try:
        if bot.get("ai_enabled", 1):
            ai_reply_raw = await get_ai_response(
                bot, user_query_for_ai, history[:-1], knowledge, db, is_cont=cont
            )
        else:
            ai_reply_raw = bot.get("welcome_message") or "شكراً لتواصلك معنا. 🙏"
    finally:
        stop_typing.set()
        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass

    ai_reply = format_for_telegram(ai_reply_raw)

    # Save AI response
    await db.execute(
        "INSERT INTO messages (conversation_id, role, content) VALUES (?, 'assistant', ?)",
        (conv_id, ai_reply_raw)
    )
    await db.execute(
        "UPDATE conversations SET messages_count = messages_count + 1, last_activity = datetime('now') WHERE id = ?",
        (conv_id,)
    )
    await db.commit()

    # Auto-cleanup after AI response too
    await cleanup_old_messages(db, conv_id, max_msgs=30)

    # Send reply
    if is_group:
        await send_telegram_message(token, chat_id, ai_reply, reply_to_message_id=message_id)
    else:
        await send_telegram_message(token, chat_id, ai_reply, reply_markup=build_main_keyboard(bot))


# ─── New Chat Members Handler ─────────────────────────────────────────────────

async def _handle_new_chat_members(bot: dict, message: dict, db: aiosqlite.Connection):
    """Send a welcome message when a new member joins the bot's bound group."""
    token = bot["token"]
    chat = message.get("chat", {})
    chat_id = str(chat.get("id", ""))
    allowed_group_id = str(bot.get("allowed_group_id") or "").strip()
    group_trigger = (bot.get("group_trigger") or "botrun").strip()

    # Only respond in the bound group
    if not allowed_group_id or allowed_group_id != chat_id:
        return

    new_members = message.get("new_chat_members", [])
    for member in new_members:
        # Don't welcome bots
        if member.get("is_bot"):
            continue

        first = member.get("first_name", "")
        last = member.get("last_name", "")
        name = f"{first} {last}".strip() or "العضو الجديد"

        welcome_msg = (
            f"🎉 <b>أهلاً وسهلاً بك يا {name}!</b>\n\n"
            f"نحن سعداء بانضمامك إلى مجموعتنا 🤝\n\n"
            f"يمكنك التفاعل مع البوت الذكي بكتابة:\n"
            f"<code>{group_trigger} [سؤالك هنا]</code>\n\n"
            f"وسأجيبك فوراً! 🤖✨"
        )
        await send_chat_action(token, chat_id, "typing")
        await asyncio.sleep(0.5)
        await send_telegram_message(token, chat_id, welcome_msg)


# ─── Bot Added to Group Handler ───────────────────────────────────────────────

async def _handle_my_chat_member(bot: dict, update: dict, db: aiosqlite.Connection):
    """Handle when the bot is added to or removed from a group."""
    token = bot["token"]
    chat = update.get("chat", {})
    chat_id = str(chat.get("id", ""))
    chat_title = chat.get("title", "هذه المجموعة")
    new_status = update.get("new_chat_member", {}).get("status", "")

    # Bot was added (member or administrator)
    if new_status in ("member", "administrator"):
        group_trigger = (bot.get("group_trigger") or "botrun").strip()
        bot_username = await get_bot_username(token, bot["id"])

        greeting = (
            f"👋 <b>السلام عليكم!</b>\n\n"
            f"أنا <b>{bot.get('name', 'SmartBot')}</b>، بوت الذكاء الاصطناعي الخاص بهذه المجموعة.\n\n"
            f"للتفاعل معي اكتب:\n"
            f"<code>{group_trigger} [سؤالك]</code>\n\n"
        )

        if new_status == "member":
            # Bot is not yet admin — request promotion
            greeting += (
                "⚙️ <b>طلب من المشرفين:</b>\n"
                "لكي أعمل بشكل مثالي، يُرجى منح البوت صلاحيات الإشراف من:\n"
                "إعدادات المجموعة ← المشرفون ← إضافة مشرف"
            )

        await send_chat_action(token, chat_id, "typing")
        await asyncio.sleep(0.5)
        await send_telegram_message(token, chat_id, greeting)


# ─── Callback Query Handler ───────────────────────────────────────────────────

async def _handle_callback_query(bot: dict, cq: dict, db: aiosqlite.Connection):
    """Handle inline keyboard callback queries."""
    token = bot["token"]
    chat_id = str(cq.get("message", {}).get("chat", {}).get("id", ""))
    cq_id = cq.get("id")
    data = cq.get("data", "")

    # Acknowledge callback
    async with httpx.AsyncClient() as client:
        try:
            await client.post(
                f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                json={"callback_query_id": cq_id},
                timeout=5
            )
        except Exception:
            pass

    if not chat_id:
        return

    if data == "start_chat":
        await send_telegram_message(
            token, chat_id,
            "💬 رائع! ابدأ بكتابة سؤالك وسأجيب عليه فوراً. أنا هنا على مدار الساعة! 🤖",
            reply_markup=build_main_keyboard(bot),
        )
    elif data == "about":
        about_text = (
            bot.get("system_prompt", "")[:400]
            or f"🌐 أنا <b>{bot.get('name', 'SmartBot')}</b>\n\nبوت ذكاء اصطناعي متطور لخدمة عملائنا الكرام على مدار الساعة. 🤖✨"
        )
        await send_telegram_message(token, chat_id, about_text)
    elif data == "contact":
        await send_telegram_message(
            token, chat_id,
            "💬 <b>للتواصل معنا:</b>\n\nاكتب استفسارك هنا وسيرد عليك فريقنا في أقرب وقت. 🙏"
        )


# ─── Polling Manager ──────────────────────────────────────────────────────────

class TelegramPollingManager:
    """
    Background manager that performs Long Polling on all active Telegram bots.
    Enables real-time Telegram bot responses without webhooks.
    """
    def __init__(self):
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._offsets: Dict[int, int] = {}

    def start(self):
        """Start the background polling loop."""
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._poll_loop())
            print("[OK] Telegram Real-Time Polling Service started.")

    async def stop(self):
        """Gracefully stop the background polling loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            print("[*] Telegram Polling Service stopped.")

    async def _poll_loop(self):
        """Continuous polling loop for all active bots."""
        while self._running:
            try:
                async with aiosqlite.connect(DB_PATH) as db:
                    db.row_factory = aiosqlite.Row

                    async with db.execute("""
                        SELECT b.* FROM bots b
                        JOIN users u ON b.user_id = u.id
                        WHERE b.is_active = 1
                          AND b.platform = 'telegram'
                          AND b.token IS NOT NULL
                          AND b.token != ''
                          AND b.token NOT LIKE 'demo_%'
                          AND b.token NOT LIKE 'test_%'
                          AND u.is_active = 1
                          AND (u.is_suspended IS NULL OR u.is_suspended = 0)
                    """) as c:
                        active_bots = [dict(r) for r in await c.fetchall()]

                    # Check maintenance mode
                    async with db.execute(
                        "SELECT value FROM platform_settings WHERE key = 'maintenance_mode'"
                    ) as c:
                        row = await c.fetchone()
                        if row and row["value"] == "1":
                            await asyncio.sleep(5)
                            continue

                    for bot in active_bots:
                        bot_id = bot["id"]
                        token = bot["token"]
                        offset = self._offsets.get(bot_id, 0)

                        try:
                            async with httpx.AsyncClient() as client:
                                url = f"https://api.telegram.org/bot{token}/getUpdates"
                                params = {
                                    "offset": offset,
                                    "timeout": 1,
                                    "limit": 20,
                                    "allowed_updates": [
                                        "message", "callback_query",
                                        "my_chat_member", "chat_member"
                                    ]
                                }
                                resp = await client.get(url, params=params, timeout=5)

                                if resp.status_code == 200:
                                    data = resp.json()
                                    updates = data.get("result", [])
                                    for up in updates:
                                        up_id = up["update_id"]
                                        self._offsets[bot_id] = max(
                                            self._offsets.get(bot_id, 0), up_id + 1
                                        )
                                        await process_telegram_update(bot, up, db)
                                elif resp.status_code == 409:
                                    # Webhook conflict — clear it
                                    await client.post(
                                        f"https://api.telegram.org/bot{token}/deleteWebhook",
                                        timeout=5
                                    )
                        except Exception as bot_err:
                            logger.debug(f"Polling error for bot {bot_id}: {bot_err}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in Telegram polling loop: {e}")

            await asyncio.sleep(1.5)


# Global singleton instance
telegram_polling_manager = TelegramPollingManager()
