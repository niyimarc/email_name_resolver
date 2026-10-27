# Email Name Resolver

A local Windows desktop tool (Python + Kivy) that takes a list of email
addresses and works out whether each one plausibly belongs to a real person,
and what the formatted name is. Everything runs on your machine: the AI step
uses a local Ollama model.

## How it works

For every email, in a background thread, one at a time:

1. **Rule-based split** (`pipeline/splitter.py`, pure Python). Strips `+tags`,
   splits on `.` `_` `-`, letter/digit and camelCase boundaries, and flags
   hints such as `initial+surname` (`jdoe`) or `particle` (`mary.van.der.berg`).
2. **AI judgment** (`pipeline/ai_judge.py`). Sends `raw_local`, `domain`,
   `tokens` and `hint` to Ollama with a JSON schema and `temperature=0`. The
   model only judges and formats; it never invents names. Any name part that
   does not appear in the email is rejected by the app.
3. **Company website** (`pipeline/website.py`, optional). Skips free providers
   (Gmail, Outlook, ...), otherwise checks `https://<domain>` with a 3 s timeout.
   Sites that fail or redirect elsewhere are shown as `(unverified)`.

Between steps 1 and 2, a **name dictionary** step (`pipeline/name_dictionary.py`)
checks the tokens against US name lists:

- Confident matches are resolved directly and **the AI is skipped**: `john.doe`,
  `doe.john`, `johndoe` / `sarahjones` (glued), `jdoe`, `john.paul.smith`,
  `mary.van.der.berg`.
- Generic mailboxes (`info`, `sales.team`, `no-reply`) are marked "not a person"
  directly.
- Everything else goes to the AI with evidence (which tokens are known first
  names, surnames or role words, plus a suggested split for glued tokens).

Results stream into the table as they finish. You can edit **Final Name** in
any row, click a **Reason** to see full details, and click a website to open it.

## 1. Install Ollama and a model

The app has a step-by-step version of this inside it: click **Help** (or
**Setup guide** in Settings or on the orange "Ollama not reachable" banner).
It has Copy buttons for every command and a connection test.

1. Download and install Ollama for Windows: https://ollama.com/download
2. Pull the default model (in a terminal):

   ```
   ollama pull llama3.1
   ```

3. Ollama runs in the background on `http://localhost:11434`. Check with:

   ```
   ollama list
   ```

If Ollama is not running, the app still works: it shows a banner and gives
rule-based results only.

## 2. Install the app's dependencies

Use Python 3.10 to 3.13 (64-bit). From the project folder:

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 3. Run

```
python main.py
```

1. Paste emails or click **Load file** (`.txt`, `.csv`, `.tsv`, `.xlsx`).
   The layout doesn't matter: see "Messy input" below.
2. Optional: **Settings** to change the model, Ollama URL, or turn on
   **Resolve company website**. Use **Test connection** to check Ollama.
3. Click **Analyze**. Progress shows as `Processed X / Y`. **Stop** halts
   after the current email.
4. Click **Export CSV** to save the results.

Duplicates are skipped (local part compared case-insensitively, domain
exactly), and emails already in the table are not re-analysed, so your edits
are kept. **Clear** empties the table and deletes the saved session.

### Messy input and duplicates

Emails are found by pattern, not by position, so the input can be messy:

- any CSV/Excel column, several emails per cell, any delimiter (or none),
  header and junk rows anywhere, emails spread across one long line;
- `John Doe <john@acme.com>`, quotes, `mailto:` links, trailing punctuation,
  and `john [at] acme [dot] com`;
- UTF-8, UTF-16 (Excel "Unicode text"), Windows-1252 and Latin-1 files.
  Old `.xls` files must be re-saved as `.xlsx` or `.csv`.

When you **paste** text containing `@` or **load a file**, the box is cleaned
straight away: one email per line, domains lowercased, duplicates removed
(local part compared case-insensitively). The status line reports how many
new emails were added, how many duplicates were removed, which broken entries
(like `john@`) were skipped, and how many are already in the results table.

### Name dictionary

Bundled lists (in `data/`, US government data, public domain):

- `first_names.tsv`: 8,077 first names from SSA baby names (top 1,000 per year,
  1880 to 2008) and the 1990 Census.
- `surnames.tsv`: 151,671 surnames from the 2000 Census.
- `role_words.txt`: generic mailbox words (info, sales, admin, ...).
- `disposable_domains.txt`: throwaway email domains (CC0, from
  github.com/disposable-email-domains).

