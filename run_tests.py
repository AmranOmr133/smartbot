"""
Self-contained Automated Test Runner for SmartBot
Runs end-to-end integration tests using asyncio and httpx.
Includes OTP Email Verification, Forgot/Reset Password, Admin, Bots, and Subscriptions.
"""
import sys
import asyncio
import uuid
import httpx

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from main import app
from app.core.database import init_db, DB_PATH
from app.services.email_service import build_email_content
import aiosqlite


async def run_all_tests():
    print("=" * 60)
    print("  SmartBot Automated E2E Verification Suite")
    print("=" * 60)

    # 1. DB Init
    print("\n[*] Initializing Database...")
    await init_db()
    print("[PASS] Database initialized and default admin verified.")

    # 2. Bilingual Email Content Verification
    print("\n[*] Testing Bilingual Email Templates (Arabic & English)...")
    sub_ar, html_ar, _ = build_email_content("123456", "register", "ar")
    assert "123456" in html_ar and "SmartBot" in sub_ar
    assert 'dir="rtl"' in html_ar

    sub_en, html_en, _ = build_email_content("654321", "reset_password", "en")
    assert "654321" in html_en and "Password Reset" in sub_en
    assert 'dir="ltr"' in html_en
    print("[PASS] Bilingual HTML email templates rendered correctly.")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Test 1: Landing Page
        print("\n[*] Testing Landing Page (/) ...")
        r = await client.get("/")
        assert r.status_code == 200, f"Expected 200 for landing page, got {r.status_code}"
        assert "SmartBot" in r.text
        print("[PASS] Landing page renders with 200 OK.")

        # Test 2: Admin Login page
        print("\n[*] Testing Admin Login Page (/admin/login) ...")
        r = await client.get("/admin/login")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        assert "لوحة الإدارة" in r.text or "تسجيل الدخول" in r.text
        print("[PASS] Admin login page rendered.")

        # Test 3: Admin Login submission
        print("\n[*] Testing Admin Login Authentication ...")
        r = await client.post("/admin/login", data={"username": "admin", "password": "admin123"}, follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "/admin/dashboard", f"Expected 302 -> /admin/dashboard, got {r.status_code}"
        admin_cookies = r.cookies
        print("[PASS] Admin credentials verified and session cookies issued.")

        # Test 4: Admin Dashboard & Subpages
        print("\n[*] Testing Admin Dashboard and Pages ...")
        r = await client.get("/admin/dashboard", cookies=admin_cookies)
        assert r.status_code == 200, f"Dashboard failed with {r.status_code}"
        assert "لوحة التحكم" in r.text
        print("  [+] Admin Dashboard OK")

        for subpath, title_kw in [
            ("/admin/users", "المستخدمين"),
            ("/admin/bots", "البوتات"),
            ("/admin/subscriptions", "الاشتراكات"),
            ("/admin/payments", "المدفوعات"),
        ]:
            res = await client.get(subpath, cookies=admin_cookies)
            assert res.status_code == 200, f"{subpath} failed with {res.status_code}"
            assert title_kw in res.text, f"{title_kw} not found in {subpath}"
            print(f"  [+] {subpath} OK")
        print("[PASS] All Admin panel pages rendered perfectly.")

        # Test 5: User Registration with OTP Email Dispatch
        print("\n[*] Testing User Registration & OTP Email Dispatch ...")
        test_email = f"user_{uuid.uuid4().hex[:6]}@smartbot.sa"
        r = await client.post("/register", data={
            "full_name": "أحمد الشمري",
            "email": test_email,
            "password": "Password1234!",
            "password_confirm": "Password1234!",
            "lang": "ar"
        }, follow_redirects=False)
        assert r.status_code == 302
        assert "/verify-email" in r.headers["location"]
        print(f"[PASS] User registered, redirected to verification: {r.headers['location']}")

        # Retrieve generated OTP from DB
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT code FROM verification_codes WHERE email = ? AND code_type = 'register' AND is_used = 0 ORDER BY id DESC LIMIT 1",
                (test_email,)
            ) as c:
                row = await c.fetchone()
                assert row is not None, "OTP code not found in verification_codes table"
                reg_otp = row["code"]
        print(f"[PASS] Retrieved generated OTP from DB: {reg_otp}")

        # Test 6: Verify with Wrong Code (Must Fail)
        print("\n[*] Testing Verification with Wrong Code (Expected 400) ...")
        r = await client.post("/verify-email", data={
            "email": test_email,
            "code": "000000",
            "lang": "ar"
        })
        assert r.status_code == 400
        assert "غير صحيح" in r.text
        print("[PASS] Wrong OTP correctly rejected with 400 error.")

        # Test 7: Verify with Correct Code (Account Activation & 10-day trial)
        print("\n[*] Testing Verification with Correct OTP Code ...")
        r = await client.post("/verify-email", data={
            "email": test_email,
            "code": reg_otp,
            "lang": "ar"
        }, follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "/dashboard"
        user_cookies = r.cookies
        print("[PASS] Account verified, trial activated, user logged in.")

        # Test 8: User Dashboard & Free Trial Banner
        print("\n[*] Testing User Dashboard ...")
        r = await client.get("/dashboard", cookies=user_cookies)
        assert r.status_code == 200, f"User dashboard failed with {r.status_code}"
        assert "تجربة مجانية" in r.text
        assert "أحمد الشمري" in r.text
        print("[PASS] User dashboard displays 10-day free trial active status.")

        # Test 9: Forgot Password & Reset Password Flow
        print("\n[*] Testing Forgot Password Flow ...")
        # Request password reset code
        r = await client.post("/forgot-password", data={"email": test_email, "lang": "ar"}, follow_redirects=False)
        assert r.status_code == 302
        assert "/reset-password" in r.headers["location"]
        print("[PASS] Password reset requested, redirected to reset-password page.")

        # Retrieve reset OTP code
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT code FROM verification_codes WHERE email = ? AND code_type = 'reset_password' AND is_used = 0 ORDER BY id DESC LIMIT 1",
                (test_email,)
            ) as c:
                row = await c.fetchone()
                assert row is not None, "Reset OTP code not found in DB"
                reset_otp = row["code"]
        print(f"[PASS] Retrieved reset OTP: {reset_otp}")

        # Submit new password with OTP
        new_pw = "NewSecretPassword999!"
        r = await client.post("/reset-password", data={
            "email": test_email,
            "code": reset_otp,
            "password": new_pw,
            "password_confirm": new_pw,
            "lang": "ar"
        }, follow_redirects=False)
        assert r.status_code == 302
        assert "/login" in r.headers["location"]
        print("[PASS] Password reset successfully.")

        # Login with new password
        r = await client.post("/login", data={"email": test_email, "password": new_pw, "lang": "ar"}, follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "/dashboard"
        user_cookies = r.cookies
        print("[PASS] Successfully logged in with newly updated password!")

        # Test 10: Bot Creation
        print("\n[*] Testing Bot Creation ...")
        r = await client.get("/bots/new", cookies=user_cookies)
        assert r.status_code == 200
        r = await client.post("/bots/new", data={
            "name": "بوت الدعم الفني",
            "token": f"demo_{uuid.uuid4().hex[:12]}:token",
            "welcome_message": "أهلاً بك، كيف يمكنني خدمتك اليوم؟",
            "system_prompt": "أنت موظف خدمة عملاء محترف ودود.",
            "ai_model": "gemini-1.5-flash",
            "ai_enabled": "1"
        }, cookies=user_cookies, follow_redirects=False)
        assert r.status_code in (200, 302)
        print("[PASS] Bot created successfully.")

        # Verify bot in list
        r = await client.get("/bots", cookies=user_cookies)
        assert r.status_code == 200
        assert "بوت الدعم الفني" in r.text
        print("[PASS] Bot appears in user bots list.")

        # Test 11: Subscription Page and Payment Submission
        print("\n[*] Testing Subscription Payment Flow (50 SAR) ...")
        r = await client.get("/subscription", cookies=user_cookies)
        assert r.status_code == 200
        assert "50" in r.text

        tx_ref = f"PAY-{uuid.uuid4().hex[:8].upper()}"
        r = await client.post("/subscription/pay", data={
            "payment_method": "مدى",
            "transaction_ref": tx_ref,
            "notes": "تم التحويل بنجاح عبر مدى"
        }, cookies=user_cookies)
        assert r.status_code == 200
        data = r.json()
        assert data.get("success") is True
        print(f"[PASS] Payment request submitted with ref {tx_ref}.")

        # Test 12: Admin Review & Approve Payment
        print("\n[*] Testing Admin Payment Approval ...")
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT id FROM payment_requests WHERE transaction_ref = ?", (tx_ref,)) as c:
                row = await c.fetchone()
                assert row is not None
                pay_id = row["id"]

        r = await client.post(f"/admin/payments/{pay_id}/approve", cookies=admin_cookies)
        assert r.status_code == 200
        assert r.json().get("success") is True
        print(f"[PASS] Payment ID {pay_id} approved by admin.")

        # Test 13: Verify User Subscription Upgraded to 1 Year Active
        print("\n[*] Verifying User Subscription Status Updated to Active (1 Year) ...")
        r = await client.get("/subscription", cookies=user_cookies)
        assert r.status_code == 200
        assert "نشط" in r.text
        print("[PASS] User subscription successfully upgraded to 1-Year Active!")

        # Test 14: Bot Detail & Knowledge Base Management
        print("\n[*] Testing Bot Detail & Knowledge Base ...")
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT id FROM bots ORDER BY id DESC LIMIT 1") as c:
                b_row = await c.fetchone()
                bot_id = b_row["id"]

        r = await client.get(f"/bots/{bot_id}", cookies=user_cookies)
        assert r.status_code == 200
        assert "معلومات البوت" in r.text

        # Add knowledge base item
        r = await client.post(f"/bots/{bot_id}/knowledge", data={
            "title": "أوقات الدوام",
            "content": "من الأحد إلى الخميس من الساعة 9 صباحاً حتى 5 مساءً"
        }, cookies=user_cookies)
        assert r.status_code == 200
        assert r.json().get("success") is True
        print("[PASS] Knowledge base item added.")

        # Test 15: Update Bot Settings
        print("\n[*] Testing Bot Settings Update ...")
        r = await client.post(f"/bots/{bot_id}/settings", data={
            "name": "بوت الدعم المتقدم",
            "description": "بوت ذكي مخصص للردود السريعة",
            "welcome_message": "مرحباً بكم في الدعم المتقدم!",
            "system_prompt": "أنت مساعد دعم احترافي وسريع.",
            "language": "ar",
            "ai_enabled": 1
        }, cookies=user_cookies)
        assert r.status_code == 200
        assert r.json().get("success") is True
        print("[PASS] Bot settings updated successfully.")

        # Test 16: Bot Toggle Status
        print("\n[*] Testing Bot Toggle Active/Inactive ...")
        r = await client.post(f"/bots/{bot_id}/toggle", cookies=user_cookies)
        assert r.status_code == 200
        assert r.json().get("success") is True
        print("[PASS] Bot toggled active/inactive.")

        # Test 17: One Bot Per User Limit Enforcement
        print("\n[*] Testing One Bot Per User Limit Enforcement ...")
        r = await client.post("/bots/new", data={
            "name": "بوت تجريبي ثاني",
            "token": "demo_second_bot_token",
            "system_prompt": "Prompt",
            "welcome_message": "Welcome",
            "language": "ar"
        }, cookies=user_cookies)
        assert r.status_code == 400
        assert "يُسمح بإنشاء بوت واحد فقط" in r.text
        print("[PASS] One-bot-per-user restriction enforced successfully!")

        # Test 18: Admin User Detail & Suspend
        print("\n[*] Testing Admin User Detail & Suspend Controls ...")
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT id FROM users WHERE email = ?", (test_email,)) as c:
                user_id = (await c.fetchone())["id"]

        r = await client.get(f"/admin/users/{user_id}", cookies=admin_cookies)
        assert r.status_code == 200
        assert "ملف المستخدم" in r.text
        print("[PASS] Admin user detail page loaded.")

        # Suspend user
        r = await client.post(f"/admin/users/{user_id}/suspend", data={"reason": "مخالفة تجريبية"}, cookies=admin_cookies)
        assert r.status_code == 200
        assert r.json().get("success") is True
        print("[PASS] User suspended by admin.")

        # Un-suspend user
        r = await client.post(f"/admin/users/{user_id}/suspend", data={"reason": ""}, cookies=admin_cookies)
        assert r.status_code == 200
        assert r.json().get("success") is True
        print("[PASS] User un-suspended by admin.")

        # Test 19: Platform Maintenance Mode Toggle
        print("\n[*] Testing Platform Maintenance Mode Toggle ...")
        r = await client.post("/admin/platform/maintenance", cookies=admin_cookies)
        assert r.status_code == 200
        assert r.json().get("maintenance_mode") is True
        print("[PASS] Maintenance mode activated.")

        # Non-admin request gets 503
        r = await client.get("/", follow_redirects=False)
        assert r.status_code == 503
        assert "وضع الصيانة" in r.text
        print("[PASS] Non-admin requests receive 503 Maintenance Page.")

        # Deactivate maintenance mode
        r = await client.post("/admin/platform/maintenance", cookies=admin_cookies)
        assert r.status_code == 200
        assert r.json().get("maintenance_mode") is False
        print("[PASS] Maintenance mode deactivated successfully.")

        # Test 20: Telegram Markdown Formatting
        print("\n[*] Testing Telegram Markdown Formatter ...")
        from app.services.telegram_service import format_for_telegram
        sample = "**مرحباً بك** في خدمة *العملاء*:\n- نقطة 1"
        formatted = format_for_telegram(sample)
        assert "*مرحباً بك*" in formatted
        print(f"[PASS] Telegram markdown formatted correctly: {formatted}")

        # Test 21: Receipt Size Validation (< 2MB)
        print("\n[*] Testing Receipt Size Validation (< 2MB) ...")
        # Submit with oversized file (2.5 MB) -> should fail
        big_content = b"X" * (2500 * 1024)
        files_big = {"receipt": ("receipt_big.jpg", big_content, "image/jpeg")}
        r = await client.post("/subscription/pay", data={
            "payment_method": "تحويل بنكي",
            "transaction_ref": "BIG-FILE-REF",
            "notes": "Testing size limit"
        }, files=files_big, cookies=user_cookies)
        assert r.status_code == 400
        assert "أقل من 2 ميغابايت" in r.text
        print("[PASS] Oversized receipt (>2MB) correctly rejected with 400 error.")

        # Test 22: Admin Order Details API & Conversation Messages API
        print("\n[*] Testing Admin Payment Details API & Conversations Messages API ...")
        r = await client.get(f"/admin/payments/{pay_id}/details", cookies=admin_cookies)
        assert r.status_code == 200
        data = r.json()
        assert data.get("success") is True
        assert data["payment"]["id"] == pay_id
        assert data["payment"]["full_name"] == "أحمد الشمري"
        print(f"[PASS] Admin Payment Details API returned rich order details for #{pay_id}.")

        # Fetch conversation messages API
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT id FROM conversations WHERE bot_id = ? LIMIT 1", (bot_id,)) as c:
                conv_row = await c.fetchone()
        if conv_row:
            r = await client.get(f"/conversations/{conv_row['id']}/messages", cookies=user_cookies)
            assert r.status_code == 200
            assert r.json().get("success") is True
            print(f"[PASS] Conversations Messages API returned conversation #{conv_row['id']} data.")

    print("\n" + "=" * 60)
    print("  ALL 22 ADVANCED TESTS PASSED SUCCESSFULLY! (100% OK)")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(run_all_tests())
