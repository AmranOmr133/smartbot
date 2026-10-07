"""
User Auth Router - Register, Login, Logout, Email OTP Verification & Password Reset
"""
from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from datetime import datetime, timedelta
import aiosqlite
import secrets
import httpx

from app.core.database import get_db
from app.core.security import hash_password, verify_password, create_access_token, decode_token
from app.core.config import settings
from app.core.templates import templates
from app.services.email_service import generate_otp, send_verification_email

# ─── Google OAuth Constants ───────────────────────────────────
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

router = APIRouter()


# ─── Helper Functions ─────────────────────────────────────────

async def store_verification_code(db: aiosqlite.Connection, email: str, code_type: str) -> str:
    """Generate and store an OTP code in database with expiration."""
    code = generate_otp(6)
    expire_time = datetime.utcnow() + timedelta(minutes=settings.OTP_EXPIRE_MINUTES)
    
    # Invalidate previous unused codes of same type
    await db.execute(
        "UPDATE verification_codes SET is_used = 1 WHERE email = ? AND code_type = ? AND is_used = 0",
        (email, code_type)
    )
    
    await db.execute("""
        INSERT INTO verification_codes (email, code, code_type, expires_at)
        VALUES (?, ?, ?, ?)
    """, (email, code, code_type, expire_time.isoformat()))
    await db.commit()
    return code


async def verify_otp_code(db: aiosqlite.Connection, email: str, code: str, code_type: str) -> bool:
    """Verify if OTP is valid, unused, and not expired."""
    now_iso = datetime.utcnow().isoformat()
    clean_code = code.strip().replace(" ", "")
    
    async with db.execute("""
        SELECT id, expires_at FROM verification_codes
        WHERE email = ? AND code = ? AND code_type = ? AND is_used = 0
        ORDER BY id DESC LIMIT 1
    """, (email, clean_code, code_type)) as c:
        row = await c.fetchone()
    
    if not row:
        return False
    
    if row["expires_at"] < now_iso:
        return False
    
    # Mark as used
    await db.execute("UPDATE verification_codes SET is_used = 1 WHERE id = ?", (row["id"],))
    await db.commit()
    return True


# ─── Login & Register ──────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, lang: str = "ar", reset: str = ""):
    token = request.cookies.get("access_token")
    if token:
        payload = decode_token(token)
        if payload and payload.get("type") == "user":
            return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse(
        request=request,
        name="user/login.html",
        context={
            "request": request,
            "lang": lang,
            "reset_success": (reset == "success")
        }
    )


@router.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    lang: str = Form("ar"),
    db: aiosqlite.Connection = Depends(get_db)
):
    email = email.lower().strip()
    is_ar = (lang != "en")

    async with db.execute(
        "SELECT * FROM users WHERE email = ? AND is_active = 1", (email,)
    ) as cursor:
        user = await cursor.fetchone()

    if not user or not verify_password(password, user["password_hash"]):
        err_msg = "البريد الإلكتروني أو كلمة المرور غير صحيحة" if is_ar else "Invalid email or password"
        return templates.TemplateResponse(
            request=request,
            name="user/login.html",
            context={
                "request": request,
                "error": err_msg,
                "lang": lang
            },
            status_code=400
        )

    # Check if user is suspended
    if user["is_suspended"]:
        reason = user["suspended_reason"] or ("يرجى مراجعة إدارة المنصة" if is_ar else "Please contact administration")
        err_msg = f"تم تعليق حسابك مؤقتاً: {reason}" if is_ar else f"Your account has been suspended: {reason}"
        return templates.TemplateResponse(
            request=request,
            name="user/login.html",
            context={
                "request": request,
                "error": err_msg,
                "lang": lang
            },
            status_code=403
        )

    # Check if user verified their email
    if not user["is_verified"]:
        # Resend verification code and redirect to verify page
        code = await store_verification_code(db, email, "register")
        await send_verification_email(email, code, "register", lang)
        return RedirectResponse(f"/verify-email?email={email}&lang={lang}&unverified=1", status_code=302)

    await db.execute("UPDATE users SET last_login = datetime('now') WHERE id = ?", (user["id"],))
    await db.commit()

    token = create_access_token(
        data={"sub": str(user["id"]), "type": "user", "email": user["email"]},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )

    response = RedirectResponse("/dashboard", status_code=302)
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        samesite="lax"
    )
    return response


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, lang: str = "ar"):
    token = request.cookies.get("access_token")
    if token:
        payload = decode_token(token)
        if payload and payload.get("type") == "user":
            return RedirectResponse("/dashboard", status_code=302)
    return templates.TemplateResponse(
        request=request,
        name="user/register.html",
        context={
            "request": request,
            "lang": lang
        }
    )


