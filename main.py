"""
SmartBot Platform - Main Application
FastAPI + aiosqlite | Dual auth system (Admin + User)
"""

import sys
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.core.config import settings
from app.core.database import init_db
from app.services.telegram_service import telegram_polling_manager


# ─────────────────────────────────────────────────────────
# UTF-8 Console Encoding
# ─────────────────────────────────────────────────────────

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────
# Routers
# ─────────────────────────────────────────────────────────

from app.routers.admin import auth as admin_auth
from app.routers.admin import dashboard as admin_dashboard

from app.routers.user import auth as user_auth
from app.routers.user import dashboard as user_dashboard
from app.routers.user import bots as user_bots
from app.routers.user import subscription as user_subscription

from app.routers import webhook


# ─────────────────────────────────────────────────────────
# Application Lifespan
# ─────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """App startup/shutdown lifecycle."""

    print(f"[*] Starting {settings.APP_NAME}...")

    # Initialize database
    await init_db()

    # Start Telegram polling service
    telegram_polling_manager.start()

    print(f"[OK] {settings.APP_NAME} is ready!")
    print(f"   -> Admin Panel: {settings.APP_URL}/admin/login")
    print(f"   -> User App:    {settings.APP_URL}/login")

    yield

    # Shutdown
    print(f"[*] {settings.APP_NAME} shutting down...")

    await telegram_polling_manager.stop()


# ─────────────────────────────────────────────────────────
# FastAPI Application
# ─────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.APP_NAME,
    description="AI-powered Telegram Bot Management Platform",
    version="2.0.0",
    docs_url="/api/docs" if settings.DEBUG else None,
    redoc_url=None,
    lifespan=lifespan,
)


# ─────────────────────────────────────────────────────────
# Static Files
# ─────────────────────────────────────────────────────────

static_dir = os.path.join(
    os.path.dirname(__file__),
    "static"
)

if not os.path.exists(static_dir):
    os.makedirs(static_dir, exist_ok=True)

app.mount(
    "/static",
    StaticFiles(directory=static_dir),
    name="static"
)


# ─────────────────────────────────────────────────────────
# Jinja2 Templates
# ─────────────────────────────────────────────────────────

templates = Jinja2Templates(
    directory="app/templates"
)


# ─────────────────────────────────────────────────────────
# Admin Routes
# ─────────────────────────────────────────────────────────

app.include_router(
    admin_auth.router,
    prefix="/admin",
    tags=["Admin Auth"]
)

app.include_router(
    admin_dashboard.router,
    prefix="/admin",
    tags=["Admin Dashboard"]
)


# ─────────────────────────────────────────────────────────
# User Authentication Routes
# ─────────────────────────────────────────────────────────

app.include_router(
    user_auth.router,
    tags=["User Auth"]
)


# ─────────────────────────────────────────────────────────
# User Application Routes
# ─────────────────────────────────────────────────────────

app.include_router(
    user_dashboard.router,
    tags=["User Dashboard"]
)

app.include_router(
    user_bots.router,
    tags=["User Bots"]
)

app.include_router(
    user_subscription.router,
    tags=["User Subscription"]
)


# ─────────────────────────────────────────────────────────
# Webhook Routes
# ─────────────────────────────────────────────────────────

app.include_router(
    webhook.router,
    prefix="/webhook",
    tags=["Webhooks"]
)


# ─────────────────────────────────────────────────────────
# Maintenance Mode Middleware
# ─────────────────────────────────────────────────────────

@app.middleware("http")
async def maintenance_middleware(
    request: Request,
    call_next
):
    path = request.url.path

    # Allow these routes during maintenance
    if (
        path.startswith("/admin")
        or path.startswith("/static")
        or path.startswith("/webhook")
        or path.startswith("/api/docs")
    ):
        return await call_next(request)

    try:
        import aiosqlite
        from app.core.database import DB_PATH

        async with aiosqlite.connect(DB_PATH) as db:

            db.row_factory = aiosqlite.Row

            async with db.execute(
                """
                SELECT value
                FROM platform_settings
                WHERE key = 'maintenance_mode'
                """
            ) as cursor:

                row = await cursor.fetchone()

                if row and row["value"] == "1":

                    return templates.TemplateResponse(
                        request=request,
                        name="maintenance.html",
                        status_code=503
                    )

    except Exception:
        # Don't stop the application if maintenance
        # mode checking fails.
        pass

    return await call_next(request)


# ─────────────────────────────────────────────────────────
# Landing Page
# ─────────────────────────────────────────────────────────

@app.get(
    "/",
    response_class=HTMLResponse
)
async def root(request: Request):

    return templates.TemplateResponse(
        request=request,
        name="public/index.html"
    )


# ─────────────────────────────────────────────────────────
# Admin Root Redirect
# ─────────────────────────────────────────────────────────

@app.get(
    "/admin",
    response_class=RedirectResponse
)
async def admin_root():

    return RedirectResponse(
        "/admin/login",
        status_code=302
    )


# ─────────────────────────────────────────────────────────
# Health Check
# ─────────────────────────────────────────────────────────

@app.get("/health")
async def health_check():
    """
    Health check endpoint for monitoring services
    such as UptimeRobot.
    """

    return {
        "status": "ok",
        "app": "SmartBot",
        "environment": settings.ENVIRONMENT
    }


# ─────────────────────────────────────────────────────────
# 404 Handler
# ─────────────────────────────────────────────────────────

@app.exception_handler(404)
async def not_found(
    request: Request,
    exc
):

    return HTMLResponse(
        """
        <!DOCTYPE html>

        <html lang="ar" dir="rtl">

        <head>

            <meta charset="UTF-8">

            <meta
                name="viewport"
                content="width=device-width, initial-scale=1.0"
            >

            <title>404 - SmartBot</title>

            <link
                href="https://fonts.googleapis.com/css2?family=Tajawal:wght@400;700&display=swap"
                rel="stylesheet"
            >

            <style>

                * {
                    margin: 0;
                    padding: 0;
                    box-sizing: border-box;
                }

                body {
                    font-family: Tajawal, sans-serif;
                    background: #07070e;
                    color: #fff;
                    min-height: 100vh;

                    display: flex;
                    align-items: center;
                    justify-content: center;

                    text-align: center;
                }

                .c {
                    padding: 40px;
                }

                .emoji {
                    font-size: 80px;
                    margin-bottom: 20px;
                }

                .title {
                    font-size: 28px;
                    font-weight: 700;
                    margin-bottom: 8px;
                }

                .sub {
                    color: rgba(255, 255, 255, 0.5);
                    margin-bottom: 24px;
                }

                a {
                    display: inline-block;
                    padding: 12px 24px;

                    background:
                        linear-gradient(
                            135deg,
                            #6366f1,
                            #8b5cf6
                        );

                    border-radius: 12px;

                    color: #fff;
                    text-decoration: none;

                    font-weight: 600;
                }

                a:hover {
                    opacity: 0.9;
                }

            </style>

        </head>

        <body>

            <div class="c">

                <div class="emoji">
                    🤖
                </div>

                <div class="title">
                    404 — الصفحة غير موجودة
                </div>

                <div class="sub">
                    يبدو أنك ضللت الطريق!
                </div>

                <a href="/">
                    العودة للرئيسية
                </a>

            </div>

        </body>

        </html>
        """,
        status_code=404
    )


# ─────────────────────────────────────────────────────────
# Local Development
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":

    import uvicorn

    port = int(
        os.environ.get(
            "PORT",
            8000
        )
    )

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=settings.DEBUG,
        log_level="info"
    )