Regenerate the first two with `python tools/build_name_data.py` (needs internet).

**Your own lists** live in `%APPDATA%\EmailNameResolver\dictionaries\`
(`first_names.txt`, `surnames.txt`, `role_words.txt`, one word per line).
Your entries are always trusted, and a word in your `role_words.txt` is never
treated as a name. Ways to add to them:

- **Settings → Import names...**: add a `.txt` / `.csv` (one name per line, or
  first column) to first names, surnames or "not a name" words.
- **Settings → Open dictionary folder**: edit the files in Notepad.
- **Click a row's Reason → Teach: correct name**: saves the row's Final Name
  (first part as a first name, last part as a surname) and confirms the row.
- **Click a row's Reason → Teach: not a person**: adds the address's unknown
  words (e.g. `coolguy`) to "not a name". Known names are never blocked.

Turn the dictionary off with **Settings → Use name dictionary**.

Known limits: the lists are US-centric, so other names rely on the AI (or on
your own lists). A few word pairs can match as names (`rose.garden`,
`willpower`); fix those with "Teach: not a person".

### Where data is stored

- Settings: `%APPDATA%\EmailNameResolver\settings.json`
- Last session (auto-saved when a run finishes and on close, reloaded on start):
  `%APPDATA%\EmailNameResolver\last_session.json`

### Mail provider and email verification

With **Settings > Detect provider & verify** on (default), every row gets:

- **Mail Provider**: Gmail, Outlook / Hotmail, Yahoo, iCloud, AOL, Proton and
  other free providers are recognised by domain. Company domains are looked
  up in DNS (their MX record, which names the server that receives their
  mail): Google Workspace, Microsoft 365, Zoho Mail, GoDaddy, IONOS, Fastmail,
  Rackspace, Amazon WorkMail and others, or "Other business". Security
  gateways (Mimecast, Proofpoint, Barracuda, Cisco) are shown as such,
  because they hide the mailbox host behind them.
- **Email Status**:
  - `valid`: the domain exists and has a mail server.
  - `role account`: a generic mailbox like info@ or sales@ (deliverable, but
    rarely a person).
  - `domain doesn't exist`, `no mail server`: cannot receive email.
  - `disposable`: a throwaway domain (9,900+ domains from the public
    disposable-email-domains list).
  - `invalid syntax`, `unknown (lookup failed)`, `not checked`.

Lookups use DNS-over-HTTPS (Cloudflare or Google, chosen in Settings) and run
in parallel, one per domain. Only domain names are sent, never the email
addresses. Free providers and disposable domains need no lookup at all.

This checks the **domain**, not the individual mailbox. Checking that a
mailbox exists needs an SMTP conversation with the receiving server, which
Gmail, Yahoo, Microsoft 365 and catch-all domains don't answer truthfully,
and which can get the checking computer's IP blacklisted.

### Speed, Pause and timing

A run has two phases:

1. **Fast phase**: every email gets a row straight away, in input order.
   Dictionary matches, generic mailboxes and invalid addresses are finished
   here in milliseconds. Emails that need the AI show "waiting for AI...".
2. **AI phase**: only the remaining emails go to Ollama, and their rows
   update in place as answers arrive.

Company-website lookups (if enabled) run in parallel, one per domain, at the
same time. The status line shows progress, elapsed time and an estimate of
the time left. At the end it reports the total time, how many rows the
dictionary resolved and how long the AI took per email.

**Pause / Resume** holds the run between emails (an email already sent to
the AI finishes first); paused time is not counted. **Stop** ends the run;
rows not yet sent to the AI are marked so **Re-analyze unresolved** can pick
them up later.

Making the AI phase faster:

- Use a smaller model (`llama3.2`, about 2.5x faster than `llama3.1` on CPU).
- With a graphics card, set **Settings > AI emails at once** to 2 to 4.
  Keep it at 1 on CPU-only computers (no gain, more memory).
- Keep Ollama running: the app asks Ollama to keep the model loaded for 30
  minutes, so later runs skip the 10 to 30 second load.
- Add names you see often to your dictionary (Teach / Import): those emails
  then skip the AI entirely.

### Re-analyze unresolved