@router.post("/register")
async def register(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    lang: str = Form("ar"),
    db: aiosqlite.Connection = Depends(get_db)
):
    email = email.lower().strip()
    is_ar = (lang != "en")

    if password != password_confirm:
        err_msg = "كلمتا المرور غير متطابقتين" if is_ar else "Passwords do not match"
        return templates.TemplateResponse(
            request=request,
            name="user/register.html",
            context={
                "request": request,
                "error": err_msg,
                "lang": lang
            },
            status_code=400
        )

    if len(password) < 8:
        err_msg = "كلمة المرور يجب أن تكون 8 أحرف على الأقل" if is_ar else "Password must be at least 8 characters"
        return templates.TemplateResponse(
            request=request,
            name="user/register.html",
            context={
                "request": request,
                "error": err_msg,
                "lang": lang
            },
            status_code=400
        )

    # Check if email exists
    async with db.execute("SELECT id, is_verified FROM users WHERE email = ?", (email,)) as c:
        existing = await c.fetchone()

    password_hash = hash_password(password)

    if existing:
        if existing["is_verified"]:
            err_msg = "هذا البريد الإلكتروني مسجل مسبقاً" if is_ar else "This email is already registered"
            return templates.TemplateResponse(
                request=request,
                name="user/register.html",
                context={
                    "request": request,
                    "error": err_msg,
                    "lang": lang
                },
                status_code=400
            )
        else:
            # User started registration earlier but did not verify -> update details
            await db.execute("""
                UPDATE users SET full_name = ?, password_hash = ? WHERE id = ?
            """, (full_name.strip(), password_hash, existing["id"]))
            await db.commit()
    else:
        # Create new unverified user
        await db.execute("""
            INSERT INTO users (email, password_hash, full_name, is_verified)
            VALUES (?, ?, ?, 0)
        """, (email, password_hash, full_name.strip()))
        await db.commit()

    # Generate and send 6-digit OTP code
    code = await store_verification_code(db, email, "register")
    await send_verification_email(email, code, "register", lang)

    return RedirectResponse(f"/verify-email?email={email}&lang={lang}", status_code=302)


# ─── Email Verification ────────────────────────────────────────

@router.get("/verify-email", response_class=HTMLResponse)
async def verify_email_page(
    request: Request,
    email: str = "",
    lang: str = "ar",
    unverified: str = "",
    msg: str = "",
    error: str = ""
):
    if not email:
        return RedirectResponse("/register", status_code=302)

    return templates.TemplateResponse(
        request=request,
        name="user/verify_email.html",
        context={
            "request": request,
            "email": email,
            "lang": lang,
            "is_unverified_notice": (unverified == "1"),
            "msg": msg,
            "error": error
        }
    )


@router.post("/verify-email")
async def verify_email_submit(
    request: Request,
    email: str = Form(...),
    code: str = Form(...),
    lang: str = Form("ar"),
    db: aiosqlite.Connection = Depends(get_db)
):
    email = email.lower().strip()
    is_ar = (lang != "en")

    is_valid = await verify_otp_code(db, email, code, "register")
    if not is_valid:
        err_msg = "رمز التحقق غير صحيح أو منتهي الصلاحية" if is_ar else "Invalid or expired verification code"
        return templates.TemplateResponse(
            request=request,
            name="user/verify_email.html",
            context={
                "request": request,
                "email": email,
                "lang": lang,
                "error": err_msg
            },
            status_code=400
        )

    # Get user
    async with db.execute("SELECT id FROM users WHERE email = ?", (email,)) as c:
        user = await c.fetchone()

    if not user:
        return RedirectResponse("/register", status_code=302)

    user_id = user["id"]

    # Mark user as verified
    await db.execute("UPDATE users SET is_verified = 1 WHERE id = ?", (user_id,))

    # Create 10-day trial subscription if doesn't exist yet
    async with db.execute("SELECT id FROM subscriptions WHERE user_id = ?", (user_id,)) as c:
        sub_exists = await c.fetchone()

    if not sub_exists:
        trial_start = datetime.utcnow()
        trial_end = trial_start + timedelta(days=settings.TRIAL_DAYS)
        await db.execute("""
            INSERT INTO subscriptions (user_id, plan, status, trial_start, trial_end)
            VALUES (?, 'trial', 'trial', ?, ?)
        """, (user_id, trial_start.isoformat(), trial_end.isoformat()))

    await db.commit()

    # Log user in
    token = create_access_token(
        data={"sub": str(user_id), "type": "user", "email": email},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )

    response = RedirectResponse("/dashboard", status_code=302)
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        samesite="lax"
    )
    return response


