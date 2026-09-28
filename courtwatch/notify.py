"""Reminder delivery: Meta WhatsApp Cloud API, Twilio WhatsApp, or console (dry run)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

import requests

from .config import Config
from .models import Advocate, TrackedCase


@dataclass
class Reminder:
    advocate: Advocate
    case: TrackedCase
    hearing_date: date
    court_name: str

    def params(self) -> list[str]:
        """Values for the approved WhatsApp template, in {{1}}..{{6}} order.

        WhatsApp rejects template parameters containing newlines/tabs, so keep each one-line.
        """
        case = self.case
        return [
            _one_line(self.advocate.name),
            _one_line(case.display_number + (f" ({case.label})" if case.label else "")),
            _one_line(case.title or "-"),
            _one_line(f"{self.court_name}, {case.bench.title()} bench"),
            self.hearing_date.strftime("%a, %d %b %Y"),
            _one_line(case.stage or case.coram or "-"),
        ]

    def text(self) -> str:
        name, number, title, court, when, stage = self.params()
        return (
            f"Hearing reminder for {name}\n"
            f"Case: {number}\n"
            f"Parties: {title}\n"
            f"Court: {court}\n"
            f"Date: {when}\n"
            f"Stage: {stage}"
        )


def _one_line(text: str, limit: int = 200) -> str:
    return " ".join(text.split())[:limit]


class Notifier:
    channel = "base"

    def send(self, reminder: Reminder) -> str | None:
        """Deliver the reminder; return the provider's message id if any."""
        raise NotImplementedError


class ConsoleNotifier(Notifier):
    channel = "console"

    def send(self, reminder: Reminder) -> str | None:
        print(f"--- to +{reminder.advocate.phone} ---\n{reminder.text()}\n")
        return None


class NotifyError(Exception):
    pass


class WhatsAppCloudNotifier(Notifier):
    """Meta WhatsApp Business Cloud API.

    Messages you start (not replies within 24h of the advocate writing to you)
    must use a pre-approved template. See README for the template text.
    """

    channel = "whatsapp_cloud"

    def __init__(self, config: Config, session: requests.Session | None = None):
        if not (config.wa_token and config.wa_phone_number_id):
            raise NotifyError("Set WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID")
        self.config = config
        self.session = session or requests.Session()
        self.url = (
            f"https://graph.facebook.com/{config.wa_api_version}/{config.wa_phone_number_id}/messages"
        )

    def send(self, reminder: Reminder) -> str | None:
        body = {
            "messaging_product": "whatsapp",
            "to": reminder.advocate.phone,
            "type": "template",
            "template": {
                "name": self.config.wa_template_name,
                "language": {"code": self.config.wa_template_lang},
                "components": [{
                    "type": "body",
                    "parameters": [{"type": "text", "text": p} for p in reminder.params()],
                }],
            },
        }
        resp = self.session.post(
            self.url, json=body, timeout=30,
            headers={"Authorization": f"Bearer {self.config.wa_token}"},
        )
        if resp.status_code >= 400:
            raise NotifyError(f"WhatsApp API {resp.status_code}: {resp.text[:500]}")
        messages = resp.json().get("messages") or [{}]
        return messages[0].get("id")


class TwilioWhatsAppNotifier(Notifier):
    channel = "twilio"

    def __init__(self, config: Config, session: requests.Session | None = None):
        if not (config.twilio_account_sid and config.twilio_auth_token and config.twilio_from):
            raise NotifyError("Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_FROM")
        self.config = config
        self.session = session or requests.Session()
        self.url = f"https://api.twilio.com/2010-04-01/Accounts/{config.twilio_account_sid}/Messages.json"

    def send(self, reminder: Reminder) -> str | None:
        sender = self.config.twilio_from
        if not sender.startswith("whatsapp:"):
            sender = "whatsapp:" + sender
        data = {"From": sender, "To": f"whatsapp:+{reminder.advocate.phone}"}
        if self.config.twilio_content_sid:
            data["ContentSid"] = self.config.twilio_content_sid
            data["ContentVariables"] = json.dumps(
                {str(i): v for i, v in enumerate(reminder.params(), start=1)}
            )
        else:
            data["Body"] = reminder.text()
        resp = self.session.post(
            self.url, data=data, timeout=30,
            auth=(self.config.twilio_account_sid, self.config.twilio_auth_token),
        )
        if resp.status_code >= 400:
            raise NotifyError(f"Twilio {resp.status_code}: {resp.text[:500]}")
        return resp.json().get("sid")


def make_notifier(config: Config) -> Notifier:
    kinds = {
        "console": lambda: ConsoleNotifier(),
        "whatsapp_cloud": lambda: WhatsAppCloudNotifier(config),
        "twilio": lambda: TwilioWhatsAppNotifier(config),
    }
    try:
        return kinds[config.notifier]()
    except KeyError:
        raise NotifyError(f"Unknown notifier {config.notifier!r}. Use one of {list(kinds)}") from None