After a run, **Re-analyze unresolved (N)** re-runs only the rows without a
Final Name and updates them in place. Use it after Ollama comes back online,
after teaching or importing names, or after switching model in Settings.
Resolved rows, your edits, rows you marked "not a person" and invalid
addresses are left untouched. The button shows how many rows it will retry
and is disabled when there are none.

### Results counter

Above the table the app shows **Resolved / Not resolved / Total**. A row counts
as resolved when its Final Name has text, so a name you type in yourself
counts too. The counter updates live as results arrive and as you edit.

### Export CSV

**Export CSV** opens a small dialog:

- **Email sender list** (default): ready to import into Mailchimp, Brevo
  (Sendinblue), MailerLite, GMass and most other bulk email tools. Columns:

  ```
  Email Address, First Name, Last Name, Full Name, Company Website, Mail Provider, Email Status
  ```

  - First/Last Name are taken from Final Name (your edits included).
    Middle names are dropped from First/Last but kept in Full Name, and
    particles stay with the surname (`Mary` / `van der Berg`).
  - If the first name is only an initial (`J Doe`), First Name is left blank
    so your email tool uses its fallback greeting instead of "Hi J,".
  - Invalid email addresses are always left out of this format.
- **Full analysis**: every column, in this order:
  `email, is_real_name, confidence, final_name, name_parts, reason, company_website,
  mail_provider, email_status` (the first seven are the original columns, unchanged)
  (`is_real_name` is `true`/`false`; `name_parts` is joined with ` | `).
- **Mail providers to include**: tick the providers you want (with counts),
  e.g. Gmail only, or Google Workspace + Microsoft 365 only.
- **Remove unresolved names** (on by default): skips rows without a Final Name.
- **Remove undeliverable emails** (on by default): skips invalid, non-existent,
  mail-less and disposable addresses.
- **Sort by mail provider and domain** (on by default).
- **One file per mail provider (as a .zip)**: saves a single ZIP (e.g.
  `email_list_20261027_190000.zip`) containing one CSV per provider:
  `..._gmail.csv`, `..._google_workspace.csv`, `..._microsoft_365.csv`, ...
  Right-click it in Explorer and choose **Extract All** to get the files.

Both formats are UTF-8 with BOM, so Excel opens them correctly. The dialog
remembers your last choices.

## 4. Tests

The test suite (pytest, about 290 tests) needs no Ollama, no internet and no
window: Ollama, DNS and websites are replaced by fakes, network access is
blocked, and `%APPDATA%` points at a temporary folder so your real settings,
session and name lists are never touched.

Install the test tools once (in your activated environment):

```
pip install -r requirements-dev.txt
```

Run everything from the project folder:

```
python -m pytest
```

Useful variations:

| Command | What it does |
| --- | --- |
| `python -m pytest -v` | list every test by name |
| `python -m pytest tests/test_splitter.py` | one file |
| `python -m pytest -k dictionary` | tests whose name contains "dictionary" |
| `python -m pytest -m "not slow and not ui"` | quick run (skips timing and Kivy tests) |
| `python -m pytest -x` | stop at the first failure |
| `python -m pytest --lf` | re-run only the tests that failed last time |
| `python -m pytest --cov=pipeline --cov=storage --cov=exporters --cov-report=term-missing` | coverage report with the untested line numbers |

What is covered:

| File | Area |
| --- | --- |
| `test_splitter.py` | email splitting, camelCase, digits, +tags, hints, particles |
| `test_name_dictionary.py` | US name lists, glued names, role words, your own lists |
| `test_ai_judge.py` | prompt, Ollama options, "never invent names" check, errors |
| `test_email_extract.py` | messy text/CSV/Excel input, encodings, duplicates |
| `test_mail_domain.py` | Gmail vs Google Workspace, Microsoft 365, DNS results |
| `test_website.py` | company website lookup, redirects, cache |
| `test_runner.py`, `test_runner_domains.py` | two-phase run, parallel AI, pause/stop, failures |
| `test_csv_export.py` | both CSV formats, filters, sorting, ZIP export |
| `test_storage.py` | settings and session files, corrupt-file recovery |
| `test_ui_helpers.py` | timer text, status colours, bundled assets |

Shared fixtures (fake Ollama, sample rows, the safety rules) are in
`tests/conftest.py`; pytest options are in `pytest.ini`.

### Automatic tests on GitHub