@router.post("/resend-code")
async def resend_code(
    request: Request,
    email: str = Form(...),
    code_type: str = Form("register"),
    lang: str = Form("ar"),
    db: aiosqlite.Connection = Depends(get_db)
):
    email = email.lower().strip()
    is_ar = (lang != "en")

    # Generate and send code
    code = await store_verification_code(db, email, code_type)
    sent = await send_verification_email(email, code, code_type, lang)

    success_msg = "تم إرسال رمز جديد إلى بريدك الإلكتروني" if is_ar else "A new code has been sent to your email"
    
    # If request is AJAX/fetch
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"success": sent, "message": success_msg})

    target_url = f"/verify-email?email={email}&lang={lang}&msg={success_msg}" if code_type == "register" else f"/reset-password?email={email}&lang={lang}&msg={success_msg}"
    return RedirectResponse(target_url, status_code=302)


# ─── Forgot & Reset Password ────────────────────────────────────

@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request, lang: str = "ar", error: str = ""):
    return templates.TemplateResponse(
        request=request,
        name="user/forgot_password.html",
        context={
            "request": request,
            "lang": lang,
            "error": error
        }
    )


@router.post("/forgot-password")
async def forgot_password_submit(
    request: Request,
    email: str = Form(...),
    lang: str = Form("ar"),
    db: aiosqlite.Connection = Depends(get_db)
):
    email = email.lower().strip()
    is_ar = (lang != "en")

    # Check if user exists
    async with db.execute("SELECT id FROM users WHERE email = ? AND is_active = 1", (email,)) as c:
        user = await c.fetchone()

    if user:
        code = await store_verification_code(db, email, "reset_password")
        await send_verification_email(email, code, "reset_password", lang)

    # Redirect to reset password page with email filled
    msg = "إذا كان هذا البريد مسجلاً لدينا، فقد أرسلنا رمز التحقق إليه." if is_ar else "If this email is registered, we have sent a verification code to it."
    return RedirectResponse(f"/reset-password?email={email}&lang={lang}&msg={msg}", status_code=302)


@router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(
    request: Request,
    email: str = "",
    lang: str = "ar",
    msg: str = "",
    error: str = ""
):
    return templates.TemplateResponse(
        request=request,
        name="user/reset_password.html",
        context={
            "request": request,
            "email": email,
            "lang": lang,
            "msg": msg,
            "error": error
        }
    )


@router.post("/reset-password")
async def reset_password_submit(
    request: Request,
    email: str = Form(...),
    code: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    lang: str = Form("ar"),
    db: aiosqlite.Connection = Depends(get_db)
):
    email = email.lower().strip()
    is_ar = (lang != "en")

    if password != password_confirm:
        err_msg = "كلمتا المرور غير متطابقتين" if is_ar else "Passwords do not match"
        return templates.TemplateResponse(
            request=request,
            name="user/reset_password.html",
            context={
                "request": request,
                "email": email,
                "lang": lang,
                "error": err_msg
            },
            status_code=400
        )

    if len(password) < 8:
        err_msg = "كلمة المرور يجب أن تكون 8 أحرف على الأقل" if is_ar else "Password must be at least 8 characters"
        return templates.TemplateResponse(
            request=request,
            name="user/reset_password.html",
            context={
                "request": request,
                "email": email,
                "lang": lang,
                "error": err_msg
            },
            status_code=400
        )

    # Verify code
    is_valid = await verify_otp_code(db, email, code, "reset_password")
    if not is_valid:
        err_msg = "رمز التحقق غير صحيح أو منتهي الصلاحية" if is_ar else "Invalid or expired verification code"
        return templates.TemplateResponse(
            request=request,
            name="user/reset_password.html",
            context={
                "request": request,
                "email": email,
                "lang": lang,
                "error": err_msg
            },
            status_code=400
        )

    # Update password
    password_hash = hash_password(password)
    await db.execute("""
        UPDATE users SET password_hash = ?, is_verified = 1 WHERE email = ?
    """, (password_hash, email))
    await db.commit()

    return RedirectResponse(f"/login?reset=success&lang={lang}", status_code=302)


