"""
Core Configuration - SmartBot Platform
"""
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # App
    APP_NAME: str = "SmartBot"
    APP_URL: str = "http://localhost:8000"
    SECRET_KEY: str = "change-me-in-production"
    DEBUG: bool = True
    ENVIRONMENT: str = "development"

    # Admin
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "admin123"
    ADMIN_EMAIL: str = "admin@smartbot.sa"

    # AI
    GEMINI_API_KEY: Optional[str] = None

    # Payment
    PAYMENT_WHATSAPP: str = "+967 778420154"
    PAYMENT_EMAIL: str = "tamayozsoft.info@gmail.com"
    PAYMENT_AMOUNT_SAR: int = 50
    PAYMENT_METHODS: str = "مدى,Apple Pay,تحويل بنكي"

    # Subscription
    TRIAL_DAYS: int = 10
    SUBSCRIPTION_PRICE_SAR: int = 50
    SUBSCRIPTION_DURATION_DAYS: int = 365

    # JWT
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours
    ADMIN_TOKEN_EXPIRE_MINUTES: int = 60 * 8    # 8 hours

    # Email SMTP (Gmail)
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = "amranomr122@gmail.com"
    SMTP_PASSWORD: str = "aspn zicy aofv ugdd"
    SMTP_FROM_EMAIL: str = "amranomr122@gmail.com"
    SMTP_FROM_NAME: str = "SmartBot"
    OTP_EXPIRE_MINUTES: int = 10

    @property
    def clean_smtp_password(self) -> str:
        """Return app password without spaces."""
        return self.SMTP_PASSWORD.replace(" ", "")

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
