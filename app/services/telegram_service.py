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


def format_for_telegram(text: str) -> str:
    """
    Convert Gemini markdown output to Telegram-compatible Markdown format.
    Safely converts **bold** to *bold* without double-converting into italic.
    """
    # 1. Code blocks: ```code``` -> `code`
    text = re.sub(r'```[\w]*\n?(.*?)```', r'`\1`', text, flags=re.DOTALL)

    # 2. Extract and protect bold tokens
    bold_tokens = []
    def save_bold(m):
        bold_tokens.append(m.group(1))
        return f"\x00BOLD_{len(bold_tokens)-1}\x00"

    text = re.sub(r'\*\*(.+?)\*\*', save_bold, text)
    text = re.sub(r'__(.+?)__', save_bold, text)

    # 3. Italic *text* -> _text_
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'_\1_', text)

    # 4. Restore bold tokens as *bold*
    for i, b in enumerate(bold_tokens):
        text = text.replace(f"\x00BOLD_{i}\x00", f"*{b}*")

    # Clean up: normalize multiple blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


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
    parse_mode: str = "Markdown",
    reply_to_message_id: Optional[int] = None
):
    """Send text message to Telegram chat with optional inline keyboard and reply-to."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(url, json=payload, timeout=10)
            if resp.status_code != 200:
                # Retry without parse_mode if formatting fails
                payload.pop("parse_mode", None)
                await client.post(url, json=payload, timeout=10)
        except Exception as e:
            logger.error(f"Failed to send Telegram message to {chat_id}: {e}")


def build_main_keyboard(bot: dict) -> dict:
    """Build a persistent reply keyboard for the bot."""
    return {
        "keyboard": [
            [{"text": "📞 تواصل معنا"}, {"text": "ℹ️ عن الخدمة"}],
            [{"text": "🔄 بدء جديد"}, {"text": "❓ مساعدة"}],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False,
    }


def build_start_inline_keyboard() -> dict:
    """Build inline keyboard for /start welcome message."""
    return {
        "inline_keyboard": [
            [
                {"text": "🚀 ابدأ المحادثة", "callback_data": "start_chat"},
                {"text": "ℹ️ عن الخدمة", "callback_data": "about"},
            ],
            [
                {"text": "📞 تواصل معنا", "callback_data": "contact"},
            ],
        ]
    }


async def get_ai_response(
    bot: dict,
    user_message: str,
    chat_history: list,
    knowledge: list,
    db: aiosqlite.Connection
) -> str:
    """Generate AI response using Gemini REST API with fast response time."""
    if not settings.GEMINI_API_KEY:
        return "أهلاً بك! شكراً لتواصلك معنا."

    # Preferred models
    candidate_models = ["gemini-3.5-flash-lite", "gemini-3.5-flash"]
    selected_model = bot.get("ai_model") or "gemini-3.5-flash-lite"
    if selected_model in ("gemini-1.5-flash", "gemini-2.5-flash", "gemini-2.0-flash"):
        selected_model = "gemini-3.5-flash-lite"

    models_to_try = [selected_model] + [m for m in candidate_models if m != selected_model]

    # Build system prompt
    system_text = (
        bot.get("system_prompt")
        or "أنت مساعد ذكي ومفيد وودود. أجب باللغة العربية بدقة واختصار. "
           "استخدم التنسيق الجيد مع نقاط وعناوين عند الحاجة."
    )
    if knowledge:
        kb_text = "\n\n".join([f"**{k['title']}**\n{k['content']}" for k in knowledge[:5]])
        system_text += (
            f"\n\n=== قاعدة المعرفة والمعلومات المتاحة ===\n{kb_text}\n\n"
            "استخدم هذه المعلومات للإجابة على استفسارات المستخدمين."
        )

    # Handle keyboard shortcut messages
    if user_message in ("📞 تواصل معنا", "تواصل معنا"):
        return "📞 *للتواصل معنا:*\n\nيمكنك مراسلتنا مباشرة وسيرد عليك فريقنا في أقرب وقت ممكن. شكراً لاهتمامك! 🙏"
    if user_message in ("ℹ️ عن الخدمة", "عن الخدمة"):
        return "ℹ️ *عن خدمتنا:*\n\nنحن نقدم خدمة ذكاء اصطناعي متطورة للرد على استفساراتكم على مدار الساعة. 🤖✨"
    if user_message in ("❓ مساعدة", "مساعدة"):
        return "❓ *المساعدة:*\n\nاكتب استفسارك وسأقوم بالإجابة عليه فوراً. أنا هنا لمساعدتك! 😊"
    if user_message in ("🔄 بدء جديد", "بدء جديد"):
        return "🔄 حسناً، لنبدأ من جديد!\n\nكيف يمكنني مساعدتك اليوم؟ 😊"

    # Build conversation history for API
    contents = []
    for msg in chat_history[-4:]:
        role = "user" if msg.get("role") == "user" else "model"
        content = msg.get("content", "").strip()
        if content:
            contents.append({"role": role, "parts": [{"text": content}]})

    user_prompt = f"تعليمات النظام:\n{system_text}\n\nرسالة المستخدم:\n{user_message}"
    contents.append({"role": "user", "parts": [{"text": user_prompt}]})

    payload = {
        "contents": contents,
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 800,
        }
    }

    async with httpx.AsyncClient() as client:
        for model_name in models_to_try:
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/"
                f"{model_name}:generateContent?key={settings.GEMINI_API_KEY}"
            )
            try:
                resp = await client.post(url, json=payload, timeout=15)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts and "text" in parts[0]:
                            return parts[0]["text"].strip()
                else:
                    logger.warning(
                        f"Gemini {model_name} failed {resp.status_code}: {resp.text[:100]}"
                    )
            except Exception as e:
                logger.warning(f"Error querying Gemini {model_name}: {e}")

    return "أهلاً بك! تلقينا استفسارك وسيقوم أحد ممثلي الخدمة بالرد عليك قريباً. 🙏"


async def process_telegram_update(bot: dict, update: dict, db: aiosqlite.Connection):
    """Process a single Telegram update (from Webhook or Polling)."""
    # Handle callback queries from inline keyboards
    if "callback_query" in update:
        cq = update["callback_query"]
        await _handle_callback_query(bot, cq, db)
        return

    message = update.get("message") or update.get("edited_message")
    if not message:
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

    if not text or not chat_id:
        return

    bot_id = bot["id"]
    token = bot["token"]
    allowed_group_id = str(bot.get("allowed_group_id") or "").strip()
    group_trigger = (bot.get("group_trigger") or "botrun").strip().lower()
    clean_lower_text = text.lower().strip()

    # Handle Group Binding Commands
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
                f"🔒 *تم ربط البوت بهذه المجموعة بنجاح!*\n\n"
                f"🏢 المجموعة: *{chat_title or 'هذه المجموعة'}*\n"
                f"🆔 المعرّف: `{chat_id}`\n"
                f"⚡ كلمة التفعيل: *{group_trigger}*\n\n"
                f"من الآن فصاعداً سيعمل البوت حصرياً هنا، وسيجيب على استفسارات الأعضاء عند كتابة كلمة *{group_trigger}* متبوعة بالسؤال. 🤖✨"
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

                unbind_msg = (
                    "🔓 *تم فك ارتباط البوت بهذه المجموعة بنجاح.*\n"
                    "لن يستجيب البوت للرسائل هنا بعد الآن حتى يتم إعادة ربطه بالأمر `/bind`."
                )
                await send_telegram_message(token, chat_id, unbind_msg, reply_to_message_id=message_id)
            return

        # Filtering in Group Mode:
        # 1. If an allowed group is configured and this message is from a DIFFERENT group -> ignore completely
        if allowed_group_id and allowed_group_id != chat_id:
            return

        # 2. If no group is bound yet and user mentions the trigger word
        if not allowed_group_id:
            if group_trigger in clean_lower_text:
                not_bound_msg = (
                    "⚠️ *هذا البوت غير مربوط بهذه المجموعة بعد!*\n\n"
                    "لربط البوت بهذه المجموعة حصرياً وتفعيل الرد الآلي، يرجى كتابة الأمر:\n"
                    "`/bind`"
                )
                await send_telegram_message(token, chat_id, not_bound_msg, reply_to_message_id=message_id)
            return

        # 3. If this IS the bound group, only respond if trigger word is present
        if group_trigger not in clean_lower_text:
            return

        # 4. Extract actual question
        extracted_query = re.sub(re.escape(group_trigger), '', text, flags=re.IGNORECASE).strip()
        extracted_query = extracted_query.lstrip(":,- ").strip()

        if not extracted_query:
            hint_msg = (
                f"👋 أهلاً بك! أنا في الخدمة.\n"
                f"اكتب استفسارك بعد كلمة *{group_trigger}* وسأجيبك فوراً. 😊\n\n"
                f"💡 *مثال:* `{group_trigger} ما هي خدماتكم وأوقات العمل؟`"
            )
            await send_telegram_message(token, chat_id, hint_msg, reply_to_message_id=message_id)
            return

        user_query_for_ai = extracted_query
    else:
        user_query_for_ai = text

    # 1. Get or create conversation in database for every message
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

    # 2. Save incoming user message
    await db.execute(
        "INSERT INTO messages (conversation_id, role, content) VALUES (?, 'user', ?)",
        (conv_id, text)
    )
    await db.commit()

    # 3. Handle /start command (only in private chat)
    if not is_group and text.startswith("/start"):
        welcome = (
            bot.get("welcome_message")
            or f"مرحباً بك! 👋\n\nأنا *{bot.get('name', 'SmartBot')}*، مساعدك الذكي.\n\nكيف يمكنني مساعدتك اليوم؟ 😊"
        )
        # Send typing action
        await send_chat_action(token, chat_id, "typing")
        await asyncio.sleep(0.3)

        # Save welcome response to messages
        await db.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, 'assistant', ?)",
            (conv_id, welcome)
        )
        await db.execute(
            "UPDATE conversations SET messages_count = messages_count + 1, last_activity = datetime('now') WHERE id = ?",
            (conv_id,)
        )
        await db.commit()

        await send_telegram_message(
            token, chat_id,
            welcome,
            reply_markup=build_main_keyboard(bot),
        )
        # Also send greeting with inline options
        await asyncio.sleep(0.4)
        await send_telegram_message(
            token, chat_id,
            "اختر أحد الخيارات أو اكتب استفسارك مباشرة:",
            reply_markup=build_start_inline_keyboard(),
        )
        return

    # Send typing indicator
    await send_chat_action(token, chat_id, "typing")

    # Fetch recent chat history
    async with db.execute(
        "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT 8",
        (conv_id,)
    ) as c:
        history = list(reversed([dict(r) for r in await c.fetchall()]))

    # Fetch knowledge base
    async with db.execute(
        "SELECT title, content FROM knowledge_base WHERE bot_id = ? AND is_active = 1",
        (bot_id,)
    ) as c:
        knowledge = [dict(r) for r in await c.fetchall()]

    # Generate AI response using user_query_for_ai
    if bot.get("ai_enabled", 1):
        ai_reply_raw = await get_ai_response(bot, user_query_for_ai, history[:-1], knowledge, db)
    else:
        ai_reply_raw = bot.get("welcome_message") or "شكراً لتواصلك معنا. 🙏"

    # Format the reply for Telegram
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

    # Send formatted AI reply:
    # In groups: Reply directly to the message without keyboard
    # In private: Reply with keyboard
    if is_group:
        await send_telegram_message(
            token, chat_id,
            ai_reply,
            reply_to_message_id=message_id,
        )
    else:
        await send_telegram_message(
            token, chat_id,
            ai_reply,
            reply_markup=build_main_keyboard(bot),
        )


async def _handle_callback_query(bot: dict, cq: dict, db: aiosqlite.Connection):
    """Handle inline keyboard callback queries."""
    token = bot["token"]
    chat_id = str(cq.get("message", {}).get("chat", {}).get("id", ""))
    cq_id = cq.get("id")
    data = cq.get("data", "")

    # Acknowledge the callback
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
            bot.get("system_prompt", "")
            or f"🤖 أنا *{bot.get('name', 'SmartBot')}*\n\nبوت ذكاء اصطناعي متطور لخدمة عملائنا الكرام."
        )
        await send_telegram_message(token, chat_id, about_text[:500])
    elif data == "contact":
        await send_telegram_message(
            token, chat_id,
            "📞 *للتواصل معنا:*\n\nاكتب استفسارك هنا وسيرد عليك فريقنا في أقرب وقت. 🙏"
        )


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
                                params = {"offset": offset, "timeout": 1, "limit": 20}
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
