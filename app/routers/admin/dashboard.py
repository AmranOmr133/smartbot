"""
Admin Dashboard & Main Pages Router — Full Admin Control
"""
from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from datetime import datetime, timedelta
import aiosqlite

from app.core.database import get_db
from app.core.security import decode_token, hash_password
from app.core.templates import templates

router = APIRouter()


def require_admin(request: Request):
    """Check admin authentication."""
    token = request.cookies.get("admin_token") or request.cookies.get("access_token")
    if not token:
        return None
    payload = decode_token(token)
    if not payload or payload.get("type") != "admin":
        return None
    return payload


# ── Dashboard ─────────────────────────────────────────────────
@router.get("/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    admin = require_admin(request)
    if not admin:
        return RedirectResponse("/admin/login", status_code=302)

    async with db.execute("SELECT COUNT(*) as cnt FROM users WHERE is_active = 1") as c:
        total_users = (await c.fetchone())["cnt"]
    async with db.execute("SELECT COUNT(*) as cnt FROM users WHERE date(created_at) = date('now')") as c:
        new_users_today = (await c.fetchone())["cnt"]
    async with db.execute("SELECT COUNT(*) as cnt FROM bots WHERE is_active = 1") as c:
        active_bots = (await c.fetchone())["cnt"]
    async with db.execute("SELECT COUNT(*) as cnt FROM subscriptions WHERE status = 'active'") as c:
        active_subs = (await c.fetchone())["cnt"]
    async with db.execute("SELECT COUNT(*) as cnt FROM payment_requests WHERE status = 'pending'") as c:
        pending_payments = (await c.fetchone())["cnt"]
    async with db.execute("SELECT SUM(amount) as total FROM payment_requests WHERE status = 'approved'") as c:
        row = await c.fetchone()
        total_revenue = row["total"] or 0
    async with db.execute("SELECT COUNT(*) as cnt FROM conversations") as c:
        total_conversations = (await c.fetchone())["cnt"]

    # Maintenance mode
    async with db.execute("SELECT value FROM platform_settings WHERE key = 'maintenance_mode'") as c:
        row = await c.fetchone()
        maintenance_mode = row["value"] == "1" if row else False

    async with db.execute(
        "SELECT u.*, s.status as sub_status FROM users u LEFT JOIN subscriptions s ON s.user_id = u.id ORDER BY u.created_at DESC LIMIT 5"
    ) as cursor:
        recent_users = [dict(r) for r in await cursor.fetchall()]

    async with db.execute(
        "SELECT pr.*, u.full_name, u.email FROM payment_requests pr JOIN users u ON pr.user_id = u.id ORDER BY pr.created_at DESC LIMIT 5"
    ) as cursor:
        recent_payments = [dict(r) for r in await cursor.fetchall()]

    return templates.TemplateResponse(
        request=request,
        name="admin/dashboard.html",
        context={
            "request": request,
            "admin": admin,
            "stats": {
                "total_users": total_users,
                "new_users_today": new_users_today,
                "active_bots": active_bots,
                "active_subs": active_subs,
                "pending_payments": pending_payments,
                "total_revenue": total_revenue,
                "total_conversations": total_conversations,
            },
            "recent_users": recent_users,
            "recent_payments": recent_payments,
            "maintenance_mode": maintenance_mode,
            "page": "dashboard"
        }
    )


# ── Users List ────────────────────────────────────────────────
@router.get("/users", response_class=HTMLResponse)
async def admin_users(request: Request, search: str = "", db: aiosqlite.Connection = Depends(get_db)):
    admin = require_admin(request)
    if not admin:
        return RedirectResponse("/admin/login", status_code=302)

    if search:
        query = """
            SELECT u.*, s.status as sub_status, s.trial_end, s.subscription_end,
                   (SELECT COUNT(*) FROM bots WHERE user_id = u.id) as bots_count
            FROM users u
            LEFT JOIN subscriptions s ON s.user_id = u.id
            WHERE u.email LIKE ? OR u.full_name LIKE ?
            ORDER BY u.created_at DESC
        """
        params = (f"%{search}%", f"%{search}%")
    else:
        query = """
            SELECT u.*, s.status as sub_status, s.trial_end, s.subscription_end,
                   (SELECT COUNT(*) FROM bots WHERE user_id = u.id) as bots_count
            FROM users u
            LEFT JOIN subscriptions s ON s.user_id = u.id
            ORDER BY u.created_at DESC
        """
        params = ()

    async with db.execute(query, params) as cursor:
        users = [dict(r) for r in await cursor.fetchall()]

    # Pending payments count for badge
    async with db.execute("SELECT COUNT(*) as cnt FROM payment_requests WHERE status = 'pending'") as c:
        pending_payments = (await c.fetchone())["cnt"]

    return templates.TemplateResponse(
        request=request,
        name="admin/users.html",
        context={
            "request": request,
            "admin": admin,
            "users": users,
            "search": search,
            "stats": {"pending_payments": pending_payments},
            "page": "users"
        }
    )


# ── User Detail ───────────────────────────────────────────────
@router.get("/users/{user_id}", response_class=HTMLResponse)
async def admin_user_detail(user_id: int, request: Request, db: aiosqlite.Connection = Depends(get_db)):
    admin = require_admin(request)
    if not admin:
        return RedirectResponse("/admin/login", status_code=302)

    async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as c:
        user = await c.fetchone()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user = dict(user)

    async with db.execute("SELECT * FROM subscriptions WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,)) as c:
        sub = await c.fetchone()
        sub = dict(sub) if sub else None

    async with db.execute("SELECT * FROM bots WHERE user_id = ? ORDER BY created_at DESC", (user_id,)) as c:
        bots = [dict(r) for r in await c.fetchall()]

    async with db.execute(
        "SELECT * FROM payment_requests WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
    ) as c:
        payments = [dict(r) for r in await c.fetchall()]

    async with db.execute("SELECT COUNT(*) as cnt FROM payment_requests WHERE status = 'pending'") as c:
        pending_payments = (await c.fetchone())["cnt"]

    return templates.TemplateResponse(
        request=request,
        name="admin/user_detail.html",
        context={
            "request": request,
            "admin": admin,
            "target_user": user,
            "subscription": sub,
            "bots": bots,
            "payments": payments,
            "stats": {"pending_payments": pending_payments},
            "page": "users"
        }
    )


# ── Toggle User Status ────────────────────────────────────────
@router.post("/users/{user_id}/toggle-status")
async def toggle_user_status(user_id: int, request: Request, db: aiosqlite.Connection = Depends(get_db)):
    admin = require_admin(request)
    if not admin:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    async with db.execute("SELECT is_active FROM users WHERE id = ?", (user_id,)) as c:
        user = await c.fetchone()
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=404)

    new_status = 0 if user["is_active"] else 1
    await db.execute("UPDATE users SET is_active = ? WHERE id = ?", (new_status, user_id))
    await db.commit()
    return JSONResponse({"success": True, "is_active": new_status})


# ── Suspend User ──────────────────────────────────────────────
@router.post("/users/{user_id}/suspend")
async def suspend_user(
    user_id: int,
    request: Request,
    reason: str = Form(""),
    db: aiosqlite.Connection = Depends(get_db)
):
    admin = require_admin(request)
    if not admin:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    async with db.execute("SELECT is_suspended FROM users WHERE id = ?", (user_id,)) as c:
        user = await c.fetchone()
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=404)

    new_suspended = 0 if user["is_suspended"] else 1
    await db.execute(
        "UPDATE users SET is_suspended = ?, suspended_reason = ? WHERE id = ?",
        (new_suspended, reason if new_suspended else None, user_id)
    )
    await db.commit()
    return JSONResponse({"success": True, "is_suspended": new_suspended})


