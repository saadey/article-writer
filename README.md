# YouTube → Article Pipeline

Finds YouTube videos in any niche, pulls their transcripts, and (in later steps) rewrites them into
Substack-style article drafts with image prompts, ready to publish by hand.

| Step | What it does | Status |
|------|--------------|--------|
| 1 | Search YouTube by keywords from `config.yaml` and filter results | ✅ built |
| 2 | Download transcripts and save a draft folder per video | ✅ built |
| 3 | Rewrite transcripts into articles with the Gemini free API | planned |
| 4 | Generate thumbnail image prompts | planned |
| 5 | Final draft folders for manual publishing | planned |

Your niche lives entirely in `config.yaml`. Your API keys live in `.env`. You don't need to edit any code.

---

## 1. Setup (about 10 minutes)

You need **Python 3.9 or newer**. Check with `python3 --version`.

```bash
# 1. Go into the project folder
cd "Article Writer"

# 2. Create an isolated Python environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3. Create your secrets file
cp .env.example .env               # Windows: copy .env.example .env
```

Then open `.env` and paste your YouTube API key after `YOUTUBE_API_KEY=` (see the next section).

Check that everything works:

```bash
python main.py check
```

> Each time you open a new terminal, run `source .venv/bin/activate` again before using the pipeline.

## 2. Get a YouTube API key (free)

1. Go to <https://console.cloud.google.com/> and sign in with a Google account.
2. Click the project picker at the top, then **New Project**. Name it anything (e.g. "article-pipeline") and click **Create**.
3. Open **APIs & Services → Library**, search for **YouTube Data API v3**, open it, and click **Enable**.
4. Open **APIs & Services → Credentials → Create credentials → API key**. Copy the key.
5. (Recommended) Click the key, then under **API restrictions** choose **Restrict key → YouTube Data API v3** and save.
   Leave **Application restrictions** set to *None*. Otherwise the script can't use the key.
6. Paste it into `.env`:
   ```
   YOUTUBE_API_KEY=AIzaSy...your-key...
   ```

No billing account is needed. The free quota is **10,000 units per day**, and it resets at midnight Pacific time:

- each keyword search costs **100 units** per 50 results
- looking up video details costs **1 unit** per 50 videos

With 3 keywords and 25 results each, a run costs about 300 units, so you can run it ~30 times a day.

## 3. Configure your niche

Open `config.yaml`. Every setting has a comment. The important ones:

```yaml
search:
  keywords: ["how to budget money", "pay off debt fast"]   # one search per keyword
  max_results_per_keyword: 25

filters:
  min_views: 10000
  max_age_days: 365
  min_duration_minutes: 8
  max_duration_minutes: 60
  max_videos_per_run: 10

transcripts:
  languages: ["en"]
  min_words: 400

writing:          # used by step 3 (article writing)
  audience: "..."
  tone: "..."
  instructions: |
    ...
```

If you mistype a setting name, the pipeline tells you exactly which one is wrong.

**Several niches:** copy `config.yaml` to e.g. `config.fitness.yaml`, change its keywords and give it its
own `output.database_path` and `output.drafts_dir`, then run it with `--config`:

```bash
python main.py --config config.fitness.yaml run
```

## 4. Running it

```bash
python main.py run --dry-run   # search + filter only: shows what would be processed, saves nothing
python main.py run             # search, filter, download transcripts, save drafts
python main.py status          # counts of done/failed videos and the latest errors
python main.py retry           # try failed (or interrupted) videos again
python main.py -v run          # same as run, but explains why each video was filtered out
```

### What you get

Each video becomes a folder in `drafts/`:

```
drafts/
  how-to-budget-on-a-low-income-AbC123xYz00/
    metadata.json      # title, channel, URL, views, duration, keyword, transcript info
    transcript.txt     # clean transcript in ~1-minute paragraphs
    transcript.json    # raw caption lines with timestamps
```

Later steps will add `article.md` and image prompts to the same folder.

### How re-runs work

Every video is recorded in a small SQLite database (`data/pipeline.db`) with a status:

- **done**: transcript saved. Never processed again.
- **failed**: something went wrong (no captions, too short, blocked...). Skipped by `run` and
  picked up by `retry`. `python main.py status` shows the reason.
- **pending**: work started but the run was interrupted (e.g. Ctrl+C). `retry` picks these up.

So you can run `python main.py run` every day and only new videos get processed.

## 5. Project layout

```
.
├── main.py                    # entry point: python main.py <command>
├── config.yaml                # your niche settings
├── .env.example               # template for your secrets (copy to .env)
├── requirements.txt
└── article_pipeline/
    ├── cli.py                 # commands: run, retry, status, check
    ├── config.py              # loads + validates config.yaml and .env
    ├── youtube_search.py      # step 1: YouTube Data API search + filters
    ├── transcripts.py         # step 2: transcripts + draft folders
    ├── db.py                  # SQLite status tracking
    └── logging_setup.py       # console + file logging
```

Generated (git-ignored): `.env`, `data/`, `drafts/`, `logs/`, `.venv/`.

## 6. Troubleshooting

Full details of every run are written to `logs/pipeline.log`.

| Message | Fix |
|---------|-----|
| `YOUTUBE_API_KEY is not set` | Create `.env` from `.env.example` and paste your key. |
| `Your YOUTUBE_API_KEY is not valid` | Re-copy the key; no quotes or spaces around it. |
| `YouTube Data API v3 is not enabled` | Step 3 of "Get a YouTube API key". Wait a minute after enabling. |
| `daily quota is used up` | Wait until midnight Pacific time, or use fewer keywords/results. |
| `YouTube is blocking transcript requests from your IP` | See below. |
| `Captions are disabled` / `No transcript in languages` | Normal for some videos; they're marked failed and skipped. |

**Blocked transcript requests.** Transcripts aren't part of the official API. They're fetched the way
the YouTube website loads them, and YouTube blocks IPs that look automated. This is common on **VPNs, cloud
servers, and after many requests in a short time**. The pipeline stops the run as soon as this happens
so it doesn't mark every video as failed. To fix it:

1. Turn off any VPN and run from a normal home connection.
2. Wait a few hours (blocks are usually temporary), then run `python main.py retry`.
3. Raise `transcripts.delay_seconds` in `config.yaml` (e.g. to 5).
4. If you still get blocked, use a rotating residential proxy and put its URL in `.env`:
   `TRANSCRIPT_PROXY_URL=http://user:pass@host:port`.

**"NotOpenSSLWarning" on macOS:** this warning is harmless and already silenced in `main.py`.
