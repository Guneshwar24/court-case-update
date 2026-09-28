import json
from datetime import date

from courtwatch.config import Config
from courtwatch.models import Advocate, TrackedCase
from courtwatch.notify import Reminder, TwilioWhatsAppNotifier, WhatsAppCloudNotifier


class FakeResp:
    status_code = 200

    def __init__(self, body):
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, body):
        self.body = body
        self.calls = []

    def post(self, url, **kw):
        self.calls.append((url, kw))
        return FakeResp(self.body)


def reminder():
    case = TrackedCase(1, 1, "jkhc", "jammu", "35", "1234", 2023, label="Sharma\nmatter",
                       case_type_name="WP(C)", title="Ram Kumar vs UT of J&K", stage="FOR ADMISSION")
    return Reminder(Advocate(1, "S. K. Gupta", "919876543210"), case, date(2026, 9, 29), "JKHC")


def test_whatsapp_cloud_template_payload():
    cfg = Config(wa_token="tok", wa_phone_number_id="555")
    session = FakeSession({"messages": [{"id": "wamid.X"}]})
    ref = WhatsAppCloudNotifier(cfg, session).send(reminder())
    assert ref == "wamid.X"
    url, kw = session.calls[0]
    assert url.endswith("/555/messages")
    assert kw["headers"]["Authorization"] == "Bearer tok"
    body = kw["json"]
    assert body["to"] == "919876543210"
    assert body["template"]["name"] == "hearing_reminder"
    params = [p["text"] for p in body["template"]["components"][0]["parameters"]]
    assert params[1] == "WP(C) 1234/2023 (Sharma matter)"  # newline stripped
    assert all("\n" not in p for p in params)


def test_twilio_freeform_body():
    cfg = Config(twilio_account_sid="AC1", twilio_auth_token="t", twilio_from="+14155238886")
    session = FakeSession({"sid": "SM1"})
    assert TwilioWhatsAppNotifier(cfg, session).send(reminder()) == "SM1"
    _, kw = session.calls[0]
    assert kw["data"]["To"] == "whatsapp:+919876543210"
    assert kw["data"]["From"] == "whatsapp:+14155238886"
    assert "29 Sep 2026" in kw["data"]["Body"]