# ── Edit User ─────────────────────────────────────────────────
@router.post("/users/{user_id}/edit")
async def edit_user(
    user_id: int,
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(""),
    new_password: str = Form(""),
    db: aiosqlite.Connection = Depends(get_db)
):
    admin = require_admin(request)
    if not admin:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    if new_password:
        pw_hash = hash_password(new_password)
        await db.execute(
            "UPDATE users SET full_name=?, email=?, phone=?, password_hash=? WHERE id=?",
            (full_name, email, phone or None, pw_hash, user_id)
        )
    else:
        await db.execute(
            "UPDATE users SET full_name=?, email=?, phone=? WHERE id=?",
            (full_name, email, phone or None, user_id)
        )
    await db.commit()
    return JSONResponse({"success": True, "message": "تم تحديث بيانات المستخدم بنجاح"})


# ── Delete User ───────────────────────────────────────────────
@router.post("/users/{user_id}/delete")
async def delete_user(user_id: int, request: Request, db: aiosqlite.Connection = Depends(get_db)):
    admin = require_admin(request)
    if not admin:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    await db.commit()
    return JSONResponse({"success": True})


# ── Grant Subscription ────────────────────────────────────────
@router.post("/users/{user_id}/grant-subscription")
async def grant_subscription(user_id: int, request: Request, db: aiosqlite.Connection = Depends(get_db)):
    admin = require_admin(request)
    if not admin:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    start = datetime.utcnow()
    end = start + timedelta(days=365)

    async with db.execute("SELECT id FROM subscriptions WHERE user_id = ?", (user_id,)) as c:
        existing = await c.fetchone()

    if existing:
        await db.execute("""
            UPDATE subscriptions
            SET status='active', plan='annual', subscription_start=?, subscription_end=?, updated_at=datetime('now')
            WHERE user_id = ?
        """, (start.isoformat(), end.isoformat(), user_id))
    else:
        await db.execute("""
            INSERT INTO subscriptions (user_id, status, plan, subscription_start, subscription_end)
            VALUES (?, 'active', 'annual', ?, ?)
        """, (user_id, start.isoformat(), end.isoformat()))

    await db.commit()
    return JSONResponse({"success": True, "message": "تم منح اشتراك سنة كاملة"})