`.github/workflows/tests.yml` runs the whole suite on every push and pull
request, on Windows and Linux with Python 3.12 and 3.13, and fails if
coverage drops below 90%. Results appear in the **Actions** tab and as a
green tick / red cross next to each commit. The release build
(`build-exe.yml`) also runs the tests before building the .exe.

## 5. Build a Windows .exe

```
pyinstaller build.spec
```

The app is created at:

```
dist\EmailNameResolver\EmailNameResolver.exe
```

Copy the whole `dist\EmailNameResolver` folder to share it. Ollama must still
be installed on the target machine for AI judgment.

### Single-file .exe (no `_internal` folder)

```
pyinstaller build_onefile.spec --clean -y
```

creates **one** file, `dist\EmailNameResolver.exe`, with everything inside.
It unpacks itself to a temporary folder each time it starts, so it opens a
few seconds slower than the folder version (`build.spec`).

### Download the .exe from GitHub (no copying)

`.github/workflows/build-exe.yml` builds the single-file .exe on GitHub's
Windows machines (tests run first). To publish a new version:

```
git tag v1.0.0
git push origin v1.0.0
```

After about 5 to 10 minutes the Release appears under **Releases** on GitHub.
On any computer (e.g. the RDP) download the latest version directly:

https://github.com/niyimarc/email_name_resolver/releases/latest/download/EmailNameResolver.exe

(For a private repository, sign in to GitHub in that browser first.) You can
also run the build by hand: **Actions > Build Windows .exe > Run workflow**;
the .exe is then attached to that run under **Artifacts** instead of a Release.

The first time Windows runs a downloaded, unsigned .exe, SmartScreen may show
"Windows protected your PC": click **More info > Run anyway**.

### Remote Desktop (RDP)

RDP sessions usually only offer OpenGL 1.1 ("GDI Generic"), below Kivy's 2.0
minimum. The app detects a Remote Desktop session and switches Kivy to the
ANGLE backend (DirectX) automatically. To force a backend yourself, set the
`KIVY_GL_BACKEND` environment variable (e.g. `angle_sdl2`, `glew` or `sdl2`)
before starting the app; your value always takes priority.

## Project layout

```
email_name_resolver/
├── main.py                  # Kivy UI, threading, file dialogs
├── help_guide.py            # in-app Ollama install / model setup guide
├── pipeline/
│   ├── splitter.py          # split_email_local, hints, PARTICLES
│   ├── ai_judge.py          # NameVerdict, judge_name_with_ai
│   ├── email_extract.py     # find/clean/dedupe emails in messy text and files
│   ├── mail_domain.py       # mail provider detection, domain verification (DNS)
│   ├── name_dictionary.py   # US name lists, dictionary resolution, user lists
│   ├── runner.py            # two-phase background run, pause/stop, parallel AI
│   └── website.py           # resolve_company_website, free providers, cache
├── storage/
│   ├── settings.py          # settings.json
│   └── session.py           # last_session.json
├── exporters/
│   └── csv_export.py        # full + email-sender CSV exports
├── assets/                  # logo (SVG/PNG) and Windows icon (icon.ico)
├── data/                    # bundled US name lists
├── tools/
│   └── build_name_data.py   # regenerates data/ from public sources
├── tests/                   # pytest suite (see "4. Tests")
│   ├── conftest.py          # shared fixtures, fakes, safety rules
│   └── test_*.py            # 11 test files
├── requirements.txt
├── requirements-dev.txt     # + pytest, pytest-cov
├── pytest.ini
├── build.spec               # one-folder build
├── build_onefile.spec       # single standalone .exe
├── .github/workflows/tests.yml      # runs the tests on every push
├── .github/workflows/build-exe.yml  # builds + publishes the .exe on GitHub
└── README.md
```

## Logo

`assets/logo.svg` is the full logo and `assets/logo_small.svg` a simplified
version for small sizes (16 to 64 px). `assets/icon.ico` (used for the .exe)
contains both: the simple artwork at 16/24/32/48/64 px and the full artwork at
128/256 px. `assets/icon_small.png` is the window/taskbar icon and
`assets/logo.png` (512 px) is shown in the Help guide.

## Notes

- The UI uses plain Kivy rather than KivyMD, because the allowed dependency
  list does not include KivyMD.
- File dialogs try `plyer` first and fall back to `tkinter`. On Windows,
  plyer's dialog needs `pywin32`; without it the tkinter dialog is used, which
  works out of the box.
