# Article Writer

A small app that finds YouTube videos in any niche, rewrites their transcripts into original,
publication ready articles with Gemini (free tier), writes image prompts for each one, and keeps
your drafts organised until you publish them by hand.

It runs on your own computer and opens in your browser. Everything (projects, settings, prompts,
transcripts, articles) is stored in a local database. Your API keys stay in a `.env` file on your machine.

**How it works:**

1. **Find**: you choose how many videos (1, 10, 100...). It searches your keywords, filters by views, age and
   length, and lists the matches with their stats: views, likes, likes per 1,000 views, comments, channel
   and subscribers, length, upload date. No transcripts or AI yet, so this is quick and cheap.
2. **Pick**: tick the videos worth writing about. Dismiss the rest (they won't be found again).
3. **Write**: only the videos you picked get a transcript, an article (following your instructions, with
   3 title options, editor notes and claims to fact check) and image prompts. One video at a time, so
   each one shows up as ready as soon as it's done.
4. **Publish**: copy the article into Substack, then mark it as published.

(Dislike counts aren't available: YouTube removed them from its public API in 2021.)

---

## 1. Install

### Option A: download the app (no Python needed)

1. Open the repository's **Releases** page on GitHub and download the zip for your computer:
   - **Windows:** `ArticleWriter-windows.zip`
   - **Mac (Apple Silicon, M1 or newer):** `ArticleWriter-mac.zip`
2. Unzip it.
   - **Windows:** open the `ArticleWriter` folder and double-click **ArticleWriter.exe**. The first time,
     Windows may say "Windows protected your PC": click **More info → Run anyway**.
   - **Mac:** drag **Article Writer.app** to Applications. macOS blocks the first launch because the app isn't
     notarized by Apple (that needs a paid developer account). Allow it once, either way:
     - **System Settings:** double-click the app and click **Done** on the warning. Open **System Settings →
       Privacy & Security**, scroll down to *"Article Writer" was blocked*, and click **Open Anyway**. Then
       open the app again → **Open Anyway** → enter your password.
     - **Or Terminal:** `xattr -dr com.apple.quarantine "/Applications/Article Writer.app"`, then double-click it.

     (Right-click → Open no longer works for this on macOS 15 and newer.) Repeat after downloading a new version.
3. The app opens in your browser. It has no window of its own: on Mac it shows a small icon in the menu bar
   (top right of the screen) with **Open** and **Quit**; on Windows it runs in the background. You can also
   click **Quit** at the top right of the app. Double-clicking the app again reopens the browser.

Your data is kept separately from the app, so you can replace the app with a newer version anytime:

| System | Data folder |
|---|---|
| Mac | `~/Library/Application Support/Article Writer` |
| Windows | `%APPDATA%\Article Writer` |

### Option B: run from source (Python 3.9 or newer)

```bash
cd "Article Writer"
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

It opens in your browser at <http://localhost:8000>. Press Ctrl+C in the terminal (or **Quit** in the app)
to stop it. Running from source keeps your data in the project folder (`data/`, `.env`).

### Then

Open **API keys** in the app and paste your two free keys (see below).

### Building the apps (for the maintainer)

GitHub builds both apps automatically (`.github/workflows/build-apps.yml`):

- **Test build:** GitHub → **Actions → Build apps → Run workflow**. The zips appear at the bottom of the run page.
- **Release:** `git tag v1.0.0 && git push --tags`. The zips are attached to a new GitHub Release for your friends
  to download. Bump the version in `article_pipeline/__init__.py` first.

While the repository is private, friends need to be added as collaborators (Settings → Collaborators) to download
releases. Each build uses some of GitHub's free Actions minutes; macOS minutes count 10x.

To build locally: `pip install pyinstaller` then `pyinstaller packaging/article_writer.spec`. It builds for the
computer you run it on.

## 2. Get a YouTube API key (free)

1. Go to <https://console.cloud.google.com/> and sign in with a Google account.
2. Click the project picker at the top, then **New Project**. Name it anything (e.g. "article-writer") and click **Create**.
3. Open **APIs & Services → Library**, search for **YouTube Data API v3**, open it, and click **Enable**.
4. Open **APIs & Services → Credentials → Create credentials → API key**. Copy the key.
5. (Recommended) Click the key, then under **API restrictions** choose **Restrict key → YouTube Data API v3** and save.
   Leave **Application restrictions** set to *None*. Otherwise the app can't use the key.
6. Paste it on the app's **API keys** page.

No billing account is needed. The free quota is **10,000 units per day** (resets at midnight Pacific time).
Each keyword search costs **100 units** per 50 results, so 8 keywords cost about 800 units per run.

## 3. Get a Gemini API key (free)

1. Go to <https://aistudio.google.com/apikey> and sign in with your Google account.
2. Click **Create API key** and pick your existing Google Cloud project (or let it make one).
3. Paste it on the app's **API keys** page, then click **Save and test keys**.

**Don't enable billing** on that project. Without billing you stay on the free tier and can't be charged.
Free-tier limits are per model and change often (a few requests per minute, a limited number per day).
The app handles them: it waits out per-minute limits, switches to your backup models when one hits its
daily limit, and when all are used up it stops and keeps the rest queued for tomorrow. On the free tier,
Google may use prompts to improve its products, which is fine for public YouTube transcripts.

## 4. Using the app

| Page | What it's for |
|------|---------------|
| **Dashboard** | **Find videos** (choose how many), counts, **Continue writing** (videos already started), **Retry failed**, live progress with a Stop button |
| **Found** | Search results with their stats, sortable (most viewed, most liked, best like ratio, biggest channels, newest...). Tick videos and click **Write selected**, or **Dismiss** them |
| **Drafts** | Ready to publish / In progress / Published / Failed lists. Download a list as a .zip |
| **Draft page** | Read and edit the article, **Copy article for Substack** (keeps headings and formatting), pick a title option, check flagged claims, copy image prompts, see the transcript, rewrite, mark as published |
| **Settings** | Keywords, filters (views, age, length), transcript languages, Gemini model and backups, article length, image prompt count and aspect ratio |
| **Prompts** | Your instructions to the AI for articles and for image prompts. Edit freely; **Reset to default** restores the files in `prompts/` |
| **Activity** | Every task with its full log |
| **Projects** | One project per niche, each with its own settings, prompts and drafts. Create, copy, rename, delete |
| **API keys** | YouTube and Gemini keys (plus an optional transcript proxy), shared by all projects |

**Workflow:**

1. **Dashboard → Find videos**, choosing how many. When it finishes you're taken to the **Found** page.
2. Tick the promising ones and click **Write selected**. Watch progress in the blue bar; each takes a few minutes.
3. Open **Drafts → Ready to publish** and pick a draft.
4. On **Titles & notes**, choose a title and fact check the flagged claims.
5. On **Image prompts**, copy each prompt into your image tool (ChatGPT, Gemini, Midjourney...).
6. Click **Copy article for Substack**, paste into a new Substack post, add the title, subtitle and images.
7. Click **Mark as published**.

Always read and edit before publishing: the AI can get facts wrong, and the ideas come from other creators.

**How re-runs work:** each video moves through `found → transcript → article → images → published`.
Searching again only lists videos you haven't seen (found, dismissed or written ones are skipped). Finished
videos are never processed twice, failed ones wait for **Retry failed**, and anything a stopped or
out-of-quota task didn't finish stays queued for **Continue writing**.

## 5. Command line (optional)

Everything the app does is also available from the terminal, which is handy for scheduling:

```bash
python main.py projects                          # list projects
python main.py -p "Personal Finance" find -n 10  # find 10 new videos for review
python main.py -p "Personal Finance" found       # list them with stats and IDs
python main.py -p "Personal Finance" process VIDEO_ID VIDEO_ID   # write the ones you pick
python main.py -p "Personal Finance" run         # fully automatic, no review: find + write all
python main.py -p "Personal Finance" write       # continue: articles + image prompts
python main.py -p "Personal Finance" retry
python main.py -p "Personal Finance" status
python main.py -p "Personal Finance" export      # ready drafts as Markdown folders in exports/
python main.py -p "Personal Finance" published VIDEO_ID
python main.py import-config my-niche.yaml       # create a project from a config.yaml file
```

`-p` can be left out when you only have one project.

## 6. Project layout

```
.
├── app.py                     # start the app: python app.py (also the packaged app's entry point)
├── packaging/                 # PyInstaller build recipe and smoke test
├── .github/workflows/         # builds the Windows and Mac apps on GitHub
├── main.py                    # command line: python main.py --help
├── config.yaml                # starter project, imported on first launch only
├── prompts/                   # default prompts for new projects ("Reset to default")
├── requirements.txt
└── article_pipeline/
    ├── web/                   # the app: routes, background tasks, settings form, templates, CSS/JS
    ├── pipeline.py            # runs the steps (shared by app and command line)
    ├── youtube_search.py      # step 1: search + filters
    ├── transcripts.py         # step 2: transcripts
    ├── articles.py            # step 3: articles
    ├── images.py              # step 4: image prompts
    ├── gemini.py              # Gemini client: rate limits, model fallback, errors
    ├── db.py                  # SQLite: projects, videos + content, task history
    ├── projects.py            # creating/importing projects
    ├── export.py              # Markdown files and .zip downloads
    ├── config.py              # settings validation and API keys
    └── cli.py                 # command line
```

Generated on your computer (never committed): `.env` (keys), `data/app.db` (everything else),
`logs/`, `exports/`, `.venv/`. These live in the project folder when running from source, or in the data
folder above for the downloaded app. **Back up `app.db`** to keep your drafts. To move your data into the
downloaded app, quit both, then copy `data/app.db` and `.env` into the app's data folder (`app.db` goes in its
`data` subfolder).

## 7. Troubleshooting

Every task's log is on the **Activity** page; full details are also in `logs/pipeline.log`.

| Message | Fix |
|---------|-----|
| `YouTube API key is not set / not valid` | Paste it again on the API keys page. |
| `YouTube Data API v3 is not enabled` | Step 3 of "Get a YouTube API key". Wait a minute after enabling. |
| `daily quota is used up` | Wait until midnight Pacific time, or use fewer keywords/results. |
| `YouTube is blocking transcript requests from your IP` | With **Transcript source: Auto** (the default) Gemini transcribes instead, so you won't see this. See below. |
| `Captions are disabled` / `No transcript in languages` | Normal for some videos; they're marked failed. |
| `All configured Gemini models have hit their free daily limit` | Wait until midnight Pacific time, then **Continue writing**. |
| `Model '...' was not found` | Google renamed or retired it. Pick a current one from the [models page](https://ai.google.dev/gemini-api/docs/models) in Settings. |
| `Another task is still running` | One task runs at a time. Wait, or press Stop in the blue bar. |
| Port 8000 already in use | The app picks the next free port automatically. |
| Double-clicking the app does nothing visible | It may already be running: check your browser for an open tab at `localhost:8000`. Logs are in the data folder's `logs/`. |

**Blocked transcript requests.** YouTube captions aren't part of the official API, and YouTube blocks
connections that look automated, especially **VPNs, cloud servers, and many requests in a short time**.
That's why the default **Transcript source** (Settings → Transcripts) is **Auto**: it tries YouTube's
captions first and, if YouTube blocks you or a video has no captions, Gemini watches the public video from
its link and transcribes it (about 15 seconds per video, using Gemini free quota of up to 8 hours of video
per day). Choose **Gemini only** to skip YouTube entirely, or **YouTube captions only** to never use Gemini
for transcripts (then a block stops the task; wait a few hours, or add a residential proxy on the API keys page).

**"NotOpenSSLWarning" on macOS:** harmless and already silenced.
