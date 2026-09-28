# courtwatch

Hearing reminders for advocates. Each advocate has a profile (name + WhatsApp
number) and a list of cases. Every evening courtwatch checks each case on the
court's website, and if a case is listed for **tomorrow** it sends the advocate a
WhatsApp message with the case number, parties, bench and stage.

The first court supported is the **High Court of Jammu & Kashmir and Ladakh**
(Jammu and Srinagar benches), via the eCourts High Court services portal
(`hcservices.ecourts.gov.in`). You can add more courts as adapters in `courtwatch/courts/`.

## How it works

```
advocate profiles + cases (SQLite)
        │
        ▼  run-daily (cron, ~6 PM IST)
1. refresh: for each pending case, fetch status from the court site
      first time:  case type/no/year + captcha  →  CNR + internal case id (stored)
      after that:  CNR + internal id → case history (no captcha)
2. remind:  cases whose next hearing date == tomorrow  →  WhatsApp message
            (each case/date is sent once; failed sends retry on the next run)
```

It refreshes before sending on every run, so a matter that was adjourned doesn't
get a reminder for the old date. Disposed cases are no longer checked.

## Setup

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[ocr]'              # drop [ocr] if you'll solve captchas by hand
sudo apt install tesseract-ocr       # for automatic captcha solving
cp .env.example .env                 # then edit
```

## Adding advocates and cases

One at a time:

```bash
courtwatch add-advocate "S K Gupta" "98765 43210"
courtwatch add-case 9876543210 "WP(C) 1234/2023" --bench jammu --label "Ram Kumar"
courtwatch add-case 9876543210 "OWP No. 55 of 2019" --bench srinagar
```

Case references are read the way advocates write them: `WP(C) 1234/2023`,
`OWP No. 55 of 2019`, `CRM(M)-12/2024`, `Bail App 7/2025`. The case type is
matched against the court's own case-type list. Run `courtwatch case-types --bench jammu`
to see the list.

In bulk from a CSV (see `examples/cases.csv`):

```csv
name,phone,case,bench,label
S K Gupta,9876543210,WP(C) 1234/2023,jammu,Ram Kumar
```

```bash
courtwatch import cases.csv --fetch
```

Other commands:

| command | what it does |
|---|---|
| `courtwatch search "WP(C) 1234/2023" --bench jammu` | look a case up live, without saving it |
| `courtwatch cases [--phone N]` | list tracked cases with their next date |
| `courtwatch upcoming --days 7` | hearings in the next week, all advocates |
| `courtwatch refresh [--force]` | re-fetch statuses only |
| `courtwatch notify [--today YYYY-MM-DD]` | send reminders only |
| `courtwatch run-daily` | refresh, then send reminders; schedule this |
| `courtwatch benches` / `case-types` | show the court's live bench / case-type codes |
| `courtwatch remove-case ID` | stop tracking a case |

## WhatsApp

Set `COURTWATCH_NOTIFIER` to one of:

- `console`: prints the messages. This is the default, for testing.
- `whatsapp_cloud`: Meta WhatsApp Business Cloud API. Needs a WhatsApp Business
  account, a phone number ID and a permanent access token.
- `twilio`: Twilio's WhatsApp API. The Twilio sandbox is the quickest way to try it.

WhatsApp only lets a business start a conversation with an **approved message
template**. Create one in WhatsApp Manager named `hearing_reminder` (category
*Utility*, language *English*) with this body:

```
Hearing reminder for {{1}}
Case: {{2}}
Parties: {{3}}
Court: {{4}}
Date: {{5}}
Stage: {{6}}
```

On Twilio, create the same template in Content Template Builder and set
`TWILIO_CONTENT_SID`. If `TWILIO_CONTENT_SID` is empty, a free-form message is
sent instead, which only works in the sandbox or within 24h of the advocate
messaging you.

## Scheduling

Run `courtwatch run-daily` once every evening, after the court has updated next
dates for the day. `deploy/courtwatch.cron` has an example for 6:05 PM IST. It
needs a machine that is always on (a small VPS is enough), because the SQLite
file holds all the state.

## Captchas

The eCourts case search has a captcha. courtwatch only needs to solve it the
**first** time it looks up a case. After that it stores the CNR and the court's
internal case id, and later refreshes skip the captcha.

- `COURTWATCH_CAPTCHA_SOLVER=tesseract` uses OCR and retries up to
  `COURTWATCH_CAPTCHA_ATTEMPTS` times. It is not perfect, and a case that fails
  is retried on the next run.
- `COURTWATCH_CAPTCHA_SOLVER=manual` saves the image to `captchas/` and asks you
  to type the text in. Use this for interactive `add-case` / `import`.

## Adding another court

Implement `CourtAdapter` (`courtwatch/courts/base.py`) and register it in
`courtwatch/courts/__init__.py`. For another High Court on eCourts, you only need
a new `ECourtsHighCourt(...)` entry with that court's `state_code`.

## Development

```bash
pip install -e '.[dev]'
pytest
```
