"""
Admin Auth Router - SmartBot
"""
from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from datetime import timedelta
import aiosqlite

from app.core.database import get_db
from app.core.security import verify_password, create_access_token
from app.core.config import settings

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/login", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    # If already logged in, redirect to dashboard
    token = request.cookies.get("admin_token")
    if token:
        from app.core.security import decode_token
        payload = decode_token(token)
        if payload and payload.get("type") == "admin":
            return RedirectResponse("/admin/dashboard", status_code=302)
    return templates.TemplateResponse("admin/login.html", {"request": request})


@router.post("/login")
async def admin_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: aiosqlite.Connection = Depends(get_db)
):
    async with db.execute(
        "SELECT * FROM admins WHERE username = ? AND is_active = 1", (username,)
    ) as cursor:
        admin = await cursor.fetchone()

    if not admin or not verify_password(password, admin["password_hash"]):
        return templates.TemplateResponse("admin/login.html", {
            "request": request,
            "error": "اسم المستخدم أو كلمة المرور غير صحيحة"
        }, status_code=400)

    # Update last login
    await db.execute("UPDATE admins SET last_login = datetime('now') WHERE id = ?", (admin["id"],))
    await db.commit()

    token = create_access_token(
        data={"sub": str(admin["id"]), "type": "admin", "username": admin["username"]},
        expires_delta=timedelta(minutes=settings.ADMIN_TOKEN_EXPIRE_MINUTES)
    )

    response = RedirectResponse("/admin/dashboard", status_code=302)
    response.set_cookie(
        key="admin_token",
        value=token,
        httponly=True,
        max_age=settings.ADMIN_TOKEN_EXPIRE_MINUTES * 60,
        samesite="lax"
    )
    # Also set access_token for shared middleware
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        max_age=settings.ADMIN_TOKEN_EXPIRE_MINUTES * 60,
        samesite="lax"
    )
    return response


@router.get("/logout")
async def admin_logout():
    response = RedirectResponse("/admin/login", status_code=302)
    response.delete_cookie("admin_token")
    response.delete_cookie("access_token")
    return response
