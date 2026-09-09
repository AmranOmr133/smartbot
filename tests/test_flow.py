"""
Integration and End-to-End Tests for SmartBot Platform
"""
import pytest
import httpx
from main import app
from app.core.database import init_db


@pytest.fixture(autouse=True)
async def setup_db():
    await init_db()


@pytest.mark.asyncio
async def test_root_redirect():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/login"


@pytest.mark.asyncio
async def test_admin_flow():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Admin login page
        r = await client.get("/admin/login")
        assert r.status_code == 200

        # Login with correct credentials
        r = await client.post("/admin/login", data={"username": "admin", "password": "admin123"}, follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/admin/dashboard"
        admin_cookies = r.cookies

        # Admin dashboard
        r = await client.get("/admin/dashboard", cookies=admin_cookies)
        assert r.status_code == 200

        # Admin subpages
        for path in ["/admin/users", "/admin/bots", "/admin/subscriptions", "/admin/payments"]:
            res = await client.get(path, cookies=admin_cookies)
            assert res.status_code == 200, f"Path {path} returned {res.status_code}"


@pytest.mark.asyncio
async def test_user_flow_and_subscription():
    import uuid
    unique_email = f"user_{uuid.uuid4().hex[:8]}@example.com"

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. Register new user
        r = await client.post("/register", data={
            "full_name": "مستخدم تجريبي",
            "email": unique_email,
            "password": "Password123!",
            "password_confirm": "Password123!"
        }, follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/dashboard"
        user_cookies = r.cookies

        # 2. User Dashboard - check 10-day trial status
        r = await client.get("/dashboard", cookies=user_cookies)
        assert r.status_code == 200
        assert "تجربة مجانية" in r.text

        # 3. Create Bot
        r = await client.post("/bots/new", data={
            "name": "بوت تجريبي",
            "token": "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
            "welcome_message": "مرحباً بك!",
            "system_prompt": "أنت مساعد ذكي ومفيد.",
            "ai_model": "gemini-1.5-flash",
            "ai_enabled": "1"
        }, cookies=user_cookies, follow_redirects=False)
        assert r.status_code in (200, 302)

        # 4. View Bots page
        r = await client.get("/bots", cookies=user_cookies)
        assert r.status_code == 200
        assert "بوت تجريبي" in r.text

        # 5. User Subscription page
        r = await client.get("/subscription", cookies=user_cookies)
        assert r.status_code == 200
        assert "50" in r.text

        # 6. Submit Payment request
        r = await client.post("/subscription/pay", data={
            "payment_method": "مدى",
            "transaction_ref": "TX-123456",
            "notes": "تحويل عبر مدى"
        }, cookies=user_cookies)
        assert r.status_code == 200
        data = r.json()
        assert data.get("success") is True

        # 7. Admin views and approves payment
        r_admin_login = await client.post("/admin/login", data={"username": "admin", "password": "admin123"}, follow_redirects=False)
        admin_cookies = r_admin_login.cookies

        r_payments = await client.get("/admin/payments", cookies=admin_cookies)
        assert r_payments.status_code == 200
        assert "TX-123456" in r_payments.text

        # Extract payment id or query payment in db
        import aiosqlite
        from app.core.database import DB_PATH
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT id FROM payment_requests WHERE transaction_ref = 'TX-123456'") as c:
                row = await c.fetchone()
                payment_id = row["id"]

        r_approve = await client.post(f"/admin/payments/{payment_id}/approve", cookies=admin_cookies)
        assert r_approve.status_code == 200
        assert r_approve.json().get("success") is True

        # 8. Verify user subscription is now active (annual plan)
        r_user_sub = await client.get("/subscription", cookies=user_cookies)
        assert r_user_sub.status_code == 200
        assert "نشط" in r_user_sub.text
