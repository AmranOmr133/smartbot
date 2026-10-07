"""
Telegram Webhook Handler
"""
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import JSONResponse
import aiosqlite
import json

from app.core.database import get_db
from app.core.config import settings

router = APIRouter()


async def get_ai_response(bot: dict, user_message: str, chat_history: list, knowledge: list, db: aiosqlite.Connection) -> str:
    """Generate AI response using Gemini."""
    if not settings.GEMINI_API_KEY:
        return "عذراً، خدمة الذكاء الاصطناعي غير متاحة حالياً."

    try:
        import google.generativeai as genai
        genai.configure(api_key=settings.GEMINI_API_KEY)
        model = genai.GenerativeModel(bot.get("ai_model", "gemini-3.8-flash"))

        # Build context
        system = bot.get("system_prompt") or "أنت مساعد ذكي ومفيد. أجب باللغة العربية."
        if knowledge:
            kb_text = "\n\n".join([f"**{k['title']}**\n{k['content']}" for k in knowledge[:5]])
            system += f"\n\n=== قاعدة المعرفة ===\n{kb_text}"

        history = []
        for msg in chat_history[-6:]:
            history.append({
                "role": "user" if msg["role"] == "user" else "model",
                "parts": [msg["content"]]
            })

        chat = model.start_chat(history=history)
        response = await chat.send_message_async(user_message)
        return response.text

    except Exception as e:
        return f"عذراً، حدث خطأ في معالجة طلبك. الرجاء المحاولة مرة أخرى."


async def send_telegram_message(token: str, chat_id: str, text: str):
    """Send message via Telegram API."""
    import httpx
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient() as client:
        await client.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }, timeout=15)


@router.post("/telegram/{bot_id}")
async def telegram_webhook(bot_id: int, request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """Handle Telegram webhook updates."""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Get bot
    async with db.execute(
        "SELECT * FROM bots WHERE id = ? AND is_active = 1 AND platform = 'telegram'", (bot_id,)
    ) as c:
        bot = await c.fetchone()

    if not bot:
        return JSONResponse({"ok": True})

    bot = dict(bot)
    from app.services.telegram_service import process_telegram_update
    try:
        await process_telegram_update(bot, data, db)
    except Exception as e:
        logger.error(f"Error processing webhook update: {e}")

    return JSONResponse({"ok": True})
