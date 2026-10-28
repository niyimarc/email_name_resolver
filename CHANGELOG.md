# Changelog

What changed in each version of Email Name Resolver. Newest first.
When releasing, add a section here and paste it into the GitHub release
description.

## Unreleased

- Responsive window: opens at 90% of the screen (never larger), hides less
  important columns on small windows, moves the buttons under the input box
  when narrow, and dialogs shrink to fit and scroll.
- New **Settings > Interface size** (Auto, 80% to 125%) for screens where
  everything looks too big or too small.

## 1.0.0 - 2026-10-28

First release.

- Name detection: rule-based email splitting, US first-name and surname
  dictionary (resolves confident matches instantly), and a local Ollama AI
  for the rest. The AI can never invent names.
- Input: paste or load .txt / .csv / .tsv / .xlsx in any layout; emails are
  cleaned and duplicates removed automatically.
- Speed: dictionary first, AI last; pause/resume; timer with time left;
  optional parallel AI requests.
- Mail provider detection (Gmail, Outlook, Google Workspace, Microsoft 365
  and more) and domain-level email verification (no mail server, disposable,
  role accounts).
- Export: email-sender CSV (Mailchimp, Brevo, MailerLite...) or full
  analysis; filter by provider; remove unresolved or undeliverable emails;
  one file per provider as a ZIP.
- Teach the dictionary (correct name / not a person) and import name lists.
- Re-analyze unresolved rows; resolved/unresolved counters.
- In-app Ollama setup guide; works over Remote Desktop (ANGLE).
- Single-file .exe build; version and developer info in version.py.
