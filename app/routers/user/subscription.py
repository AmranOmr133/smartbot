"""
User Subscription Router - with receipt image upload support
"""
import os
import uuid
from fastapi import APIRouter, Depends, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from datetime import datetime
import aiosqlite

from app.core.database import get_db
from app.core.security import decode_token
from app.core.config import settings
from app.core.templates import templates

router = APIRouter()

RECEIPTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "static", "uploads", "receipts"
)


def require_user(request: Request):
    token = request.cookies.get("access_token")
    if not token:
        return None
    payload = decode_token(token)
    if not payload or payload.get("type") != "user":
        return None
    return payload


@router.get("/subscription", response_class=HTMLResponse)
async def subscription_page(request: Request, db: aiosqlite.Connection = Depends(get_db)):
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

    # Get pending payment
    async with db.execute(
        "SELECT * FROM payment_requests WHERE user_id = ? AND status = 'pending' ORDER BY created_at DESC LIMIT 1",
        (user_id,)
    ) as c:
        pending_payment = await c.fetchone()
        pending_payment = dict(pending_payment) if pending_payment else None

    # Calculate subscription status
    now = datetime.utcnow()
    sub_info = {"status": "none", "days_left": 0}
    if sub:
        if sub["status"] == "trial" and sub["trial_end"]:
            end = datetime.fromisoformat(sub["trial_end"].replace("Z", ""))
            days_left = max(0, (end - now).days)
            sub_info = {"status": "trial", "days_left": days_left, "end_date": sub["trial_end"]}
        elif sub["status"] == "active" and sub["subscription_end"]:
            end = datetime.fromisoformat(sub["subscription_end"].replace("Z", ""))
            days_left = max(0, (end - now).days)
            sub_info = {"status": "active", "days_left": days_left, "end_date": sub["subscription_end"]}
        else:
            sub_info = {"status": "expired", "days_left": 0}

    return templates.TemplateResponse(
        request=request,
        name="user/subscription.html",
        context={
            "request": request,
            "user": user,
            "subscription": sub,
            "sub_info": sub_info,
            "pending_payment": pending_payment,
            "payment_methods": settings.PAYMENT_METHODS.split(","),
            "payment_amount": settings.SUBSCRIPTION_PRICE_SAR,
            "payment_whatsapp": settings.PAYMENT_WHATSAPP,
            "payment_email": settings.PAYMENT_EMAIL,
            "page": "subscription"
        }
    )


@router.post("/subscription/pay")
async def submit_payment(
    request: Request,
    payment_method: str = Form(...),
    transaction_ref: str = Form(""),
    notes: str = Form(""),
    receipt: UploadFile = File(None),
    receipt_image: UploadFile = File(None),
    db: aiosqlite.Connection = Depends(get_db)
):
    user_payload = require_user(request)
    if not user_payload:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    user_id = int(user_payload["sub"])

    # Check no pending payment exists
    async with db.execute(
        "SELECT id FROM payment_requests WHERE user_id = ? AND status = 'pending'", (user_id,)
    ) as c:
        existing = await c.fetchone()
    if existing:
        return JSONResponse({"error": "لديك طلب دفع قيد المراجعة بالفعل"}, status_code=400)

    # Handle receipt image upload
    screenshot_path = None
    upload_file = receipt or receipt_image
    if upload_file and upload_file.filename:
        allowed_types = {"image/jpeg", "image/png", "image/jpg", "image/webp"}
        if upload_file.content_type not in allowed_types:
            return JSONResponse({"error": "يُسمح فقط برفع صور (JPEG, PNG, WebP)"}, status_code=400)

        content = await upload_file.read()
        # Enforce < 2MB limit as requested
        max_size = 2 * 1024 * 1024  # 2MB
        if len(content) > max_size:
            return JSONResponse({"error": "حجم صورة الإيصال يجب أن يكون أقل من 2 ميغابايت (2MB)"}, status_code=400)

        os.makedirs(RECEIPTS_DIR, exist_ok=True)
        ext = upload_file.filename.rsplit(".", 1)[-1].lower()
        filename = f"receipt_{user_id}_{uuid.uuid4().hex[:8]}.{ext}"
        file_path = os.path.join(RECEIPTS_DIR, filename)
        with open(file_path, "wb") as f:
            f.write(content)
        screenshot_path = f"/static/uploads/receipts/{filename}"

    await db.execute("""
        INSERT INTO payment_requests (user_id, amount, payment_method, transaction_ref, notes, screenshot_path)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, settings.SUBSCRIPTION_PRICE_SAR, payment_method, transaction_ref, notes, screenshot_path))
    await db.commit()
    return JSONResponse({"success": True, "message": "تم إرسال طلب الدفع بنجاح. سيتم مراجعته خلال 24 ساعة."})