# ── Reset Subscription ────────────────────────────────────────
@router.post("/users/{user_id}/reset-subscription")
async def reset_subscription(user_id: int, request: Request, db: aiosqlite.Connection = Depends(get_db)):
    admin = require_admin(request)
    if not admin:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    await db.execute(
        "UPDATE subscriptions SET status='expired', updated_at=datetime('now') WHERE user_id = ?",
        (user_id,)
    )
    await db.commit()
    return JSONResponse({"success": True, "message": "تم إيقاف الاشتراك"})


# ── Bots List ─────────────────────────────────────────────────
@router.get("/bots", response_class=HTMLResponse)
async def admin_bots(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    admin = require_admin(request)
    if not admin:
        return RedirectResponse("/admin/login", status_code=302)

    async with db.execute("""
        SELECT b.*, u.full_name as owner_name, u.email as owner_email,
               (SELECT COUNT(*) FROM conversations WHERE bot_id = b.id) as conv_count
        FROM bots b JOIN users u ON b.user_id = u.id
        ORDER BY b.created_at DESC
    """) as cursor:
        bots = [dict(r) for r in await cursor.fetchall()]

    async with db.execute("SELECT COUNT(*) as cnt FROM payment_requests WHERE status = 'pending'") as c:
        pending_payments = (await c.fetchone())["cnt"]

    return templates.TemplateResponse(
        request=request,
        name="admin/bots.html",
        context={
            "request": request,
            "admin": admin,
            "bots": bots,
            "stats": {"pending_payments": pending_payments},
            "page": "bots"
        }
    )


# ── Admin Toggle Bot ──────────────────────────────────────────
@router.post("/bots/{bot_id}/toggle")
async def admin_toggle_bot(bot_id: int, request: Request, db: aiosqlite.Connection = Depends(get_db)):
    admin = require_admin(request)
    if not admin:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    async with db.execute("SELECT is_active FROM bots WHERE id = ?", (bot_id,)) as c:
        bot = await c.fetchone()
    if not bot:
        return JSONResponse({"error": "Not found"}, status_code=404)

    new_status = 0 if bot["is_active"] else 1
    await db.execute("UPDATE bots SET is_active = ?, updated_at = datetime('now') WHERE id = ?", (new_status, bot_id))
    await db.commit()
    return JSONResponse({"success": True, "is_active": new_status})


# ── Admin Delete Bot ──────────────────────────────────────────
@router.post("/bots/{bot_id}/delete")
async def admin_delete_bot(bot_id: int, request: Request, db: aiosqlite.Connection = Depends(get_db)):
    admin = require_admin(request)
    if not admin:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    await db.execute("DELETE FROM bots WHERE id = ?", (bot_id,))
    await db.commit()
    return JSONResponse({"success": True})


# ── Subscriptions ─────────────────────────────────────────────
@router.get("/subscriptions", response_class=HTMLResponse)
async def admin_subscriptions(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    admin = require_admin(request)
    if not admin:
        return RedirectResponse("/admin/login", status_code=302)

    async with db.execute("""
        SELECT s.*, u.full_name, u.email
        FROM subscriptions s JOIN users u ON s.user_id = u.id
        ORDER BY s.updated_at DESC
    """) as cursor:
        subscriptions = [dict(r) for r in await cursor.fetchall()]

    async with db.execute("SELECT COUNT(*) as cnt FROM payment_requests WHERE status = 'pending'") as c:
        pending_payments = (await c.fetchone())["cnt"]

    return templates.TemplateResponse(
        request=request,
        name="admin/subscriptions.html",
        context={
            "request": request,
            "admin": admin,
            "subscriptions": subscriptions,
            "stats": {"pending_payments": pending_payments},
            "page": "subscriptions"
        }
    )


# ── Payments List ─────────────────────────────────────────────
@router.get("/payments", response_class=HTMLResponse)
async def admin_payments(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    admin = require_admin(request)
    if not admin:
        return RedirectResponse("/admin/login", status_code=302)

    async with db.execute("""
        SELECT pr.*, u.full_name, u.email, u.phone, u.created_at as user_created_at,
               s.status as sub_status, s.subscription_end
        FROM payment_requests pr
        JOIN users u ON pr.user_id = u.id
        LEFT JOIN subscriptions s ON s.user_id = u.id
        ORDER BY pr.created_at DESC
    """) as cursor:
        payments = [dict(r) for r in await cursor.fetchall()]

    async with db.execute("SELECT COUNT(*) as cnt FROM payment_requests WHERE status = 'pending'") as c:
        pending_payments = (await c.fetchone())["cnt"]
    async with db.execute("SELECT COUNT(*) as cnt FROM payment_requests WHERE status = 'approved'") as c:
        approved_payments = (await c.fetchone())["cnt"]
    async with db.execute("SELECT COUNT(*) as cnt FROM payment_requests WHERE status = 'rejected'") as c:
        rejected_payments = (await c.fetchone())["cnt"]
    async with db.execute("SELECT COALESCE(SUM(amount), 0) as total FROM payment_requests WHERE status = 'approved'") as c:
        total_revenue = (await c.fetchone())["total"]

    return templates.TemplateResponse(
        request=request,
        name="admin/payments.html",
        context={
            "request": request,
            "admin": admin,
            "payments": payments,
            "stats": {
                "pending_payments": pending_payments,
                "approved_payments": approved_payments,
                "rejected_payments": rejected_payments,
                "total_revenue": total_revenue,
            },
            "page": "payments"
        }
    )


# ── Payment Details API ───────────────────────────────────────
@router.get("/payments/{payment_id}/details")
async def admin_payment_details(payment_id: int, request: Request, db: aiosqlite.Connection = Depends(get_db)):
    admin = require_admin(request)
    if not admin:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    async with db.execute("""
        SELECT pr.*, u.full_name, u.email, u.phone, u.created_at as user_created_at,
               s.status as sub_status, s.subscription_end,
               (SELECT COUNT(*) FROM bots WHERE user_id = u.id) as bots_count
        FROM payment_requests pr
        JOIN users u ON pr.user_id = u.id
        LEFT JOIN subscriptions s ON s.user_id = u.id
        WHERE pr.id = ?
    """, (payment_id,)) as c:
        row = await c.fetchone()
    if not row:
        return JSONResponse({"error": "Payment request not found"}, status_code=404)

    return JSONResponse({"success": True, "payment": dict(row)})


# ── Approve Payment ───────────────────────────────────────────
@router.post("/payments/{payment_id}/approve")
async def approve_payment(
    payment_id: int,
    request: Request,
    admin_notes: str = Form(""),
    db: aiosqlite.Connection = Depends(get_db)
):
    admin = require_admin(request)
    if not admin:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    async with db.execute("SELECT * FROM payment_requests WHERE id = ?", (payment_id,)) as c:
        payment = await c.fetchone()
    if not payment:
        return JSONResponse({"error": "Not found"}, status_code=404)

    await db.execute("""
        UPDATE payment_requests
        SET status='approved', admin_notes=?, reviewed_by=?, reviewed_at=datetime('now')
        WHERE id = ?
    """, (admin_notes, admin["sub"], payment_id))

    # Grant subscription for 1 year
    start = datetime.utcnow()
    end = start + timedelta(days=365)
    user_id = payment["user_id"]

    async with db.execute("SELECT id FROM subscriptions WHERE user_id = ?", (user_id,)) as c:
        existing = await c.fetchone()
    if existing:
        await db.execute("""
            UPDATE subscriptions
            SET status='active', plan='annual', subscription_start=?, subscription_end=?, updated_at=datetime('now')
            WHERE user_id = ?
        """, (start.isoformat(), end.isoformat(), user_id))
    else:
        await db.execute("""
            INSERT INTO subscriptions (user_id, status, plan, subscription_start, subscription_end)
            VALUES (?, 'active', 'annual', ?, ?)
        """, (user_id, start.isoformat(), end.isoformat()))

    await db.commit()
    return JSONResponse({"success": True})


# ── Reject Payment ────────────────────────────────────────────
@router.post("/payments/{payment_id}/reject")
async def reject_payment(
    payment_id: int,
    request: Request,
    admin_notes: str = Form(""),
    db: aiosqlite.Connection = Depends(get_db)
):
    admin = require_admin(request)
    if not admin:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    await db.execute("""
        UPDATE payment_requests
        SET status='rejected', admin_notes=?, reviewed_by=?, reviewed_at=datetime('now')
        WHERE id = ?
    """, (admin_notes, admin["sub"], payment_id))
    await db.commit()
    return JSONResponse({"success": True})


# ── Platform Maintenance Toggle ────────────────────────────────
@router.post("/platform/maintenance")
async def toggle_maintenance(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    admin = require_admin(request)
    if not admin:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    async with db.execute("SELECT value FROM platform_settings WHERE key = 'maintenance_mode'") as c:
        row = await c.fetchone()
    current = row["value"] if row else "0"
    new_val = "0" if current == "1" else "1"
    await db.execute(
        "INSERT OR REPLACE INTO platform_settings (key, value, updated_at) VALUES ('maintenance_mode', ?, datetime('now'))",
        (new_val,)
    )
    await db.commit()
    return JSONResponse({"success": True, "maintenance_mode": new_val == "1"})
