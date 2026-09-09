"""
Email Service - SMTP verification & notifications
Supports bilingual HTML templates (Arabic & English) with 6-digit OTP
"""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import asyncio
import secrets
import logging
from typing import Tuple

from app.core.config import settings

logger = logging.getLogger("email_service")


def generate_otp(length: int = 6) -> str:
    """Generate secure numeric OTP code."""
    return "".join(secrets.choice("0123456789") for _ in range(length))


def build_email_content(code: str, code_type: str = "register", lang: str = "ar") -> Tuple[str, str, str]:
    """
    Build email subject, HTML body, and plain-text version in Arabic or English.
    code_type: 'register' or 'reset_password'
    lang: 'ar' or 'en'
    """
    is_ar = (lang.lower() == "ar")
    is_register = (code_type == "register")

    if is_ar:
        dir_attr = "rtl"
        font_family = "'Tajawal', 'Segoe UI', Arial, sans-serif"
        if is_register:
            subject = f"رمز تأكيد حسابك في SmartBot: {code}"
            title = "تأكيد بريدك الإلكتروني"
            badge = "إنشاء حساب جديد"
            greeting = "مرحباً بك في منصة SmartBot!"
            desc = "شكراً لتسجيلك معنا. لإكمال إنشاء حسابك وتفعيل تجربتك المجانية لمدة 10 أيام، يرجى استخدام رمز التحقق التالي:"
            cta_note = "أدخل هذا الرمز في صفحة التأكيد للمتابعة."
        else:
            subject = f"رمز إعادة تعيين كلمة المرور: {code}"
            title = "إعادة تعيين كلمة المرور"
            badge = "أمان الحساب"
            greeting = "مرحباً،"
            desc = "تلقينا طلباً لإعادة تعيين كلمة المرور لحسابك في منصة SmartBot. استخدم رمز التحقق التالي لتعيين كلمة مرور جديدة:"
            cta_note = "إذا لم تكن أنت من قام بهذا الطلب، يمكنك تجاهل هذا البريد بأمان ولن يتم إجراء أي تغيير."

        expire_text = f"ينتهي هذا الرمز خلال {settings.OTP_EXPIRE_MINUTES} دقائق."
        warning_text = "تنبيه أمني: لا تشارك هذا الرمز مع أي شخص آخر، فريق الدعم لن يطلب منك هذا الرمز أبداً."
        footer_text = "SmartBot — منصة إدارة بوتات الذكاء الاصطناعي والتليغرام"
        rights_text = "جميع الحقوق محفوظة © SmartBot"
    else:
        dir_attr = "ltr"
        font_family = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
        if is_register:
            subject = f"Your SmartBot Verification Code: {code}"
            title = "Verify Your Email Address"
            badge = "Account Registration"
            greeting = "Welcome to SmartBot!"
            desc = "Thank you for registering. To complete your account creation and activate your 10-day free trial, please use the following verification code:"
            cta_note = "Enter this code on the verification screen to continue."
        else:
            subject = f"Password Reset Code: {code}"
            title = "Reset Your Password"
            badge = "Account Security"
            greeting = "Hello,"
            desc = "We received a request to reset the password for your SmartBot account. Use the following verification code to set a new password:"
            cta_note = "If you did not request this, you can safely ignore this message and no changes will be made."

        expire_text = f"This code expires in {settings.OTP_EXPIRE_MINUTES} minutes."
        warning_text = "Security Notice: Never share this code with anyone. Our support team will never ask for it."
        footer_text = "SmartBot — AI-powered Telegram Bot Management Platform"
        rights_text = "All rights reserved © SmartBot"

    # Plain-text version
    text_content = f"""{title}
{greeting}
{desc}

VERIFICATION CODE: {code}

{expire_text}
{warning_text}

{footer_text}
"""

    # Rich Responsive HTML
    html_content = f"""<!DOCTYPE html>
<html lang="{ 'ar' if is_ar else 'en' }" dir="{dir_attr}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{subject}</title>
</head>
<body style="margin:0;padding:0;background-color:#07070e;font-family:{font_family};color:#ffffff;-webkit-font-smoothing:antialiased;">
    <table border="0" cellpadding="0" cellspacing="0" width="100%" style="background-color:#07070e;padding:30px 10px;">
        <tr>
            <td align="center">
                <table border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width:540px;background:#0d0d18;border-radius:18px;border:1px solid rgba(255,255,255,0.08);overflow:hidden;box-shadow:0 20px 40px rgba(0,0,0,0.5);">
                    <!-- Header -->
                    <tr>
                        <td align="center" style="padding:36px 30px 20px;background:linear-gradient(180deg, rgba(99,102,241,0.12) 0%, rgba(13,13,24,0) 100%);">
                            <div style="display:inline-flex;align-items:center;justify-content:center;width:56px;height:56px;background:linear-gradient(135deg,#6366f1,#8b5cf6);border-radius:14px;box-shadow:0 8px 20px rgba(99,102,241,0.35);font-size:28px;line-height:56px;text-align:center;margin-bottom:14px;">
                                🤖
                            </div>
                            <div style="font-size:22px;font-weight:800;color:#ffffff;letter-spacing:-0.5px;">SmartBot</div>
                            <div style="display:inline-block;margin-top:10px;padding:4px 12px;background:rgba(99,102,241,0.15);border:1px solid rgba(99,102,241,0.3);border-radius:20px;color:#a5b4fc;font-size:11px;font-weight:600;">
                                {badge}
                            </div>
                        </td>
                    </tr>

                    <!-- Body Content -->
                    <tr>
                        <td style="padding:10px 36px 30px;">
                            <h1 style="margin:0 0 12px;font-size:22px;font-weight:700;color:#ffffff;text-align:{ 'right' if is_ar else 'left' };">{title}</h1>
                            <p style="margin:0 0 8px;font-size:15px;color:#cbd5e1;line-height:1.6;text-align:{ 'right' if is_ar else 'left' };"><strong>{greeting}</strong></p>
                            <p style="margin:0 0 24px;font-size:14px;color:#94a3b8;line-height:1.7;text-align:{ 'right' if is_ar else 'left' };">{desc}</p>

                            <!-- OTP Box -->
                            <div style="margin:24px 0;padding:22px;background:linear-gradient(135deg, rgba(99,102,241,0.12) 0%, rgba(139,92,246,0.08) 100%);border:2px dashed #6366f1;border-radius:14px;text-align:center;">
                                <div style="font-size:12px;text-transform:uppercase;letter-spacing:1px;color:#a5b4fc;font-weight:600;margin-bottom:8px;">
                                    { 'رمز التحقق الخاص بك' if is_ar else 'YOUR VERIFICATION CODE' }
                                </div>
                                <div style="font-size:38px;font-weight:800;letter-spacing:10px;color:#ffffff;font-family:'Courier New', monospace;text-shadow:0 2px 10px rgba(99,102,241,0.5);">
                                    {code}
                                </div>
                                <div style="margin-top:10px;font-size:12px;color:#f59e0b;font-weight:500;">
                                    ⏱ {expire_text}
                                </div>
                            </div>

                            <p style="margin:0 0 16px;font-size:13px;color:#94a3b8;text-align:{ 'right' if is_ar else 'left' };line-height:1.6;">{cta_note}</p>

                            <!-- Warning Box -->
                            <div style="padding:12px 16px;background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.2);border-radius:10px;font-size:12px;color:#fca5a5;line-height:1.5;text-align:{ 'right' if is_ar else 'left' };">
                                🔒 {warning_text}
                            </div>
                        </td>
                    </tr>

                    <!-- Footer -->
                    <tr>
                        <td style="padding:22px 30px;background:rgba(0,0,0,0.3);border-top:1px solid rgba(255,255,255,0.05);text-align:center;">
                            <div style="font-size:12px;color:#64748b;margin-bottom:6px;">{footer_text}</div>
                            <div style="font-size:11px;color:#475569;">{rights_text}</div>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"""
    return subject, html_content, text_content


def _send_sync_email(to_email: str, subject: str, html_content: str, text_content: str) -> bool:
    """Synchronous SMTP email sender."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{settings.SMTP_FROM_NAME} <{settings.SMTP_FROM_EMAIL}>"
    msg["To"] = to_email

    part1 = MIMEText(text_content, "plain", "utf-8")
    part2 = MIMEText(html_content, "html", "utf-8")
    msg.attach(part1)
    msg.attach(part2)

    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(settings.SMTP_USER, settings.clean_smtp_password)
            server.send_message(msg)
        logger.info(f"Email successfully sent to {to_email} (Subject: {subject})")
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}")
        print(f"[ERROR] Email sending failed: {e}")
        return False


async def send_verification_email(to_email: str, code: str, code_type: str = "register", lang: str = "ar") -> bool:
    """
    Send verification email asynchronously via thread pool.
    Returns True if sent successfully, False otherwise.
    """
    subject, html_content, text_content = build_email_content(code, code_type, lang)
    return await asyncio.to_thread(_send_sync_email, to_email, subject, html_content, text_content)
