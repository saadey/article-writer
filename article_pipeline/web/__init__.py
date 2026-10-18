"""The local web app: python app.py opens it in your browser."""

from __future__ import annotations

import os
import re
import secrets
import threading
from datetime import datetime

import markdown as md
from flask import (
    Flask, Response, abort, flash, g, jsonify, redirect, render_template, request, session, url_for,
)

from ..config import (
    APP_PORT_FILE, DB_PATH, FROZEN, KEY_NAMES, ConfigError, build_settings, default_prompt, load_keys, save_keys,
    settings_to_dict,
)
from ..db import Database, loads
from ..export import article_markdown, draft_files, zip_drafts
from ..logging_setup import get_logger
from ..projects import create_project, ensure_a_project, settings_for
from .forms import SETTINGS_FORM, display_value, field_value, parse_settings_form
from .jobs import JOB_LABELS, runner

log = get_logger()

ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
VIEWS = [("ready", "Ready to publish"), ("progress", "In progress"), ("published", "Published"),
         ("failed", "Failed"), ("all", "All")]
FOUND_SORTS = [("views", "Most viewed"), ("likes", "Most liked"), ("ratio", "Best like ratio"),
               ("subscribers", "Biggest channels"), ("comments", "Most comments"), ("newest", "Newest"),
               ("length", "Longest")]
