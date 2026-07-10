"""
src/utils/security.py
─────────────────────
Utility functions for scrubbing and redacting sensitive data (tokens, API keys,
passwords, chat IDs, email addresses) from logs and error messages.
"""

from __future__ import annotations

import os
import re


# Regex for Telegram Bot Tokens (e.g., 1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ)
TELEGRAM_BOT_URL_RE = re.compile(r"https://api\.telegram\.org/bot[^/\s]+", re.IGNORECASE)
TELEGRAM_TOKEN_RE   = re.compile(r"\b\d{8,11}:[A-Za-z0-9_-]{35,}\b")


def redact_sensitive_text(text: str) -> str:
    """
    Redact secrets (Telegram tokens, API keys, passwords, chat IDs) from text
    before logging or displaying.
    """
    if not text:
        return text

    # 1. Redact Telegram Bot API URL patterns
    text = TELEGRAM_BOT_URL_RE.sub("https://api.telegram.org/bot[REDACTED_TOKEN]", text)

    # 2. Redact standalone Telegram token patterns
    text = TELEGRAM_TOKEN_RE.sub("[REDACTED_TOKEN]", text)

    # 3. Redact environment variable secrets if currently loaded
    env_secrets = [
        "TELEGRAM_TOKEN",
        "VNSTOCK_API_KEY",
        "SMTP_PASS",
    ]
    for key in env_secrets:
        val = os.getenv(key, "").strip()
        if val and len(val) > 3 and val != "your_api_key_here":
            text = text.replace(val, f"[REDACTED_{key}]")

    return text
