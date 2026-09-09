"""
User Bots Router - CRUD for Telegram bots
"""
from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import aiosqlite
import httpx
import json

from app.core.database import get_db
from app.core.security import decode_token
from app.core.config import settings

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def require_user(request: Request):
    token = request.cookies.get("access_token")
    if not token:
        return None
    payload = decode_token(token)
    if not payload or payload.get("type") != "user":
        return None
    return payload


async def check_subscription(user_id: int, db: aiosqlite.Connection) -> bool:
    """Check if user has active subscription or valid trial."""
    from datetime import datetime
    async with db.execute(
        "SELECT * FROM subscriptions WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,)
    ) as c:
        sub = await c.fetchone()
    if not sub:
        return False
    now = datetime.utcnow()
    if sub["status"] == "trial" and sub["trial_end"]:
        end = datetime.fromisoformat(sub["trial_end"].replace("Z", ""))
        return now < end
    if sub["status"] == "active" and sub["subscription_end"]:
        end = datetime.fromisoformat(sub["subscription_end"].replace("Z", ""))
        return now < end
    return False


@router.get("/bots", response_class=HTMLResponse)
async def user_bots(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    user_payload = require_user(request)
    if not user_payload:
        return RedirectResponse("/login", status_code=302)

    user_id = int(user_payload["sub"])
    async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as c:
        user = dict(await c.fetchone())

    async with db.execute("""
        SELECT b.*, 
               (SELECT COUNT(*) FROM conversations WHERE bot_id = b.id) as conv_count,
               (SELECT COUNT(*) FROM knowledge_base WHERE bot_id = b.id) as kb_count
        FROM bots b WHERE b.user_id = ? ORDER BY b.created_at DESC
    """, (user_id,)) as c:
        bots = [dict(r) for r in await c.fetchall()]

    has_sub = await check_subscription(user_id, db)

    return templates.TemplateResponse("user/bots.html", {
        "request": request,
        "user": user,
        "bots": bots,
        "has_subscription": has_sub,
        "page": "bots"
    })


@router.get("/bots/new", response_class=HTMLResponse)
async def new_bot_page(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    user_payload = require_user(request)
    if not user_payload:
        return RedirectResponse("/login", status_code=302)

    user_id = int(user_payload["sub"])
    has_sub = await check_subscription(user_id, db)
    if not has_sub:
        return RedirectResponse("/subscription", status_code=302)

    async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as c:
        user = dict(await c.fetchone())

    return templates.TemplateResponse("user/bot_new.html", {
        "request": request,
        "user": user,
        "page": "bots"
    })


@router.post("/bots/new")
async def create_bot(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    token: str = Form(...),
    welcome_message: str = Form(""),
    system_prompt: str = Form(""),
    language: str = Form("ar"),
    db: aiosqlite.Connection = Depends(get_db)
):
    user_payload = require_user(request)
    if not user_payload:
        return RedirectResponse("/login", status_code=302)

    user_id = int(user_payload["sub"])
    has_sub = await check_subscription(user_id, db)
    if not has_sub:
        return RedirectResponse("/subscription", status_code=302)

    # ── Enforce 1 bot per user ──────────────────────────
    async with db.execute("SELECT id FROM bots WHERE user_id = ?", (user_id,)) as c:
        existing_bot = await c.fetchone()
    if existing_bot:
        async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as c:
            user = dict(await c.fetchone())
        return templates.TemplateResponse("user/bot_new.html", {
            "request": request,
            "user": user,
            "error": "⚠️ يُسمح بإنشاء بوت واحد فقط لكل مستخدم. يمكنك تعديل إعدادات بوتك الحالي من صفحة البوتات.",
            "page": "bots",
            "has_bot_already": True,
        }, status_code=400)
    # ─────────────────────────────────────────────────────

    # Validate Telegram token
    bot_info = {}
    if settings.DEBUG and (token.startswith("test_") or token.startswith("demo_")):
        bot_info = {"first_name": name or "Test Bot", "username": "test_bot"}
    else:
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
                data = resp.json()
                if not data.get("ok"):
                    async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as c:
                        user = dict(await c.fetchone())
                    return templates.TemplateResponse("user/bot_new.html", {
                        "request": request,
                        "user": user,
                        "error": "توكن التليغرام غير صالح. تحقق من التوكن وأعد المحاولة.",
                        "page": "bots"
                    }, status_code=400)
                bot_info = data["result"]
            except Exception:
                async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as c:
                    user = dict(await c.fetchone())
                return templates.TemplateResponse("user/bot_new.html", {
                    "request": request,
                    "user": user,
                    "error": "تعذر الاتصال بـ Telegram API. تحقق من الاتصال.",
                    "page": "bots"
                }, status_code=400)

    # Check token not already used
    async with db.execute("SELECT id FROM bots WHERE token = ?", (token,)) as c:
        existing = await c.fetchone()
    if existing:
        async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as c:
            user = dict(await c.fetchone())
        return templates.TemplateResponse("user/bot_new.html", {
            "request": request,
            "user": user,
            "error": "هذا التوكن مستخدم بالفعل.",
            "page": "bots"
        }, status_code=400)

    bot_name = name or bot_info.get("first_name", "My Bot")
    await db.execute("""
        INSERT INTO bots (user_id, name, description, platform, token, welcome_message, system_prompt, language)
        VALUES (?, ?, ?, 'telegram', ?, ?, ?, ?)
    """, (user_id, bot_name, description, token, welcome_message, system_prompt, language))
    await db.commit()
    return RedirectResponse("/bots", status_code=302)


@router.get("/bots/{bot_id}", response_class=HTMLResponse)
async def bot_detail(bot_id: int, request: Request, db: aiosqlite.Connection = Depends(get_db)):
    user_payload = require_user(request)
    if not user_payload:
        return RedirectResponse("/login", status_code=302)

    user_id = int(user_payload["sub"])
    async with db.execute("SELECT * FROM bots WHERE id = ? AND user_id = ?", (bot_id, user_id)) as c:
        bot = await c.fetchone()
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")

    bot = dict(bot)
    async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as c:
        user = dict(await c.fetchone())

    async with db.execute(
        "SELECT * FROM knowledge_base WHERE bot_id = ? ORDER BY created_at DESC", (bot_id,)
    ) as c:
        knowledge = [dict(r) for r in await c.fetchall()]

    async with db.execute(
        "SELECT * FROM conversations WHERE bot_id = ? ORDER BY last_activity DESC LIMIT 10", (bot_id,)
    ) as c:
        conversations = [dict(r) for r in await c.fetchall()]

    return templates.TemplateResponse("user/bot_detail.html", {
        "request": request,
        "user": user,
        "bot": bot,
        "knowledge": knowledge,
        "conversations": conversations,
        "page": "bots"
    })


@router.post("/bots/{bot_id}/toggle")
async def toggle_bot(bot_id: int, request: Request, db: aiosqlite.Connection = Depends(get_db)):
    user_payload = require_user(request)
    if not user_payload:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    user_id = int(user_payload["sub"])
    async with db.execute("SELECT * FROM bots WHERE id = ? AND user_id = ?", (bot_id, user_id)) as c:
        bot = await c.fetchone()
    if not bot:
        return JSONResponse({"error": "Not found"}, status_code=404)

    new_status = 0 if bot["is_active"] else 1
    await db.execute("UPDATE bots SET is_active = ?, updated_at = datetime('now') WHERE id = ?", (new_status, bot_id))
    await db.commit()

    if new_status and bot["token"]:
        from app.core.config import settings
        webhook_url = f"{settings.APP_URL}/webhook/telegram/{bot_id}"
        async with httpx.AsyncClient() as client:
            try:
                await client.post(
                    f"https://api.telegram.org/bot{bot['token']}/setWebhook",
                    json={"url": webhook_url}, timeout=10
                )
            except Exception:
                pass

    return JSONResponse({"success": True, "is_active": new_status})


@router.post("/bots/{bot_id}/delete")
async def delete_bot(bot_id: int, request: Request, db: aiosqlite.Connection = Depends(get_db)):
    user_payload = require_user(request)
    if not user_payload:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    user_id = int(user_payload["sub"])
    async with db.execute("SELECT * FROM bots WHERE id = ? AND user_id = ?", (bot_id, user_id)) as c:
        bot = await c.fetchone()
    if not bot:
        return JSONResponse({"error": "Not found"}, status_code=404)

    # Remove webhook
    if bot["token"]:
        async with httpx.AsyncClient() as client:
            try:
                await client.post(
                    f"https://api.telegram.org/bot{bot['token']}/deleteWebhook",
                    timeout=10
                )
            except Exception:
                pass

    await db.execute("DELETE FROM bots WHERE id = ?", (bot_id,))
    await db.commit()
    return JSONResponse({"success": True})


@router.post("/bots/{bot_id}/settings")
async def update_bot_settings(
    bot_id: int,
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    welcome_message: str = Form(""),
    system_prompt: str = Form(""),
    ai_enabled: int = Form(1),
    language: str = Form("ar"),
    allowed_group_id: str = Form(""),
    group_trigger: str = Form("botrun"),
    db: aiosqlite.Connection = Depends(get_db)
):
    user_payload = require_user(request)
    if not user_payload:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    user_id = int(user_payload["sub"])
    async with db.execute("SELECT id FROM bots WHERE id = ? AND user_id = ?", (bot_id, user_id)) as c:
        if not await c.fetchone():
            return JSONResponse({"error": "Not found"}, status_code=404)

    allowed_group_id_val = allowed_group_id.strip() or None
    group_trigger_val = group_trigger.strip() or "botrun"

    await db.execute("""
        UPDATE bots 
        SET name = ?, description = ?, welcome_message = ?, system_prompt = ?, 
            ai_enabled = ?, language = ?, allowed_group_id = ?, group_trigger = ?, updated_at = datetime('now')
        WHERE id = ? AND user_id = ?
    """, (name, description, welcome_message, system_prompt, ai_enabled, language, allowed_group_id_val, group_trigger_val, bot_id, user_id))
    await db.commit()
    return JSONResponse({"success": True, "message": "تم تحديث إعدادات البوت بنجاح"})


@router.post("/bots/{bot_id}/unbind-group")
async def unbind_bot_group(
    bot_id: int,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db)
):
    user_payload = require_user(request)
    if not user_payload:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    user_id = int(user_payload["sub"])
    async with db.execute("SELECT id FROM bots WHERE id = ? AND user_id = ?", (bot_id, user_id)) as c:
        if not await c.fetchone():
            return JSONResponse({"error": "Not found"}, status_code=404)

    await db.execute("""
        UPDATE bots 
        SET allowed_group_id = NULL, allowed_group_title = NULL, updated_at = datetime('now')
        WHERE id = ? AND user_id = ?
    """, (bot_id, user_id))
    await db.commit()
    return JSONResponse({"success": True, "message": "تم فك ارتباط المجموعة بنجاح"})


@router.post("/bots/{bot_id}/knowledge")
async def add_knowledge(
    bot_id: int,
    request: Request,
    title: str = Form(...),
    content: str = Form(...),
    db: aiosqlite.Connection = Depends(get_db)
):
    user_payload = require_user(request)
    if not user_payload:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    user_id = int(user_payload["sub"])
    async with db.execute("SELECT id FROM bots WHERE id = ? AND user_id = ?", (bot_id, user_id)) as c:
        if not await c.fetchone():
            return JSONResponse({"error": "Not found"}, status_code=404)

    await db.execute(
        "INSERT INTO knowledge_base (bot_id, title, content) VALUES (?, ?, ?)",
        (bot_id, title, content)
    )
    await db.commit()
    return JSONResponse({"success": True})


@router.post("/bots/{bot_id}/knowledge/{kb_id}/delete")
async def delete_knowledge(bot_id: int, kb_id: int, request: Request, db: aiosqlite.Connection = Depends(get_db)):
    user_payload = require_user(request)
    if not user_payload:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    user_id = int(user_payload["sub"])
    async with db.execute("SELECT id FROM bots WHERE id = ? AND user_id = ?", (bot_id, user_id)) as c:
        if not await c.fetchone():
            return JSONResponse({"error": "Not found"}, status_code=404)

    await db.execute("DELETE FROM knowledge_base WHERE id = ? AND bot_id = ?", (kb_id, bot_id))
    await db.commit()
    return JSONResponse({"success": True})


@router.get("/conversations/{conv_id}/messages")
async def get_conversation_messages(conv_id: int, request: Request, db: aiosqlite.Connection = Depends(get_db)):
    user_payload = require_user(request)
    if not user_payload:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    user_id = int(user_payload["sub"])
    # Verify the conversation belongs to one of this user's bots
    async with db.execute("""
        SELECT c.*, b.name as bot_name
        FROM conversations c
        JOIN bots b ON c.bot_id = b.id
        WHERE c.id = ? AND b.user_id = ?
    """, (conv_id, user_id)) as c:
        conv = await c.fetchone()
    if not conv:
        return JSONResponse({"error": "Conversation not found"}, status_code=404)

    conv_dict = dict(conv)
    async with db.execute("""
        SELECT id, role, content, created_at
        FROM messages
        WHERE conversation_id = ?
        ORDER BY id ASC
    """, (conv_id,)) as c:
        messages = [dict(r) for r in await c.fetchall()]

    return JSONResponse({
        "success": True,
        "conversation": conv_dict,
        "messages": messages
    })