STAGE_BADGES = {
    ("transcript", "done"): ("Needs article", "progress"),
    ("transcript", "pending"): ("Fetching transcript", "progress"),
    ("transcript", "failed"): ("Transcript failed", "failed"),
    ("article", "done"): ("Needs image prompts", "progress"),
    ("article", "pending"): ("Writing article", "progress"),
    ("article", "failed"): ("Article failed", "failed"),
    ("images", "done"): ("Ready", "ready"),
    ("images", "pending"): ("Writing image prompts", "progress"),
    ("images", "failed"): ("Image prompts failed", "failed"),
    ("published", "done"): ("Published", "published"),
}


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = secrets.token_hex(32)
    csrf_token = secrets.token_urlsafe(32)
    asset_version = secrets.token_hex(4)  # new each launch, so browsers fetch updated CSS/JS

    with Database(DB_PATH) as db:
        db.mark_orphaned_jobs()
        ensure_a_project(db)

    # ---- request plumbing ---------------------------------------------------

    @app.before_request
    def guard():
        # Only answer to this computer, by name (blocks DNS-rebinding tricks).
        host = (request.host or "").rsplit(":", 1)[0]
        if host not in ALLOWED_HOSTS:
            abort(403)
        # Every form and fetch carries a token, so other websites can't trigger actions here.
        if request.method == "POST":
            sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
            if not sent or not secrets.compare_digest(sent, csrf_token):
                abort(400, "Security token missing or out of date. Reload the page and try again.")
        g.db = Database(DB_PATH)

    @app.teardown_request
    def close_db(_exc):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    app.add_template_filter(lambda value: len(loads(value, []) or []), "json_len")
    app.add_template_filter(_compact, "compact")

    @app.context_processor
    def template_globals():
        project = getattr(g, "project", None)
        return {
            "csrf_token": csrf_token,
            "asset_version": asset_version,
            "projects": g.db.projects() if "db" in g else [],
            "project": project,
            "keys_missing": [k for k in ("YOUTUBE_API_KEY", "GEMINI_API_KEY") if not load_keys().get(k)],
            "job": runner.snapshot(),
            "badge": lambda row: STAGE_BADGES.get((row["stage"], row["status"]), (row["stage"], "progress")),
            "ago": _ago,
        }

    def load_project(pid: int):
        project = g.db.project(pid)
        if project is None:
            abort(404)
        g.project = project
        session["project_id"] = pid
        return project

    def load_video(pid: int, vid: int):
        row = g.db.video(vid)
        if row is None or row["project_id"] != pid:
            abort(404)
        return row

    def start_job(pid, kind, task, back, video_ids=None):
        try:
            runner.start(pid, kind, task, video_ids)
            flash(f"Started: {JOB_LABELS.get(kind, kind)}. Progress shows at the top of the page.", "ok")
        except RuntimeError as exc:
            flash(str(exc), "error")
        return redirect(back)

    # ---- projects & dashboard ----------------------------------------------------

    @app.get("/")
    def home():
        projects = g.db.projects()
        pid = session.get("project_id")
        if not any(p["id"] == pid for p in projects):
            pid = projects[0]["id"]
        return redirect(url_for("dashboard", pid=pid))

    @app.get("/p/<int:pid>/")
    def dashboard(pid):
        load_project(pid)
        settings = loads(g.project["settings_json"], {})
        keywords = (settings.get("search") or {}).get("keywords") or []
        per_keyword = (settings.get("search") or {}).get("max_results_per_keyword", 25)
        return render_template(
            "dashboard.html",
            counts=g.db.summary_counts(pid),
            failures=g.db.recent_failures(pid, 5),
            jobs=g.db.jobs(pid, 8),
            keywords=keywords,
            default_count=(settings.get("filters") or {}).get("max_videos_per_run", 10),
            search_cost=len(keywords) * -(-per_keyword // 50) * 100,
            waiting=len(g.db.ready_for_next(pid, "transcript")) + len(g.db.ready_for_next(pid, "article")),
            job_labels=JOB_LABELS,
        )

    @app.post("/p/<int:pid>/find")
    def find(pid):
        load_project(pid)
        try:
            limit = int(request.form.get("count", "").strip())
            if not 1 <= limit <= 500:
                raise ValueError
        except ValueError:
            flash("Enter how many videos to find, from 1 to 500.", "error")
            return redirect(url_for("dashboard", pid=pid))
        return start_job(pid, "find", lambda p: p.find(limit), url_for("dashboard", pid=pid))

    @app.get("/p/<int:pid>/found")
    def found(pid):
        load_project(pid)
        sort = request.args.get("sort", "views")
        dismissed = request.args.get("show") == "dismissed"
        counts = g.db.summary_counts(pid)
        return render_template("found.html", rows=g.db.found_videos(pid, dismissed, sort), sort=sort,
                               sorts=FOUND_SORTS, dismissed=dismissed, counts=counts)

    @app.post("/p/<int:pid>/found")
    def found_action(pid):
        load_project(pid)
        action = request.form.get("action")
        try:
            ids = [int(i) for i in request.form.getlist("ids")]
        except ValueError:
            abort(400)
        back = url_for("found", pid=pid, sort=request.form.get("sort", "views"))
        if not ids:
            flash("Tick at least one video first.", "error")
            return redirect(back)
        if action in ("dismiss", "restore"):
            n = g.db.set_dismissed(pid, ids, action == "dismiss")
            flash(f"Dismissed {n} video(s). They won't be found again." if action == "dismiss"
                  else f"Restored {n} video(s) to the Found list.", "ok")
            return redirect(back)
        if action == "process":
            return start_job(pid, "process", lambda p: p.process(ids), back, video_ids=ids)
        abort(400)

    @app.post("/p/<int:pid>/jobs")
    def start(pid):
        load_project(pid)
        kind = request.form.get("kind", "")
        tasks = {
            "run": lambda p: p.run_all(write=True),
            "transcripts": lambda p: p.run_all(write=False),
            "preview": _preview,
            "write": lambda p: p.write_pending(),
            "images": lambda p: p.write_pending(stages=("images",)),
            "retry": lambda p: p.retry(),
            "check": lambda p: p.check_keys(),
        }
        if kind not in tasks:
            abort(400)
        return start_job(pid, kind, tasks[kind], request.form.get("back") or url_for("dashboard", pid=pid))

    @app.post("/jobs/cancel")
    def cancel_job():
        runner.cancel()
        if request.headers.get("X-CSRF-Token"):
            return jsonify(ok=True)
        flash("Stopping after the current step.", "ok")
        return redirect(request.referrer or url_for("home"))

    @app.get("/api/ping")
    def ping():
        return jsonify(app="article-writer")

    @app.post("/quit")
    def quit_app():
        runner.cancel()
        APP_PORT_FILE.unlink(missing_ok=True)
        log.info("Quit from the app.")
        threading.Timer(0.5, os._exit, args=(0,)).start()  # after this page is sent
        return render_template("stopped.html", frozen=FROZEN)

    @app.get("/api/job")
    def job_status():
        return jsonify(runner.snapshot(int(request.args.get("since", 0) or 0)))

    @app.get("/p/<int:pid>/activity")
    def activity(pid):
        load_project(pid)
        return render_template("activity.html", jobs=g.db.jobs(pid, 50), job_labels=JOB_LABELS)

    @app.get("/p/<int:pid>/activity/<int:jid>")
    def activity_log(pid, jid):
        load_project(pid)
        job = g.db.job(jid)
        if job is None or job["project_id"] != pid:
            abort(404)
        return render_template("job.html", j=job, job_labels=JOB_LABELS)

    @app.get("/projects")
    def projects_page():
        rows = [(p, g.db.summary_counts(p["id"])) for p in g.db.projects()]
        if session.get("project_id"):
            g.project = g.db.project(session["project_id"])
        return render_template("projects.html", rows=rows)

    @app.post("/projects/new")
    def new_project():
        try:
            copy_from = int(request.form.get("copy_from") or 0) or None
            pid = create_project(g.db, request.form.get("name", ""), copy_from)
        except ConfigError as exc:
            flash(str(exc), "error")
            return redirect(url_for("projects_page"))
        flash("Project created. Add your keywords below, then check the Prompts page.", "ok")
        return redirect(url_for("settings_page", pid=pid))

    @app.post("/p/<int:pid>/rename")
    def rename_project(pid):
        load_project(pid)
        name = request.form.get("name", "").strip()
        existing = g.db.project_by_name(name)
        if not name or (existing and existing["id"] != pid):
            flash("Choose a different, non-empty name.", "error")
        else:
            g.db.update_project(pid, name=name)
            flash("Renamed.", "ok")
        return redirect(url_for("projects_page"))

    @app.post("/p/<int:pid>/delete")
    def delete_project(pid):
        project = load_project(pid)
        if runner.busy() and runner.current["project_id"] == pid:
            flash("Stop the running task first.", "error")
        elif request.form.get("confirm_name", "").strip() != project["name"]:
            flash("Type the project's exact name to delete it.", "error")
        elif len(g.db.projects()) == 1:
            flash("You can't delete your only project.", "error")
        else:
            g.db.delete_project(pid)
            session.pop("project_id", None)
            flash(f"Deleted '{project['name']}' and all its drafts.", "ok")
        return redirect(url_for("projects_page"))

    # ---- drafts ----------------------------------------------------------------------

    @app.get("/p/<int:pid>/drafts")
    def drafts(pid):
        load_project(pid)
        view = request.args.get("view", "ready")
        if view not in dict(VIEWS):
            view = "ready"
        counts = {v: len(g.db.videos(pid, v)) for v, _ in VIEWS}
        return render_template("drafts.html", rows=g.db.videos(pid, view), view=view, views=VIEWS,
                               view_counts=counts)

    @app.get("/p/<int:pid>/drafts/<int:vid>")
    def draft(pid, vid):
        load_project(pid)
        row = load_video(pid, vid)
        s = loads(g.project["settings_json"], {})
        tab = request.args.get("tab", "article" if row["article_body"] else "source")
        return render_template(
            "draft.html", row=row, tab=tab,
            article_html=_render_md(row["article_body"]),
            title_options=loads(row["title_options_json"], []),
            claims=loads(row["flagged_claims_json"], []),
            image_prompts=loads(row["image_prompts_json"], []),
            info=loads(row["transcript_info_json"], {}),
            meta=loads(row["metadata_json"], {}),
            article_md=article_markdown(row, (s.get("writing") or {}).get("include_source_credit", False))
            if row["article_body"] else "",
            busy=runner.busy(),
        )

    @app.post("/p/<int:pid>/drafts/<int:vid>/save")
    def save_draft(pid, vid):
        load_project(pid)
        load_video(pid, vid)
        title = request.form.get("title", "").strip()
        body = request.form.get("body", "").strip()
        if not title or not body:
            flash("The title and article can't be empty.", "error")
        else:
            g.db.edit_article(vid, title, request.form.get("subtitle", "").strip(), body)
            flash("Saved.", "ok")
        return redirect(url_for("draft", pid=pid, vid=vid, tab="article"))

    @app.post("/p/<int:pid>/drafts/<int:vid>/use-title")
    def use_title(pid, vid):
        load_project(pid)
        row = load_video(pid, vid)
        title = request.form.get("title", "").strip()
        if title:
            g.db.edit_article(vid, title, row["article_subtitle"] or "", row["article_body"] or "")
            flash(f"Title changed to “{title}”.", "ok")
        return redirect(url_for("draft", pid=pid, vid=vid, tab="article"))

    @app.post("/p/<int:pid>/drafts/<int:vid>/regenerate")
    def regenerate(pid, vid):
        load_project(pid)
        row = load_video(pid, vid)
        what = request.form.get("what", "article")
        if row["stage"] == "published":
            flash("This draft is marked as published. Move it back to ready first.", "error")
            return redirect(url_for("draft", pid=pid, vid=vid))
        if not row["transcript_text"]:
            flash("This video has no transcript yet. Use 'Retry failed' on the dashboard.", "error")
            return redirect(url_for("draft", pid=pid, vid=vid))
        if what == "images" and not row["article_body"]:
            flash("Write the article first.", "error")
            return redirect(url_for("draft", pid=pid, vid=vid))
        stages = ("images",) if what == "images" else ("article", "images")
        kind = "regenerate_images" if what == "images" else "regenerate"
        return start_job(pid, kind, lambda p: p.regenerate(vid, stages),
                         url_for("draft", pid=pid, vid=vid, tab="images" if what == "images" else "article"))

    @app.post("/p/<int:pid>/drafts/<int:vid>/posted")
    def mark_posted(pid, vid):
        load_project(pid)
        row = load_video(pid, vid)
        posted = request.form.get("posted") == "1"
        if posted and (row["stage"], row["status"]) != ("images", "done"):
            flash("Only drafts that are ready (article + image prompts) can be marked as published.", "error")
        else:
            g.db.set_posted(vid, posted)
            flash("Marked as published." if posted else "Moved back to ready to publish.", "ok")
        return redirect(url_for("draft", pid=pid, vid=vid))

    @app.post("/p/<int:pid>/drafts/<int:vid>/delete")
    def delete_draft(pid, vid):
        load_project(pid)
        row = load_video(pid, vid)
        g.db.delete_video(vid)
        flash(f"Deleted “{row['article_title'] or row['title']}”. It may be found again by a "
              "future search.", "ok")
        return redirect(url_for("drafts", pid=pid, view="all"))

    @app.get("/p/<int:pid>/drafts/<int:vid>/download/<name>")
    def download_file(pid, vid, name):
        load_project(pid)
        row = load_video(pid, vid)
        s = settings_for(g.project)
        files = draft_files(row, s.writing.include_source_credit, s.images.aspect_ratio)
        if name == "zip":
            return _attachment(zip_drafts([row], s.writing.include_source_credit, s.images.aspect_ratio),
                               f"{row['video_id']}.zip", "application/zip")
        if name not in files:
            abort(404)
        return _attachment(files[name].encode("utf-8"), name, "text/markdown; charset=utf-8")

    @app.get("/p/<int:pid>/export")
    def export_zip(pid):
        load_project(pid)
        view = request.args.get("view", "ready")
        rows = [r for r in g.db.videos(pid, view if view in dict(VIEWS) else "ready") if r["transcript_text"]]
        if not rows:
            flash("Nothing to export in this list.", "error")
            return redirect(url_for("drafts", pid=pid, view=view))
        s = settings_for(g.project)
        name = f"{re.sub(r'[^A-Za-z0-9]+', '-', g.project['name']).strip('-').lower()}-{view}.zip"
        return _attachment(zip_drafts(rows, s.writing.include_source_credit, s.images.aspect_ratio),
                           name, "application/zip")

    # ---- settings, prompts, keys ---------------------------------------------------------

    @app.route("/p/<int:pid>/settings", methods=["GET", "POST"])
    def settings_page(pid):
        load_project(pid)
        try:  # fill in defaults for settings added in newer versions
            values = settings_to_dict(build_settings(loads(g.project["settings_json"], {}), g.project["name"],
                                                     with_keys=False))
        except ConfigError:
            values = loads(g.project["settings_json"], {})
        if request.method == "POST":
            try:
                values = parse_settings_form(request.form)
                build_settings(values, g.project["name"], with_keys=False)
            except ConfigError as exc:
                flash(str(exc), "error")
            else:
                g.db.update_project(pid, settings=values)
                flash("Settings saved.", "ok")
                return redirect(url_for("settings_page", pid=pid))
        return render_template("settings.html", form=SETTINGS_FORM, values=values,
                               field_value=field_value, display_value=display_value)

    @app.route("/p/<int:pid>/prompts", methods=["GET", "POST"])
    def prompts_page(pid):
        load_project(pid)
        if request.method == "POST":
            which = request.form.get("which")
            column = {"article": "article_instructions", "image": "image_instructions"}.get(which)
            if column is None:
                abort(400)
            text = default_prompt(which) if request.form.get("reset") else request.form.get("text", "")
            if not text.strip():
                flash("The prompt can't be empty.", "error")
            else:
                g.db.update_project(pid, **{column: text.strip()})
                flash("Default prompt restored." if request.form.get("reset") else "Prompt saved.", "ok")
            return redirect(url_for("prompts_page", pid=pid) + f"#{which}")
        return render_template("prompts.html")

    @app.route("/keys", methods=["GET", "POST"])
    def keys_page():
        if session.get("project_id"):
            g.project = g.db.project(session["project_id"])
        if request.method == "POST":
            updates = {}
            for name in KEY_NAMES:
                value = request.form.get(name, "").strip()
                if value or request.form.get(f"clear_{name}"):
                    updates[name] = value  # empty fields keep the saved key
            save_keys(updates)
            flash("Keys saved to .env on this computer.", "ok")
            if request.form.get("check") and g.project:
                return start_job(g.project["id"], "check", lambda p: p.check_keys(), url_for("keys_page"))
            return redirect(url_for("keys_page"))
        keys = load_keys()
        return render_template("keys.html", saved={k: _mask(v) for k, v in keys.items()})

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    def http_error(err):
        return render_template("error.html", err=err), err.code

    return app


# ---- helpers ------------------------------------------------------------------------------

def _preview(pipeline) -> None:
    for v in pipeline.discover():
        log.info("  %s views | %d min | %s", f"{v.view_count:,}", v.duration_seconds // 60, v.title)
    log.info("Preview only: nothing was saved.")


def _render_md(text) -> str:
    if not text:
        return ""
    # Escape raw HTML from the model, then render Markdown.
    return md.markdown(text.replace("<", "&lt;"), extensions=["extra", "sane_lists"])


def _attachment(data: bytes, filename: str, mimetype: str) -> Response:
    return Response(data, mimetype=mimetype,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _mask(value) -> str:
    if not value:
        return ""
    return "•" * 6 + value[-4:] if len(value) > 10 else "•" * 6


def _compact(n) -> str:
    """1234 -> 1.2K, 3400000 -> 3.4M."""
    if n is None:
        return ""
    for size, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if n >= size:
            value = f"{n / size:.1f}".rstrip("0").rstrip(".")
            return f"{value}{suffix}"
    return str(n)


def _ago(iso) -> str:
    if not iso:
        return ""
    try:
        then = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return iso
    seconds = (datetime.now().astimezone() - then).total_seconds()
    for limit, size, unit in ((90, None, None), (3600, 60, "min"), (86400, 3600, "h"), (86400 * 30, 86400, "d"),
                              (86400 * 365, 86400 * 30, "mo"), (float("inf"), 86400 * 365, "y")):
        if seconds < limit:
            return "just now" if size is None else f"{int(seconds // size)} {unit} ago"
    return then.strftime("%Y-%m-%d")
