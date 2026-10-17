# YouTube → Article Pipeline

Finds YouTube videos in any niche, pulls their transcripts, rewrites them into Substack-style articles
with Gemini (free tier), writes image prompts for each one, and keeps a list of drafts ready for you
to publish by hand.

| Step | What it does | Status |
|------|--------------|--------|
| 1 | Search YouTube by keywords from `config.yaml` and filter results | ✅ built |
| 2 | Download transcripts and save a draft folder per video | ✅ built |
| 3 | Rewrite transcripts into articles with the Gemini free API | ✅ built |
| 4 | Write image prompts (header/thumbnail + in-article) | ✅ built |
| 5 | Draft folders, ready-to-publish list, published tracking | ✅ built |

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

Then open `.env` and paste your two keys after `YOUTUBE_API_KEY=` and `GEMINI_API_KEY=` (see the next two sections).

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

## 3. Get a Gemini API key (free)

1. Go to <https://aistudio.google.com/apikey> and sign in with your Google account.
2. Click **Create API key**. Pick your existing Google Cloud project (or let it make one).
3. Paste it into `.env`:
   ```
   GEMINI_API_KEY=AIzaSy...your-key...
   ```

**Don't enable billing** on that project. Without billing you stay on the free tier and can't be charged.
Free-tier limits are per model and change often (roughly a few requests per minute and a limited number per day;
check the limits shown in AI Studio). The pipeline handles them for you:

- **per-minute limit**: it waits and tries again
- **daily limit**: it switches to the next model in `writing.fallback_models`; when all are used up it stops
  and leaves the remaining transcripts queued for tomorrow's run

On the free tier Google may use prompts to improve its products. That's fine for public YouTube transcripts,
but don't put private material through it.

## 4. Configure your niche

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

writing:
  model: gemini-3.8-flash
  fallback_models: [gemini-3.6-flash, gemini-3.5-flash-lite]
  audience: "..."
  tone: ""                     # leave empty if your instructions file covers tone
  target_word_count: 1200
  instructions_file: prompts/article_instructions.md
  include_source_credit: false # true adds "Inspired by <video> by <channel>" at the end
```

If you mistype a setting name, the pipeline tells you exactly which one is wrong.

### Changing how articles are written

The instructions for the AI writer live in **`prompts/article_instructions.md`**: titles, structure,
tone, what to remove, style rules. Edit that file in any text editor; no code changes needed. The next
article picks up your changes. The pipeline adds the niche, audience, target length and the response
format automatically, so you only write the editorial part.

To try new instructions on an article you already have, regenerate just that one:

```bash
python main.py write --video bNIAi9Sm3rY   # the video ID is at the end of each draft folder name
```

For a different niche with a different style, create another file (e.g. `prompts/fitness.md`) and set
`instructions_file: prompts/fitness.md` in that niche's config.

### Changing the image prompts

The image style and rules live in **`prompts/image_instructions.md`** (style, what to avoid, what each
prompt should contain). In `config.yaml`, the `images:` section sets how many prompts per article
(`count`; the first is always the header/thumbnail) and the `aspect_ratio`. To redo one article's prompts:

```bash
python main.py images --video bNIAi9Sm3rY
```

**Several niches:** copy `config.yaml` to e.g. `config.fitness.yaml`, change its keywords and give it its
own `output.database_path` and `output.drafts_dir`, then run it with `--config`:

```bash
python main.py --config config.fitness.yaml run
```

## 5. Running it

```bash
python main.py run --dry-run   # search + filter only: shows what would be processed, saves nothing
python main.py run             # all steps: search, filter, transcripts, articles, image prompts
python main.py run --no-write  # stop after transcripts (no Gemini calls)
python main.py write           # articles + image prompts for saved transcripts that don't have them yet
python main.py write --video ID  # rewrite one article and its image prompts (e.g. after editing instructions)
python main.py images          # only image prompts, for articles that don't have them yet
python main.py ready           # list drafts that are ready to publish
python main.py published ID    # mark a draft as published after you post it (--undo to reverse)
python main.py status          # how many videos are at each step, and the latest errors
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
    article.md         # the publication ready article: title, subtitle, body, source credit
    editor_notes.md    # for you, not readers: 3 title options, what was removed, claims to fact-check
    image_prompts.md   # prompts + alt text to paste into an image tool (ChatGPT, Gemini, Midjourney...)
  INDEX.md             # overview: ready to publish, published, in progress, failed (links to each draft)
