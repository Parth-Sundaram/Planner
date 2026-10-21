# Planner

Turns your tasks and course deadlines into a daily time-block plan that replans itself when the day slips.

- **Inputs:** quick-add in the app, Google Tasks, and auto-scraped course schedules (CSSE 304, CSSE 313)
- **Your week:** classes, meetings, meal windows, gym and chores as routines
- **Rules:** e.g. `#db` work must be done by Wednesday night of its due week
- **Scheduler:** earliest-deadline-first, splits work into blocks, squeezes compressible tasks, warns when something can't fit
- **Views:** Today (check-ins, "something came up"), Week (drag/resize), Tasks, Settings
- **Google Calendar:** reads your calendars as busy time and writes the plan to a **Plan (auto)** calendar; dragging a block there pins it

## Writing tasks

Quick-add and Google Tasks (title or notes) both understand:

| Text | Meaning |
|---|---|
| `~3h`, `~90m` | time needed |
| `!1` `!2` `!3` | priority (1 = high) |
| `#db` | tag (rules match tags) |
| `squeeze:2h` | can be compressed down to this if the week is overbooked |
| `min:45` | shortest block worth scheduling |
| `nosplit` | must be done in one sitting |
| `@2pm` | due time (Google Tasks only stores a date) |

Example Google Task: title `Prac Sec HW @2pm`, notes `~2h !1 #prac`.

## Run locally

```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
copy .env.example .env          # then fill it in
python seed.py                  # your routines + the #db rule (once)
python wsgi.py                  # API on http://127.0.0.1:5050

cd ..\frontend
npm install
npm run dev                     # open http://localhost:5173
```

With `DEV_LOGIN=1` you can click **Dev login** without Google. Tests: `cd backend && python -m pytest`.

## Google setup (about 10 minutes, once)

1. Go to https://console.cloud.google.com, create a project (e.g. "Planner").
2. **APIs & Services → Library:** enable **Google Calendar API** and **Google Tasks API**.
3. **OAuth consent screen:** User type **External**, fill in app name and your email. Add scopes
   `.../auth/calendar`, `.../auth/tasks.readonly`, `openid`, `.../auth/userinfo.email`.
   Then click **Publish app**. In "Testing" mode Google expires your login every 7 days;
   published-but-unverified is fine for personal use (you'll see an "unverified app" screen once — click Advanced → Continue).
4. **Credentials → Create credentials → OAuth client ID → Web application.** Authorized redirect URIs:
   - `http://127.0.0.1:5050/auth/callback` (local)
   - `https://YOUR-APP.onrender.com/auth/callback` (hosted)
5. Put the client ID and secret in `.env` (local) and in Render's environment (hosted).

## Host it (free)

1. **Database:** create a free Postgres at https://neon.tech and copy the connection string.
2. Push this folder to a GitHub repo.
3. **Render:** https://render.com → New → Blueprint → pick the repo (uses `render.yaml`). Fill in
   `DATABASE_URL`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `ALLOWED_EMAILS`.
4. Add the Render URL's `/auth/callback` to the Google OAuth client (step 4 above).
5. Seed your routine once from your laptop, pointed at Neon: set `DATABASE_URL` in `backend/.env` and run `python seed.py` (Render's free plan has no Shell).
6. Optional, for course pages the built-in readers don't know: create a free key at https://aistudio.google.com/apikey and add it in Render as `GEMINI_API_KEY`. `GEMINI_MODEL` overrides the model (default `gemini-2.5-flash`).
6. **Scheduled sync:** in the GitHub repo, Settings → Secrets → Actions, add `PLANNER_URL` and `CRON_SECRET`
   (copy it from Render's environment). `.github/workflows/sync.yml` then syncs 3× a day.

Free Render instances sleep after ~15 minutes idle, so the first load after a while takes ~30–60 seconds.

## How replanning works

Every change (new task, check-in, drag, "something came up", sync) triggers a replan:

1. Untouched future blocks are thrown away. Past blocks, checked-in blocks, and blocks you moved by hand (pinned) stay.
2. Routines are placed in their windows, then tasks earliest-deadline-first into the remaining free time.
3. If work doesn't fit, compressible tasks (`squeeze:`) are shrunk; anything still short shows a red warning.
4. Blocks whose time passed without a check-in show up in **How did these go?** on the Today page.

Course scrapers were written against the course pages' table layout; if a page changes format, Sync shows
`failed` for that course and everything else keeps working.


## AI features (all optional, free tier)
Set `GEMINI_API_KEY` in Render (free key: https://aistudio.google.com/apikey). It powers:
- reading course pages that have no built-in reader; after the first read it saves a *recipe* and stops calling the AI until the page is redesigned
- the assistant (Ctrl/⌘ K). Model: `GEMINI_ASSISTANT_MODEL`, default `gemini-2.5-flash-lite` for speed
- meal photo estimates on the Fitness page

## Chrome extension
`extension/` is an unpacked Chrome extension. In Chrome: chrome://extensions → Developer mode → Load unpacked → pick the folder.
Then open its options, paste your planner URL and the key from Settings → Phone & extension.
Right-click any page, selection or link → "Add to Planner" (or Alt+Shift+P). Dates in plain words ("by Friday", "every Sunday") are understood.
