```python
"""
User Auth Router - Register, Login, Logout, Email OTP Verification & Password Reset
"""

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from datetime import datetime, timedelta
import aiosqlite
import secrets
import httpx

from app.core.database import get_db
from app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
    decode_token
)
from app.core.config import settings
from app.services.email_service import (
    generate_otp,
    send_verification_email
)


# =========================================================
# Google OAuth Constants
# =========================================================

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


router = APIRouter()

templates = Jinja2Templates(directory="app/templates")


# =========================================================
# Helper Functions
# =========================================================

async def store_verification_code(
    db: aiosqlite.Connection,
    email: str,
    code_type: str
) -> str:
    """Generate and store an OTP code in database with expiration."""

    code = generate_otp(6)

    expire_time = (
        datetime.utcnow()
        + timedelta(minutes=settings.OTP_EXPIRE_MINUTES)
    )

    # Invalidate previous unused codes of same type
    await db.execute(
        """
        UPDATE verification_codes
        SET is_used = 1
        WHERE email = ?
        AND code_type = ?
        AND is_used = 0
        """,
        (email, code_type)
    )

    await db.execute(
        """
        INSERT INTO verification_codes
        (email, code, code_type, expires_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            email,
            code,
            code_type,
            expire_time.isoformat()
        )
    )

    await db.commit()

    return code


async def verify_otp_code(
    db: aiosqlite.Connection,
    email: str,
    code: str,
    code_type: str
) -> bool:
    """Verify if OTP is valid, unused, and not expired."""

    now_iso = datetime.utcnow().isoformat()

    clean_code = code.strip().replace(" ", "")

    async with db.execute(
        """
        SELECT id, expires_at
        FROM verification_codes
        WHERE email = ?
        AND code = ?
        AND code_type = ?
        AND is_used = 0
        ORDER BY id DESC
        LIMIT 1
        """,
        (
            email,
            clean_code,
            code_type
        )
    ) as cursor:

        row = await cursor.fetchone()

    if not row:
        return False

    if row["expires_at"] < now_iso:
        return False

    # Mark code as used
    await db.execute(
        """
        UPDATE verification_codes
        SET is_used = 1
        WHERE id = ?
        """,
        (row["id"],)
    )

    await db.commit()

    return True


# =========================================================
# Login Page
# =========================================================

@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    lang: str = "ar",
    reset: str = ""
):

    token = request.cookies.get("access_token")

    if token:
        payload = decode_token(token)

        if payload and payload.get("type") == "user":
            return RedirectResponse(
                url="/dashboard",
                status_code=302
            )

    return templates.TemplateResponse(
        request=request,
        name="user/login.html",
        context={
            "lang": lang,
            "reset_success": (reset == "success")
        }
    )


# =========================================================
# Login
# =========================================================

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
        """
        SELECT *
        FROM users
        WHERE email = ?
        AND is_active = 1
        """,
        (email,)
    ) as cursor:

        user = await cursor.fetchone()

    # -----------------------------------------------------
    # Invalid Login
    # -----------------------------------------------------

    if not user or not verify_password(
        password,
        user["password_hash"]
    ):

        err_msg = (
            "البريد الإلكتروني أو كلمة المرور غير صحيحة"
            if is_ar
            else
            "Invalid email or password"
        )

        return templates.TemplateResponse(
            request=request,
            name="user/login.html",
            context={
                "error": err_msg,
                "lang": lang
            },
            status_code=400
        )

    # -----------------------------------------------------
    # Suspended Account
    # -----------------------------------------------------

    if user["is_suspended"]:

        reason = (
            user["suspended_reason"]
            or (
                "يرجى مراجعة إدارة المنصة"
                if is_ar
                else
                "Please contact administration"
            )
        )

        err_msg = (
            f"تم تعليق حسابك مؤقتاً: {reason}"
            if is_ar
            else
            f"Your account has been suspended: {reason}"
        )

        return templates.TemplateResponse(
            request=request,
            name="user/login.html",
            context={
                "error": err_msg,
                "lang": lang
            },
            status_code=403
        )

    # -----------------------------------------------------
    # Email Verification
    # -----------------------------------------------------

    if not user["is_verified"]:

        code = await store_verification_code(
            db,
            email,
            "register"
        )

        await send_verification_email(
            email,
            code,
            "register",
            lang
        )

        return RedirectResponse(
            url=f"/verify-email?email={email}&lang={lang}&unverified=1",
            status_code=302
        )

    # -----------------------------------------------------
    # Update Last Login
    # -----------------------------------------------------

    await db.execute(
        """
        UPDATE users
        SET last_login = datetime('now')
        WHERE id = ?
        """,
        (user["id"],)
    )

    await db.commit()

    # -----------------------------------------------------
    # Create Access Token
    # -----------------------------------------------------

    token = create_access_token(
        data={
            "sub": str(user["id"]),
            "type": "user",
            "email": user["email"]
        },
        expires_delta=timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )
    )

    response = RedirectResponse(
        url="/dashboard",
        status_code=302
    )

    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        samesite="lax"
    )

    return response


# =========================================================
# Register Page
# =========================================================

@router.get("/register", response_class=HTMLResponse)
async def register_page(
    request: Request,
    lang: str = "ar"
):

    token = request.cookies.get("access_token")

    if token:

        payload = decode_token(token)

        if payload and payload.get("type") == "user":
            return RedirectResponse(
                url="/dashboard",
                status_code=302
            )

    return templates.TemplateResponse(
        request=request,
        name="user/register.html",
        context={
            "lang": lang
        }
    )


# =========================================================
# Register
# =========================================================

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

    # -----------------------------------------------------
    # Password Confirmation
    # -----------------------------------------------------

    if password != password_confirm:

        err_msg = (
            "كلمتا المرور غير متطابقتين"
            if is_ar
            else
            "Passwords do not match"
        )

        return templates.TemplateResponse(
            request=request,
            name="user/register.html",
            context={
                "error": err_msg,
                "lang": lang
            },
            status_code=400
        )

    # -----------------------------------------------------
    # Password Length
    # -----------------------------------------------------

    if len(password) < 8:

        err_msg = (
            "كلمة المرور يجب أن تكون 8 أحرف على الأقل"
            if is_ar
            else
            "Password must be at least 8 characters"
        )

        return templates.TemplateResponse(
            request=request,
            name="user/register.html",
            context={
                "error": err_msg,
                "lang": lang
            },
            status_code=400
        )

    # -----------------------------------------------------
    # Check Existing Email
    # -----------------------------------------------------

    async with db.execute(
        """
        SELECT id, is_verified
        FROM users
        WHERE email = ?
        """,
        (email,)
    ) as cursor:

        existing = await cursor.fetchone()

    password_hash = hash_password(password)

    # -----------------------------------------------------
    # Existing User
    # -----------------------------------------------------

    if existing:

        if existing["is_verified"]:

            err_msg = (
                "هذا البريد الإلكتروني مسجل مسبقاً"
                if is_ar
                else
                "This email is already registered"
            )

            return templates.TemplateResponse(
                request=request,
                name="user/register.html",
                context={
                    "error": err_msg,
                    "lang": lang
                },
                status_code=400
            )

        # User started registration earlier but
        # did not verify email
        else:

            await db.execute(
                """
                UPDATE users
                SET full_name = ?,
                    password_hash = ?
                WHERE id = ?
                """,
                (
                    full_name.strip(),
                    password_hash,
                    existing["id"]
                )
            )

            await db.commit()

    # -----------------------------------------------------
    # New User
    # -----------------------------------------------------

    else:

        await db.execute(
            """
            INSERT INTO users
            (
                email,
                password_hash,
                full_name,
                is_verified
            )
            VALUES (?, ?, ?, 0)
            """,
            (
                email,
                password_hash,
                full_name.strip()
            )
        )

        await db.commit()

    # -----------------------------------------------------
    # Send OTP
    # -----------------------------------------------------

    code = await store_verification_code(
        db,
        email,
        "register"
    )

    await send_verification_email(
        email,
        code,
        "register",
        lang
    )

    return RedirectResponse(
        url=f"/verify-email?email={email}&lang={lang}",
        status_code=302
    )


# =========================================================
# Email Verification Page
# =========================================================

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

        return RedirectResponse(
            url="/register",
            status_code=302
        )

    return templates.TemplateResponse(
        request=request,
        name="user/verify_email.html",
        context={
            "email": email,
            "lang": lang,
            "is_unverified_notice": (unverified == "1"),
            "msg": msg,
            "error": error
        }
    )


# =========================================================
# Email Verification Submit
# =========================================================

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

    is_valid = await verify_otp_code(
        db,
        email,
        code,
        "register"
    )

    if not is_valid:

        err_msg = (
            "رمز التحقق غير صحيح أو منتهي الصلاحية"
            if is_ar
            else
            "Invalid or expired verification code"
        )

        return templates.TemplateResponse(
            request=request,
            name="user/verify_email.html",
            context={
                "email": email,
                "lang": lang,
                "error": err_msg
            },
            status_code=400
        )

    # -----------------------------------------------------
    # Get User
    # -----------------------------------------------------

    async with db.execute(
        """
        SELECT id
        FROM users
        WHERE email = ?
        """,
        (email,)
    ) as cursor:

        user = await cursor.fetchone()

    if not user:

        return RedirectResponse(
            url="/register",
            status_code=302
        )

    user_id = user["id"]

    # -----------------------------------------------------
    # Mark Verified
    # -----------------------------------------------------

    await db.execute(
        """
        UPDATE users
        SET is_verified = 1
        WHERE id = ?
        """,
        (user_id,)
    )

    # -----------------------------------------------------
    # Create Trial Subscription
    # -----------------------------------------------------

    async with db.execute(
        """
        SELECT id
        FROM subscriptions
        WHERE user_id = ?
        """,
        (user_id,)
    ) as cursor:

        sub_exists = await cursor.fetchone()

    if not sub_exists:

        trial_start = datetime.utcnow()

        trial_end = (
            trial_start
            + timedelta(days=settings.TRIAL_DAYS)
        )

        await db.execute(
            """
            INSERT INTO subscriptions
            (
                user_id,
                plan,
                status,
                trial_start,
                trial_end
            )
            VALUES (?, 'trial', 'trial', ?, ?)
            """,
            (
                user_id,
                trial_start.isoformat(),
                trial_end.isoformat()
            )
        )

    await db.commit()

    # -----------------------------------------------------
    # Login User
    # -----------------------------------------------------

    token = create_access_token(
        data={
            "sub": str(user_id),
            "type": "user",
            "email": email
        },
        expires_delta=timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )
    )

    response = RedirectResponse(
        url="/dashboard",
        status_code=302
    )

    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        samesite="lax"
    )

    return response


# =========================================================
# Resend Verification Code
# =========================================================

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

    code = await store_verification_code(
        db,
        email,
        code_type
    )

    sent = await send_verification_email(
        email,
        code,
        code_type,
        lang
    )

    success_msg = (
        "تم إرسال رمز جديد إلى بريدك الإلكتروني"
        if is_ar
        else
        "A new code has been sent to your email"
    )

    # AJAX / Fetch request
    if "application/json" in request.headers.get(
        "accept",
        ""
    ):

        return JSONResponse(
            {
                "success": sent,
                "message": success_msg
            }
        )

    if code_type == "register":

        target_url = (
            f"/verify-email"
            f"?email={email}"
            f"&lang={lang}"
            f"&msg={success_msg}"
        )

    else:

        target_url = (
            f"/reset-password"
            f"?email={email}"
            f"&lang={lang}"
            f"&msg={success_msg}"
        )

    return RedirectResponse(
        url=target_url,
        status_code=302
    )


# =========================================================
# Forgot Password Page
# =========================================================

@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(
    request: Request,
    lang: str = "ar",
    error: str = ""
):

    return templates.TemplateResponse(
        request=request,
        name="user/forgot_password.html",
        context={
            "lang": lang,
            "error": error
        }
    )


# =========================================================
# Forgot Password Submit
# =========================================================

@router.post("/forgot-password")
async def forgot_password_submit(
    request: Request,
    email: str = Form(...),
    lang: str = Form("ar"),
    db: aiosqlite.Connection = Depends(get_db)
):

    email = email.lower().strip()

    is_ar = (lang != "en")

    async with db.execute(
        """
        SELECT id
        FROM users
        WHERE email = ?
        AND is_active = 1
        """,
        (email,)
    ) as cursor:

        user = await cursor.fetchone()

    if user:

        code = await store_verification_code(
            db,
            email,
            "reset_password"
        )

        await send_verification_email(
            email,
            code,
            "reset_password",
            lang
        )

    msg = (
        "إذا كان هذا البريد مسجلاً لدينا، فقد أرسلنا رمز التحقق إليه."
        if is_ar
        else
        "If this email is registered, we have sent a verification code to it."
    )

    return RedirectResponse(
        url=(
            f"/reset-password"
            f"?email={email}"
            f"&lang={lang}"
            f"&msg={msg}"
        ),
        status_code=302
    )


# =========================================================
# Reset Password Page
# =========================================================

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
            "email": email,
            "lang": lang,
            "msg": msg,
            "error": error
        }
    )


# =========================================================
# Reset Password Submit
# =========================================================

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

    # -----------------------------------------------------
    # Password Confirmation
    # -----------------------------------------------------

    if password != password_confirm:

        err_msg = (
            "كلمتا المرور غير متطابقتين"
            if is_ar
            else
            "Passwords do not match"
        )

        return templates.TemplateResponse(
            request=request,
            name="user/reset_password.html",
            context={
                "email": email,
                "lang": lang,
                "error": err_msg
            },
            status_code=400
        )

    # -----------------------------------------------------
    # Password Length
    # -----------------------------------------------------

    if len(password) < 8:

        err_msg = (
            "كلمة المرور يجب أن تكون 8 أحرف على الأقل"
            if is_ar
            else
            "Password must be at least 8 characters"
        )

        return templates.TemplateResponse(
            request=request,
            name="user/reset_password.html",
            context={
                "email": email,
                "lang": lang,
                "error": err_msg
            },
            status_code=400
        )

    # -----------------------------------------------------
    # Verify OTP
    # -----------------------------------------------------

    is_valid = await verify_otp_code(
        db,
        email,
        code,
        "reset_password"
    )

    if not is_valid:

        err_msg = (
            "رمز التحقق غير صحيح أو منتهي الصلاحية"
            if is_ar
            else
            "Invalid or expired verification code"
        )

        return templates.TemplateResponse(
            request=request,
            name="user/reset_password.html",
            context={
                "email": email,
                "lang": lang,
                "error": err_msg
            },
            status_code=400
        )

    # -----------------------------------------------------
    # Update Password
    # -----------------------------------------------------

    password_hash = hash_password(password)

    await db.execute(
        """
        UPDATE users
        SET password_hash = ?,
            is_verified = 1
        WHERE email = ?
        """,
        (
            password_hash,
            email
        )
    )

    await db.commit()

    return RedirectResponse(
        url=f"/login?reset=success&lang={lang}",
        status_code=302
    )


# =========================================================
# Google OAuth Login
# =========================================================

@router.get("/auth/google")
async def google_login(
    request: Request,
    lang: str = "ar"
):

    if not settings.GOOGLE_CLIENT_ID:

        return RedirectResponse(
            url=f"/login?lang={lang}&error=google_not_configured",
            status_code=302
        )

    state = secrets.token_urlsafe(16)

    redirect_uri = (
        f"{settings.APP_URL}"
        f"/auth/google/callback"
    )

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

    response = RedirectResponse(
        url=google_url,
        status_code=302
    )

    # Store state + language for CSRF verification
    response.set_cookie(
        key="oauth_state",
        value=state,
        max_age=600,
        httponly=True,
        samesite="lax"
    )

    response.set_cookie(
        key="oauth_lang",
        value=lang,
        max_age=600,
        httponly=True,
        samesite="lax"
    )

    return response


# =========================================================
# Google OAuth Callback
# =========================================================

@router.get("/auth/google/callback")
async def google_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    db: aiosqlite.Connection = Depends(get_db)
):

    lang = request.cookies.get(
        "oauth_lang",
        "ar"
    )

    stored_state = request.cookies.get(
        "oauth_state",
        ""
    )

    # -----------------------------------------------------
    # CSRF Check
    # -----------------------------------------------------

    if not state or state != stored_state:

        return RedirectResponse(
            url=f"/login?lang={lang}",
            status_code=302
        )

    if error or not code:

        return RedirectResponse(
            url=f"/login?lang={lang}",
            status_code=302
        )

    if (
        not settings.GOOGLE_CLIENT_ID
        or not settings.GOOGLE_CLIENT_SECRET
    ):

        return RedirectResponse(
            url=f"/login?lang={lang}",
            status_code=302
        )

    redirect_uri = (
        f"{settings.APP_URL}"
        f"/auth/google/callback"
    )

    # -----------------------------------------------------
    # Exchange Authorization Code
    # -----------------------------------------------------

    async with httpx.AsyncClient() as client:

        token_resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code"
            }
        )

        if token_resp.status_code != 200:

            return RedirectResponse(
                url=f"/login?lang={lang}",
                status_code=302
            )

        token_data = token_resp.json()

        access_token_google = token_data.get(
            "access_token"
        )

        if not access_token_google:

            return RedirectResponse(
                url=f"/login?lang={lang}",
                status_code=302
            )

        # -------------------------------------------------
        # Get Google User Info
        # -------------------------------------------------

        userinfo_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={
                "Authorization":
                    f"Bearer {access_token_google}"
            }
        )

        if userinfo_resp.status_code != 200:

            return RedirectResponse(
                url=f"/login?lang={lang}",
                status_code=302
            )

        userinfo = userinfo_resp.json()

    google_email = (
        userinfo.get("email") or ""
    ).lower().strip()

    google_name = (
        userinfo.get("name")
        or google_email.split("@")[0]
    )

    google_id = userinfo.get("sub") or ""

    if not google_email:

        return RedirectResponse(
            url=f"/login?lang={lang}",
            status_code=302
        )

    # -----------------------------------------------------
    # Find Existing User
    # -----------------------------------------------------

    async with db.execute(
        """
        SELECT *
        FROM users
        WHERE email = ?
        """,
        (google_email,)
    ) as cursor:

        existing = await cursor.fetchone()

    # -----------------------------------------------------
    # Existing User
    # -----------------------------------------------------

    if existing:

        user_id = existing["id"]

        if existing["is_suspended"]:

            return RedirectResponse(
                url=f"/login?lang={lang}",
                status_code=302
            )

        if not existing["is_verified"]:

            await db.execute(
                """
                UPDATE users
                SET is_verified = 1
                WHERE id = ?
                """,
                (user_id,)
            )

        await db.execute(
            """
            UPDATE users
            SET last_login = datetime('now')
            WHERE id = ?
            """,
            (user_id,)
        )

        await db.commit()

    # -----------------------------------------------------
    # New Google User
    # -----------------------------------------------------

    else:

        cursor = await db.execute(
            """
            INSERT INTO users
            (
                email,
                password_hash,
                full_name,
                is_verified,
                is_active
            )
            VALUES (?, '', ?, 1, 1)
            """,
            (
                google_email,
                google_name
            )
        )

        user_id = cursor.lastrowid

        # Create trial subscription
        trial_start = datetime.utcnow()

        trial_end = (
            trial_start
            + timedelta(days=settings.TRIAL_DAYS)
        )

        await db.execute(
            """
            INSERT INTO subscriptions
            (
                user_id,
                plan,
                status,
                trial_start,
                trial_end
            )
            VALUES (?, 'trial', 'trial', ?, ?)
            """,
            (
                user_id,
                trial_start.isoformat(),
                trial_end.isoformat()
            )
        )

        await db.commit()

    # -----------------------------------------------------
    # Create JWT Session
    # -----------------------------------------------------

    token = create_access_token(
        data={
            "sub": str(user_id),
            "type": "user",
            "email": google_email
        },
        expires_delta=timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )
    )

    response = RedirectResponse(
        url="/dashboard",
        status_code=302
    )

    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        samesite="lax"
    )

    # Clear OAuth cookies
    response.delete_cookie("oauth_state")
    response.delete_cookie("oauth_lang")

    return response


# =========================================================
# Logout
# =========================================================

@router.get("/logout")
async def logout():

    response = RedirectResponse(
        url="/login",
        status_code=302
    )

    response.delete_cookie("access_token")

    return response
```
