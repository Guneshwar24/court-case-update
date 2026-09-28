"""Runtime configuration, read from environment variables (optionally a .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader so we don't need python-dotenv. Existing env vars win."""
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Config:
    db_path: str = "courtwatch.db"
    timezone: ZoneInfo = field(default_factory=lambda: ZoneInfo("Asia/Kolkata"))
    # Send the reminder this many days before the hearing.
    notify_days_before: int = 1
    # Re-fetch a case from the court site if the last check is older than this.
    refresh_after_hours: int = 12
    # Seconds to wait between requests to the court site (be polite).
    request_delay: float = 2.0

    # Captcha: "tesseract" (automatic OCR, needs the `ocr` extra) or "manual".
    captcha_solver: str = "tesseract"
    captcha_attempts: int = 8
    captcha_dir: str = "captchas"

    # Notifier: "console", "whatsapp_cloud" or "twilio".
    notifier: str = "console"

    # Meta WhatsApp Cloud API
    wa_token: str = ""
    wa_phone_number_id: str = ""
    wa_template_name: str = "hearing_reminder"
    wa_template_lang: str = "en"
    wa_api_version: str = "v21.0"

    # Twilio WhatsApp
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from: str = ""  # e.g. whatsapp:+14155238886
    twilio_content_sid: str = ""  # approved template; empty = free-form body

    @classmethod
    def from_env(cls, dotenv: str | Path = ".env") -> "Config":
        _load_dotenv(Path(dotenv))
        env = os.environ.get
        return cls(
            db_path=env("COURTWATCH_DB", cls.db_path),
            timezone=ZoneInfo(env("COURTWATCH_TZ", "Asia/Kolkata")),
            notify_days_before=int(env("COURTWATCH_NOTIFY_DAYS_BEFORE", cls.notify_days_before)),
            refresh_after_hours=int(env("COURTWATCH_REFRESH_AFTER_HOURS", cls.refresh_after_hours)),
            request_delay=float(env("COURTWATCH_REQUEST_DELAY", cls.request_delay)),
            captcha_solver=env("COURTWATCH_CAPTCHA_SOLVER", cls.captcha_solver),
            captcha_attempts=int(env("COURTWATCH_CAPTCHA_ATTEMPTS", cls.captcha_attempts)),
            captcha_dir=env("COURTWATCH_CAPTCHA_DIR", cls.captcha_dir),
            notifier=env("COURTWATCH_NOTIFIER", cls.notifier),
            wa_token=env("WHATSAPP_TOKEN", ""),
            wa_phone_number_id=env("WHATSAPP_PHONE_NUMBER_ID", ""),
            wa_template_name=env("WHATSAPP_TEMPLATE_NAME", cls.wa_template_name),
            wa_template_lang=env("WHATSAPP_TEMPLATE_LANG", cls.wa_template_lang),
            wa_api_version=env("WHATSAPP_API_VERSION", cls.wa_api_version),
            twilio_account_sid=env("TWILIO_ACCOUNT_SID", ""),
            twilio_auth_token=env("TWILIO_AUTH_TOKEN", ""),
            twilio_from=env("TWILIO_WHATSAPP_FROM", ""),
            twilio_content_sid=env("TWILIO_CONTENT_SID", ""),
        )
