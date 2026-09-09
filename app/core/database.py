"""
Database Layer - Async SQLite with aiosqlite
"""
import aiosqlite
import os
from datetime import datetime, timedelta
from app.core.config import settings

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "smartbot.db")

CREATE_TABLES_SQL = """
-- Admins table
CREATE TABLE IF NOT EXISTS admins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name TEXT,
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    last_login TEXT
);

-- Users table
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name TEXT NOT NULL,
    phone TEXT,
    is_active INTEGER DEFAULT 1,
    is_verified INTEGER DEFAULT 0,
    is_suspended INTEGER DEFAULT 0,
    suspended_reason TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    last_login TEXT
);

-- Platform settings table (for maintenance mode and global settings)
CREATE TABLE IF NOT EXISTS platform_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Subscriptions table
CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan TEXT DEFAULT 'trial',
    status TEXT DEFAULT 'trial',
    trial_start TEXT,
    trial_end TEXT,
    subscription_start TEXT,
    subscription_end TEXT,
    max_bots INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Payment Requests table
CREATE TABLE IF NOT EXISTS payment_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    amount REAL NOT NULL,
    currency TEXT DEFAULT 'SAR',
    payment_method TEXT NOT NULL,
    transaction_ref TEXT,
    screenshot_path TEXT,
    notes TEXT,
    status TEXT DEFAULT 'pending',
    admin_notes TEXT,
    reviewed_by INTEGER REFERENCES admins(id),
    created_at TEXT DEFAULT (datetime('now')),
    reviewed_at TEXT
);

-- Bots table
CREATE TABLE IF NOT EXISTS bots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    platform TEXT DEFAULT 'telegram',
    token TEXT,
    webhook_url TEXT,
    is_active INTEGER DEFAULT 0,
    ai_enabled INTEGER DEFAULT 1,
    ai_model TEXT DEFAULT 'gemini-1.5-flash',
    system_prompt TEXT,
    welcome_message TEXT,
    language TEXT DEFAULT 'ar',
    settings TEXT DEFAULT '{}',
    allowed_group_id TEXT,
    allowed_group_title TEXT,
    group_trigger TEXT DEFAULT 'botrun',
    group_mode_enabled INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Knowledge Base table
CREATE TABLE IF NOT EXISTS knowledge_base (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    type TEXT DEFAULT 'text',
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Conversations table
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
    chat_id TEXT NOT NULL,
    chat_username TEXT,
    chat_name TEXT,
    messages_count INTEGER DEFAULT 0,
    last_message TEXT,
    last_activity TEXT DEFAULT (datetime('now')),
    created_at TEXT DEFAULT (datetime('now'))
);

-- Messages table
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Verification codes table (OTP for email verification & password reset)
CREATE TABLE IF NOT EXISTS verification_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    code TEXT NOT NULL,
    code_type TEXT NOT NULL, -- 'register' or 'reset_password'
    is_used INTEGER DEFAULT 0,
    expires_at TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_subscriptions_user_id ON subscriptions(user_id);
CREATE INDEX IF NOT EXISTS idx_bots_user_id ON bots(user_id);
CREATE INDEX IF NOT EXISTS idx_conversations_bot_id ON conversations(bot_id);
CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_payment_requests_user_id ON payment_requests(user_id);
CREATE INDEX IF NOT EXISTS idx_payment_requests_status ON payment_requests(status);
CREATE INDEX IF NOT EXISTS idx_vc_email ON verification_codes(email, code_type);
CREATE INDEX IF NOT EXISTS idx_vc_code ON verification_codes(code);
"""


async def get_db():
    """Get database connection."""
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    try:
        yield db
    finally:
        await db.close()


async def init_db():
    """Initialize database tables and default admin."""
    from app.core.security import hash_password
    
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        
        # Create all tables
        await db.executescript(CREATE_TABLES_SQL)
        await db.commit()
        
        # Migrate: add new columns to existing databases if missing
        migrations = [
            "ALTER TABLE users ADD COLUMN is_suspended INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN suspended_reason TEXT",
            "ALTER TABLE payment_requests ADD COLUMN admin_notes TEXT",
            "ALTER TABLE bots ADD COLUMN allowed_group_id TEXT",
            "ALTER TABLE bots ADD COLUMN allowed_group_title TEXT",
            "ALTER TABLE bots ADD COLUMN group_trigger TEXT DEFAULT 'botrun'",
            "ALTER TABLE bots ADD COLUMN group_mode_enabled INTEGER DEFAULT 1",
        ]
        for sql in migrations:
            try:
                await db.execute(sql)
                await db.commit()
            except Exception:
                pass  # Column already exists
        
        # Initialize default platform settings
        await db.execute(
            "INSERT OR IGNORE INTO platform_settings (key, value) VALUES ('maintenance_mode', '0')"
        )
        await db.commit()
        
        # Create default admin if not exists
        async with db.execute("SELECT id FROM admins WHERE username = ?", (settings.ADMIN_USERNAME,)) as cursor:
            admin = await cursor.fetchone()
        
        if not admin:
            password_hash = hash_password(settings.ADMIN_PASSWORD)
            await db.execute(
                "INSERT INTO admins (username, email, password_hash, full_name) VALUES (?, ?, ?, ?)",
                (settings.ADMIN_USERNAME, settings.ADMIN_EMAIL, password_hash, "System Admin")
            )
            await db.commit()
            print(f"[OK] Default admin created: {settings.ADMIN_USERNAME} / {settings.ADMIN_PASSWORD}")
        
        print(f"[OK] Database initialized at: {DB_PATH}")