```

The article's title, title options, word count and the Gemini model used are also added to `metadata.json`.

### Publishing workflow

1. Open `drafts/INDEX.md` (or run `python main.py ready`) to see what's ready.
2. In the draft folder, read `editor_notes.md`: pick a title and fact-check the flagged claims.
3. Create the images from `image_prompts.md`.
4. Paste `article.md` into Substack (or your platform), add the images, and publish.
5. Run `python main.py published <video id>`. It moves to the Published list in `INDEX.md`.

### How re-runs work

Every video is recorded in a small SQLite database (`data/pipeline.db`) with the step it reached
(`transcript` → `article` → `images` → `published`) and a status:

- **done**: that step succeeded. Unfinished videos (e.g. a transcript with no article yet) are picked
  up by the next `run` or `write`. When image prompts are done, the draft is ready to publish.
- **failed**: something went wrong (no captions, transcript too short, Gemini refused...). Skipped by
  `run` and picked up by `retry`. `python main.py status` shows the reason.
- **pending**: work started but the run was interrupted (e.g. Ctrl+C). `retry` picks these up.

So you can run `python main.py run` every day and only new work gets done.

## 6. Project layout

```
.
├── main.py                    # entry point: python main.py <command>
├── config.yaml                # your niche settings
├── prompts/
│   ├── article_instructions.md  # how the AI writes articles (edit freely)
│   └── image_instructions.md    # how the AI writes image prompts (edit freely)
├── .env.example               # template for your secrets (copy to .env)
├── requirements.txt
└── article_pipeline/
    ├── cli.py                 # commands: run, write, images, retry, ready, published, status, check
    ├── config.py              # loads + validates config.yaml and .env
    ├── youtube_search.py      # step 1: YouTube Data API search + filters
    ├── transcripts.py         # step 2: transcripts + draft folders
    ├── gemini.py              # Gemini client: rate limits, model fallback, errors
    ├── articles.py            # step 3: article writing
    ├── images.py              # step 4: image prompts
    ├── index.py               # step 5: drafts/INDEX.md
    ├── db.py                  # SQLite status tracking
    └── logging_setup.py       # console + file logging
```

Generated (git-ignored): `.env`, `data/`, `drafts/`, `logs/`, `.venv/`.

## 7. Troubleshooting

Full details of every run are written to `logs/pipeline.log`.

| Message | Fix |
|---------|-----|
| `YOUTUBE_API_KEY is not set` | Create `.env` from `.env.example` and paste your key. |
| `Your YOUTUBE_API_KEY is not valid` | Re-copy the key; no quotes or spaces around it. |
| `YouTube Data API v3 is not enabled` | Step 3 of "Get a YouTube API key". Wait a minute after enabling. |
| `daily quota is used up` | Wait until midnight Pacific time, or use fewer keywords/results. |
| `YouTube is blocking transcript requests from your IP` | See below. |
| `Captions are disabled` / `No transcript in languages` | Normal for some videos; they're marked failed and skipped. |
| `GEMINI_API_KEY is not set` / `not valid` | Create a key at aistudio.google.com/apikey and paste it in `.env`. |
| `All configured Gemini models have hit their free daily limit` | Wait until midnight Pacific time, then `python main.py write`. |
| `Model '...' was not found` | Google renamed or retired the model. Pick a current one from the [models page](https://ai.google.dev/gemini-api/docs/models) and update `writing.model`. |
| `Gemini blocked the request` / `answer was cut off` | That video is marked failed. `retry` tries again; lower `target_word_count` if it's cut off. |

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