# ─── Google OAuth 2.0 ────────────────────────────────────────

@router.get("/auth/google")
async def google_login(request: Request, lang: str = "ar"):
    """Redirect user to Google OAuth consent screen."""
    if not settings.GOOGLE_CLIENT_ID:
        return RedirectResponse(f"/login?lang={lang}&error=google_not_configured", status_code=302)

    state = secrets.token_urlsafe(16)
    redirect_uri = f"{settings.APP_URL}/auth/google/callback"

    # Build Google OAuth URL
    params = (
        f"?client_id={settings.GOOGLE_CLIENT_ID}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
        f"&scope=openid%20email%20profile"
        f"&state={state}"
        f"&access_type=offline"
        f"&prompt=select_account"
    )
    google_url = GOOGLE_AUTH_URL + params

    response = RedirectResponse(google_url, status_code=302)
    # Store state + lang in cookie for CSRF check
    response.set_cookie("oauth_state", state, max_age=600, httponly=True, samesite="lax")
    response.set_cookie("oauth_lang", lang, max_age=600, httponly=True, samesite="lax")
    return response


@router.get("/auth/google/callback")
async def google_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    db: aiosqlite.Connection = Depends(get_db)
):
    """Handle Google OAuth callback, create or login user."""
    lang = request.cookies.get("oauth_lang", "ar")
    stored_state = request.cookies.get("oauth_state", "")

    # CSRF check
    if not state or state != stored_state:
        return RedirectResponse(f"/login?lang={lang}", status_code=302)

    if error or not code:
        return RedirectResponse(f"/login?lang={lang}", status_code=302)

    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        return RedirectResponse(f"/login?lang={lang}", status_code=302)

    redirect_uri = f"{settings.APP_URL}/auth/google/callback"

    # Exchange code for token
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        })

        if token_resp.status_code != 200:
            return RedirectResponse(f"/login?lang={lang}", status_code=302)

        token_data = token_resp.json()
        access_token_google = token_data.get("access_token")

        if not access_token_google:
            return RedirectResponse(f"/login?lang={lang}", status_code=302)

        # Fetch user info
        userinfo_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token_google}"}
        )
        if userinfo_resp.status_code != 200:
            return RedirectResponse(f"/login?lang={lang}", status_code=302)

        userinfo = userinfo_resp.json()

    google_email = (userinfo.get("email") or "").lower().strip()
    google_name = userinfo.get("name") or google_email.split("@")[0]
    google_id = userinfo.get("sub") or ""

    if not google_email:
        return RedirectResponse(f"/login?lang={lang}", status_code=302)

    # Look up existing user
    async with db.execute("SELECT * FROM users WHERE email = ?", (google_email,)) as c:
        existing = await c.fetchone()

    if existing:
        user_id = existing["id"]
        # Check suspension
        if existing["is_suspended"]:
            return RedirectResponse(f"/login?lang={lang}", status_code=302)
        # Mark verified if not already
        if not existing["is_verified"]:
            await db.execute("UPDATE users SET is_verified = 1 WHERE id = ?", (user_id,))
        await db.execute("UPDATE users SET last_login = datetime('now') WHERE id = ?", (user_id,))
        await db.commit()
    else:
        # Create new verified user (Google accounts are already verified)
        cursor = await db.execute("""
            INSERT INTO users (email, password_hash, full_name, is_verified, is_active)
            VALUES (?, '', ?, 1, 1)
        """, (google_email, google_name))
        user_id = cursor.lastrowid

        # Create 10-day trial subscription
        trial_start = datetime.utcnow()
        trial_end = trial_start + timedelta(days=settings.TRIAL_DAYS)
        await db.execute("""
            INSERT INTO subscriptions (user_id, plan, status, trial_start, trial_end)
            VALUES (?, 'trial', 'trial', ?, ?)
        """, (user_id, trial_start.isoformat(), trial_end.isoformat()))
        await db.commit()

    # Issue JWT session
    token = create_access_token(
        data={"sub": str(user_id), "type": "user", "email": google_email},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )

    response = RedirectResponse("/dashboard", status_code=302)
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        samesite="lax"
    )
    # Clear oauth cookies
    response.delete_cookie("oauth_state")
    response.delete_cookie("oauth_lang")
    return response


# ─── Logout ───────────────────────────────────────────────────

@router.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("access_token")
    return response
