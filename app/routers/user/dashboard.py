"""
User Dashboard Router
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from datetime import datetime
import aiosqlite

from app.core.database import get_db
from app.core.security import decode_token
from app.core.templates import templates

router = APIRouter()


def require_user(request: Request):
    token = request.cookies.get("access_token")
    if not token:
        return None
    payload = decode_token(token)
    if not payload or payload.get("type") != "user":
        return None
    return payload


def get_subscription_status(sub: dict) -> dict:
    """Calculate subscription status and days remaining."""
    if not sub:
        return {"status": "none", "days_left": 0, "label": "لا يوجد اشتراك"}

    now = datetime.utcnow()
    status = sub.get("status", "trial")

    if status == "trial":
        trial_end = sub.get("trial_end")
        if trial_end:
            end_dt = datetime.fromisoformat(trial_end.replace("Z", ""))
            days_left = max(0, (end_dt - now).days)
            if days_left > 0:
                return {"status": "trial", "days_left": days_left, "label": f"تجربة مجانية ({days_left} يوم)"}
        return {"status": "expired", "days_left": 0, "label": "انتهت التجربة"}

    elif status == "active":
        sub_end = sub.get("subscription_end")
        if sub_end:
            end_dt = datetime.fromisoformat(sub_end.replace("Z", ""))
            days_left = max(0, (end_dt - now).days)
            if days_left > 0:
                return {"status": "active", "days_left": days_left, "label": f"نشط ({days_left} يوم)"}
        return {"status": "expired", "days_left": 0, "label": "منتهي الصلاحية"}

    return {"status": "expired", "days_left": 0, "label": "منتهي الصلاحية"}


@router.get("/dashboard", response_class=HTMLResponse)
async def user_dashboard(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    user_payload = require_user(request)
    if not user_payload:
        return RedirectResponse("/login", status_code=302)

    user_id = int(user_payload["sub"])

    async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as c:
        user = dict(await c.fetchone())

    async with db.execute(
        "SELECT * FROM subscriptions WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,)
    ) as c:
        sub = await c.fetchone()
        sub = dict(sub) if sub else None

    sub_info = get_subscription_status(sub)

    async with db.execute(
        "SELECT * FROM bots WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
    ) as c:
        bots = [dict(r) for r in await c.fetchall()]

    # Total messages
    async with db.execute("""
        SELECT COALESCE(SUM(c.messages_count), 0) as total
        FROM conversations c JOIN bots b ON c.bot_id = b.id
        WHERE b.user_id = ?
    """, (user_id,)) as c:
        row = await c.fetchone()
        total_messages = row["total"] if row else 0

    # Recent Conversations across all bots of this user
    async with db.execute("""
        SELECT c.*, b.name as bot_name
        FROM conversations c
        JOIN bots b ON c.bot_id = b.id
        WHERE b.user_id = ?
        ORDER BY c.last_activity DESC
        LIMIT 10
    """, (user_id,)) as c:
        recent_conversations = [dict(r) for r in await c.fetchall()]

    return templates.TemplateResponse(
        request=request,
        name="user/dashboard.html",
        context={
            "request": request,
            "user": user,
            "subscription": sub,
            "sub_info": sub_info,
            "bots": bots,
            "total_messages": total_messages,
            "recent_conversations": recent_conversations,
            "page": "dashboard"
        }
    )
