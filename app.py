import json
import logging
import os
import secrets
import threading

from flask import Flask, redirect, render_template_string, request, session, url_for, jsonify

from db import (
    close_db,
    env_or_file,
    get_db,
    get_pending_musicbrainz_count,
    get_token_row,
    get_track_detail,
    get_track_override_genres,
    get_unmatched_tracks,
    init_db,
    replace_genre_overrides,
    upsert_token,
    build_cursor_from_row,
)
from musicbrainz_oauth import (
    MUSICBRAINZ_CLIENT_ID,
    MUSICBRAINZ_REDIRECT_URI,
    build_mb_authorize_url,
    exchange_mb_code,
    refresh_mb_token,
)
from playlist_builder import build_playlist_buckets, sync_bucket_playlists
from spotify_client import (
    SCOPES,
    SPOTIFY_CLIENT_ID,
    SPOTIFY_CLIENT_SECRET,
    SPOTIFY_REDIRECT_URI,
    build_authorize_url,
    get_me,
    spotify_token_request,
)
from sync import (
    enrich_selected_tracks,
    reclassify_track,
    sync_all_musicbrainz,
    sync_artist_metadata,
    sync_next_musicbrainz_batch,
    sync_saved_library,
)

APP_NAME = env_or_file("APP_NAME", "Spotify Decades Plus")
SECRET_KEY = env_or_file("FLASK_SECRET_KEY", secrets.token_hex(32))
PLAYLIST_PREFIX = env_or_file("PLAYLIST_PREFIX", "My")
PLAYLIST_PUBLIC = env_or_file("PLAYLIST_PUBLIC", "false").lower() == "true"
PORT = int(env_or_file("PORT", "8080"))
DEBUG_MODE = env_or_file("APP_DEBUG", "false").lower() == "true"
LOG_PATH = env_or_file("LOG_PATH", "/logs/app.log")
MB_BATCH_SIZE = int(env_or_file("MB_BATCH_SIZE", "5"))
MB_BACKGROUND_ENABLED = env_or_file("MB_BACKGROUND_ENABLED", "false").lower() == "true"

# Per-process in‑memory cursor state for optional background worker.
MB_CURSOR_STATE: dict[str, str | None] = {}

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s in %(module)s: %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.teardown_appcontext(close_db)

PAGE = """
<!doctype html><html><head><meta charset='utf-8'><title>{{ app_name }}</title>
<style>
body{font-family:system-ui,sans-serif;background:#111827;color:#f9fafb;margin:0}.wrap{max-width:1180px;margin:0 auto;padding:24px}.card{background:#1f2937;border:1px solid #374151;border-radius:16px;padding:20px;margin-bottom:16px}.row{display:flex;gap:10px;flex-wrap:wrap}a.btn,button.btn{display:inline-block;padding:10px 14px;border-radius:12px;background:#22c55e;color:#06270f;text-decoration:none;font-weight:700;margin:0 10px 10px 0;border:0;cursor:pointer}.btn-alt{background:#cbd5e1;color:#111827}.muted{color:#9ca3af}table{width:100%;border-collapse:collapse}th,td{padding:10px;border-bottom:1px solid #374151;text-align:left;vertical-align:top}pre{white-space:pre-wrap;background:#0b1220;padding:12px;border-radius:12px}.small{font-size:14px}
</style></head><body><div class='wrap'>
<div class='card'><h1>{{ app_name }}</h1><p class='muted'>Sync liked songs into SQLite, enrich with MusicBrainz, build playlists, and review unmatched tracks manually.</p>
{% if not connected %}<a class='btn' href='{{ url_for("login") }}'>Connect Spotify</a>{% else %}
<div class='row'>
<a class='btn' href='{{ url_for("run_sync") }}'>1. Sync library</a>
<a class='btn' href='{{ url_for("run_mb_sync_next") }}'>2. Sync next MB batch</a>
<a class='btn' href='{{ url_for("build_all_playlists") }}'>3. Build playlists</a>
<a class='btn btn-alt' href='{{ url_for("admin_unmatched") }}'>Admin: unmatched</a>
<a class='btn btn-alt' href='{{ url_for("mb_login") }}'>MusicBrainz OAuth</a>
<a class='btn btn-alt' href='{{ url_for("reset_mb_cursor") }}'>Reset MB cursor</a>
<a class='btn btn-alt' href='{{ url_for("logout") }}'>Disconnect</a>
</div>{% endif %}
</div>
{% if message %}<div class='card'><strong>Status:</strong> {{ message }}</div>{% endif %}
{% if progress %}<div class='card'><strong>MusicBrainz queue:</strong> {{ progress.pending }} pending, current cursor: {{ progress.cursor or 'none' }}</div>{% endif %}
{% if profile %}<div class='card'><h2>Connected account</h2><p>{{ profile.get('display_name') or profile.get('id') }} ({{ profile.get('id') }})</p></div>{% endif %}
{% if result_rows %}<div class='card'><h2>Latest playlist sync</h2><table><thead><tr><th>Type</th><th>Label</th><th>Tracks</th><th>Action</th><th>Playlist</th></tr></thead><tbody>{% for row in result_rows %}<tr><td>{{ row['type'] }}</td><td>{{ row['label'] }}</td><td>{{ row['count'] }}</td><td>{{ row['action'] }}</td><td><a href='{{ row['url'] }}' target='_blank' rel='noopener noreferrer'>{{ row['name'] }}</a></td></tr>{% endfor %}</tbody></table></div>{% endif %}
{% if debug and debug_enabled %}<div class='card'><h2>Debug</h2><pre>{{ debug }}</pre></div>{% endif %}
</div></body></html>
"""

ADMIN_PAGE = """
<!doctype html><html><head><meta charset='utf-8'><title>Admin - {{ app_name }}</title>
<style>
body{font-family:system-ui,sans-serif;background:#111827;color:#f9fafb;margin:0}.wrap{max-width:1280px;margin:0 auto;padding:24px}.card{background:#1f2937;border:1px solid #374151;border-radius:16px;padding:20px;margin-bottom:16px}a.btn,button.btn{display:inline-block;padding:10px 14px;border-radius:12px;background:#22c55e;color:#06270f;text-decoration:none;font-weight:700;border:0;cursor:pointer}.btn-alt{background:#cbd5e1;color:#111827}table{width:100%;border-collapse:collapse}th,td{padding:10px;border-bottom:1px solid #374151;text-align:left;vertical-align:top}.muted{color:#9ca3af}input[type=text]{width:100%;padding:10px;border-radius:10px;border:1px solid #4b5563;background:#0b1220;color:#f9fafb}.small{font-size:14px}.mono{font-family:ui-monospace,SFMono-Regular,monospace}
</style></head><body><div class='wrap'>
<div class='card'><h1>Admin: unmatched tracks</h1><p class='muted'>Review unresolved MusicBrainz matches, run selected enrichment, and apply manual genre overrides.</p><a class='btn btn-alt' href='{{ url_for("index") }}'>Back</a></div>
{% if message %}<div class='card'><strong>Status:</strong> {{ message }}</div>{% endif %}
<div class='card'><form method='post' action='{{ url_for("enrich_selected_route") }}'><table><thead><tr><th>Select</th><th>Track</th><th>Artists</th><th>Album / Year</th><th>Match status</th><th>Overrides</th><th>Actions</th></tr></thead><tbody>
{% for row in rows %}
<tr>
<td><input type='checkbox' name='track_ids' value='{{ row["spotify_track_id"] }}'></td>
<td><div><strong>{{ row['name'] }}</strong></div><div class='small mono'>{{ row['spotify_track_id'] }}</div><div class='small muted'>ISRC: {{ row['isrc'] or '—' }}</div></td>
<td>{{ row['artist_names'] or '—' }}</td>
<td>{{ row['album_name'] or '—' }}<div class='small muted'>{{ row['album_release_date'] or '—' }}</div></td>
<td>{{ row['status'] or 'not attempted' }}<div class='small muted'>score: {{ row['score'] if row['score'] is not none else '—' }}</div><div class='small muted'>{{ row['last_error'] or '' }}</div></td>
<td><input type='text' form='override-{{ row["spotify_track_id"] }}' name='genres' value='{{ row["override_csv"] }}' placeholder='Alternative, Heavy Metal'></td>
<td>
<form id='override-{{ row["spotify_track_id"] }}' method='post' action='{{ url_for("save_override", spotify_track_id=row["spotify_track_id"]) }}'></form>
<button class='btn' form='override-{{ row["spotify_track_id"] }}' type='submit'>Save override</button>
<a class='btn btn-alt' href='{{ url_for("track_detail", spotify_track_id=row["spotify_track_id"]) }}'>Details</a>
</td>
</tr>
{% endfor %}
</tbody></table><div style='margin-top:16px'><button class='btn' type='submit'>Enrich selected tracks</button></div></form></div></div></body></html>
"""

DETAIL_PAGE = """
<!doctype html><html><head><meta charset='utf-8'><title>Track detail - {{ app_name }}</title>
<style>
body{font-family:system-ui,sans-serif;background:#111827;color:#f9fafb;margin:0}.wrap{max-width:1000px;margin:0 auto;padding:24px}.card{background:#1f2937;border:1px solid #374151;border-radius:16px;padding:20px;margin-bottom:16px}a.btn{display:inline-block;padding:10px 14px;border-radius:12px;background:#22c55e;color:#06270f;text-decoration:none;font-weight:700;border:0;cursor:pointer}.btn-alt{background:#cbd5e1;color:#111827}pre{white-space:pre-wrap;background:#0b1220;padding:12px;border-radius:12px}.muted{color:#9ca3af}
</style></head><body><div class='wrap'>
<div class='card'><h1>{{ detail['name'] }}</h1><p class='muted'>Track detail and current classification inputs.</p><a class='btn btn-alt' href='{{ url_for("admin_unmatched") }}'>Back</a> <a class='btn' href='{{ url_for("reclassify_route", spotify_track_id=detail["spotify_track_id"]) }}'>Reclassify now</a> <a class='btn' href='{{ url_for("enrich_one_route", spotify_track_id=detail["spotify_track_id"]) }}'>Search MusicBrainz now</a></div>
<div class='card'><h2>Current record</h2><pre>{{ pretty }}</pre></div>
</div></body></html>
"""


def get_access_token_for_user(spotify_user_id: str) -> str:
    row = get_token_row("spotify", spotify_user_id)
    if not row:
        raise RuntimeError("No Spotify refresh token stored for this user.")
    data = spotify_token_request(
        {"grant_type": "refresh_token", "refresh_token": row["refresh_token"]}
    )
    upsert_token(
        "spotify",
        spotify_user_id,
        refresh_token=data.get("refresh_token") or row["refresh_token"],
        access_token=data.get("access_token"),
        token_type=data.get("token_type"),
        scope=row["scope"] or data.get("scope"),
        expires_at=None,
    )
    return data["access_token"]


def get_musicbrainz_access_token() -> str | None:
    row = get_token_row("musicbrainz", "self")
    if not row:
        return None
    if row["access_token"]:
        return row["access_token"]
    if row["refresh_token"]:
        data = refresh_mb_token(row["refresh_token"])
        upsert_token(
            "musicbrainz",
            "self",
            refresh_token=data.get("refresh_token") or row["refresh_token"],
            access_token=data.get("access_token"),
            token_type=data.get("token_type"),
            scope=data.get("scope"),
            expires_at=None,
        )
        return data.get("access_token")
    return None


def current_profile():
    spotify_user_id = session.get("spotify_user_id")
    if not spotify_user_id:
        return None
    try:
        token = get_access_token_for_user(spotify_user_id)
        return get_me(token)
    except Exception as exc:
        logger.exception("current_profile failed: %s", exc)
        return None


def build_debug_snapshot(spotify_user_id=None):
    if not spotify_user_id:
        spotify_user_id = session.get("spotify_user_id")
    snap = {
        "env": {
            "redirect_uri": SPOTIFY_REDIRECT_URI,
            "playlist_public": PLAYLIST_PUBLIC,
            "requested_scopes": SCOPES,
            "client_id_present": bool(SPOTIFY_CLIENT_ID),
            "client_secret_present": bool(SPOTIFY_CLIENT_SECRET),
            "musicbrainz_client_id_present": bool(MUSICBRAINZ_CLIENT_ID),
            "musicbrainz_redirect_uri": MUSICBRAINZ_REDIRECT_URI,
            "mb_batch_size": MB_BATCH_SIZE,
            "mb_background_enabled": MB_BACKGROUND_ENABLED,
        },
        "session": {
            "spotify_user_id": spotify_user_id,
            "musicbrainz_connected": bool(get_musicbrainz_access_token()),
            "mb_cursor_session": session.get("mb_sync_cursor"),
        },
    }
    if spotify_user_id:
        counts = {
            "saved_tracks": get_db()
            .execute("SELECT COUNT(*) FROM saved_tracks")
            .fetchone()[0],
            "spotify_tracks": get_db()
            .execute("SELECT COUNT(*) FROM spotify_tracks")
            .fetchone()[0],
            "spotify_artists": get_db()
            .execute("SELECT COUNT(*) FROM spotify_artists")
            .fetchone()[0],
            "matched_tracks": get_db()
            .execute(
                "SELECT COUNT(*) FROM mb_track_matches WHERE status = 'matched'"
            )
            .fetchone()[0],
            "classified_genres": get_db()
            .execute("SELECT COUNT(*) FROM track_genres")
            .fetchone()[0],
            "override_tracks": get_db()
            .execute(
                "SELECT COUNT(DISTINCT spotify_track_id) FROM genre_overrides"
            )
            .fetchone()[0],
        }
        snap["db_counts"] = counts
    return json.dumps(snap, indent=2)


@app.before_request
def ensure_db():
    init_db()


@app.route("/")
def index():
    profile = current_profile()
    spotify_user_id = session.get("spotify_user_id")
    progress = None
    if spotify_user_id:
        # Prefer in‑memory cursor state if background batches are being used,
        # fall back to session cursor otherwise.
        cursor = MB_CURSOR_STATE.get(spotify_user_id, session.get("mb_sync_cursor"))
        pending = get_pending_musicbrainz_count(after_cursor=cursor)
        progress = {"pending": pending, "cursor": cursor}
    return render_template_string(
        PAGE,
        app_name=APP_NAME,
        connected=bool(profile),
        profile=profile,
        progress=progress,
        debug_enabled=DEBUG_MODE,
        message=session.pop("flash_message", None),
        result_rows=session.pop("latest_results", None),
        debug=session.get("debug_blob"),
    )


@app.route("/login")
def login():
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        session["flash_message"] = "Missing Spotify credentials in environment."
        return redirect(url_for("index"))
    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state
    return redirect(build_authorize_url(state))


@app.route("/callback")
def callback():
    try:
        if request.args.get("state") != session.get("oauth_state"):
            session["flash_message"] = "OAuth state mismatch."
            return redirect(url_for("index"))
        code = request.args.get("code")
        if not code:
            session["flash_message"] = "Spotify did not return an authorization code."
            return redirect(url_for("index"))
        data = spotify_token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": SPOTIFY_REDIRECT_URI,
            }
        )
        me = get_me(data["access_token"])
        upsert_token(
            "spotify",
            me["id"],
            refresh_token=data.get("refresh_token"),
            access_token=data.get("access_token"),
            token_type=data.get("token_type"),
            scope=data.get("scope", ""),
            expires_at=None,
        )
        session["spotify_user_id"] = me["id"]
        session["debug_blob"] = build_debug_snapshot(me["id"]) if DEBUG_MODE else None
        session["flash_message"] = (
            f"Connected Spotify account for {me.get('display_name') or me['id']}."
        )
        return redirect(url_for("index"))
    except Exception as exc:
        logger.exception("Spotify callback failed: %s", exc)
        session["flash_message"] = f"Spotify callback failed: {exc}"
        return redirect(url_for("index"))


@app.route("/musicbrainz/login")
def mb_login():
    if not MUSICBRAINZ_CLIENT_ID or not MUSICBRAINZ_REDIRECT_URI:
        session["flash_message"] = "Missing MusicBrainz OAuth environment variables."
        return redirect(url_for("index"))
    state = secrets.token_urlsafe(24)
    session["mb_oauth_state"] = state
    return redirect(build_mb_authorize_url(state))


@app.route("/musicbrainz/callback")
def mb_callback():
    try:
        if request.args.get("state") != session.get("mb_oauth_state"):
            session["flash_message"] = "MusicBrainz OAuth state mismatch."
            return redirect(url_for("index"))
        if request.args.get("error"):
            session["flash_message"] = (
                f"MusicBrainz authorization failed: {request.args.get('error')}"
            )
            return redirect(url_for("index"))
        code = request.args.get("code")
        if not code:
            session["flash_message"] = "MusicBrainz did not return an authorization code."
            return redirect(url_for("index"))
        token_data = exchange_mb_code(code)
        upsert_token(
            "musicbrainz",
            "self",
            refresh_token=token_data.get("refresh_token"),
            access_token=token_data.get("access_token"),
            token_type=token_data.get("token_type"),
            scope=token_data.get("scope"),
            expires_at=None,
        )
        session["flash_message"] = (
            "MusicBrainz OAuth connected. Public lookups work without it, "
            "but authenticated access is now available."
        )
        return redirect(url_for("index"))
    except Exception as exc:
        logger.exception("MusicBrainz callback failed: %s", exc)
        session["flash_message"] = f"MusicBrainz callback failed: {exc}"
        return redirect(url_for("index"))


@app.route("/run-sync")
def run_sync():
    spotify_user_id = session.get("spotify_user_id")
    if not spotify_user_id:
        session["flash_message"] = "Connect Spotify first."
        return redirect(url_for("index"))
    try:
        token = get_access_token_for_user(spotify_user_id)
        saved_summary = sync_saved_library(token, spotify_user_id)
        artist_summary = sync_artist_metadata(token)
        session["flash_message"] = (
            f"Synced {saved_summary['saved_tracks']} liked tracks and "
            f"updated {artist_summary['artists_updated']} artists."
        )
    except Exception as exc:
        logger.exception("run_sync failed: %s", exc)
        session["flash_message"] = f"Sync failed: {exc}"
    session["debug_blob"] = build_debug_snapshot(spotify_user_id) if DEBUG_MODE else None
    return redirect(url_for("index"))


@app.route("/run-mb-sync-next")
def run_mb_sync_next():
    spotify_user_id = session.get("spotify_user_id")
    if not spotify_user_id:
        session["flash_message"] = "Connect Spotify first."
        return redirect(url_for("index"))

    try:
        cursor = MB_CURSOR_STATE.get(spotify_user_id, session.get("mb_sync_cursor"))

        if MB_BACKGROUND_ENABLED:
            # Fire-and-forget background batch using a thread WITH app context.
            def worker(user_id: str, cursor_val: str | None):
                from app import app  # if this code is in a different module; otherwise not needed

                with app.app_context():
                    try:
                        summary = sync_next_musicbrainz_batch(
                            limit=MB_BATCH_SIZE, cursor=cursor_val
                        )
                        MB_CURSOR_STATE[user_id] = summary.get("next_cursor")
                        logger.info(
                            "Background MB batch for %s: %s",
                            user_id,
                            summary,
                        )
                    except Exception as exc_inner:
                        logger.exception(
                            "Background MB batch failed for %s: %s",
                            user_id,
                            exc_inner,
                        )

            threading.Thread(
                target=worker, args=(spotify_user_id, cursor), daemon=True
            ).start()
            session["flash_message"] = (
                "Queued MusicBrainz batch in background; refresh to see progress."
            )
        else:
            # Synchronous batch (safer, more predictable).
            summary = sync_next_musicbrainz_batch(
                limit=MB_BATCH_SIZE,
                cursor=cursor,
            )
            new_cursor = summary.get("next_cursor")
            MB_CURSOR_STATE[spotify_user_id] = new_cursor
            session["mb_sync_cursor"] = new_cursor
            session["flash_message"] = (
                f"MusicBrainz batch complete: enriched {summary['tracks_enriched']}, "
                f"failed {summary['tracks_failed']}, scanned {summary['tracks_scanned']}, "
                f"pending {summary['pending_remaining']} remaining."
            )
    except Exception as exc:
        logger.exception("run_mb_sync_next failed: %s", exc)
        session["flash_message"] = f"MusicBrainz batch failed: {exc}"

    session["debug_blob"] = build_debug_snapshot(spotify_user_id) if DEBUG_MODE else None
    return redirect(url_for("index"))


@app.route("/run-mb-sync-all")
def run_mb_sync_all():
    spotify_user_id = session.get("spotify_user_id")
    if not spotify_user_id:
        session["flash_message"] = "Connect Spotify first."
        return redirect(url_for("index"))
    try:
        summary = sync_all_musicbrainz(limit=100000)
        session["flash_message"] = (
            f"MusicBrainz sync complete: enriched {summary['tracks_enriched']} tracks, "
            f"failed {summary['tracks_failed']} tracks, scanned {summary['tracks_scanned']} total."
        )
    except Exception as exc:
        logger.exception("run_mb_sync_all failed: %s", exc)
        session["flash_message"] = f"MusicBrainz sync failed: {exc}"
    session["debug_blob"] = build_debug_snapshot(spotify_user_id) if DEBUG_MODE else None
    return redirect(url_for("index"))


@app.route("/reset-mb-cursor")
def reset_mb_cursor():
    spotify_user_id = session.get("spotify_user_id")
    if not spotify_user_id:
        session["flash_message"] = "Connect Spotify first."
        return redirect(url_for("index"))

    MB_CURSOR_STATE.pop(spotify_user_id, None)
    session.pop("mb_sync_cursor", None)
    pending_global = get_pending_musicbrainz_count(after_cursor=None)
    session["flash_message"] = (
        f"MusicBrainz cursor reset. Global pending tracks: {pending_global}."
    )
    session["debug_blob"] = build_debug_snapshot(spotify_user_id) if DEBUG_MODE else None
    return redirect(url_for("index"))


@app.route("/build-playlists")
def build_all_playlists():
    spotify_user_id = session.get("spotify_user_id")
    if not spotify_user_id:
        session["flash_message"] = "Connect Spotify first."
        return redirect(url_for("index"))
    try:
        token = get_access_token_for_user(spotify_user_id)
        profile = get_me(token)
        buckets = build_playlist_buckets(spotify_user_id)
        results = []
        results.extend(
            sync_bucket_playlists(
                token,
                profile,
                PLAYLIST_PREFIX,
                PLAYLIST_PUBLIC,
                "decade",
                buckets["decade"],
            )
        )
        results.extend(
            sync_bucket_playlists(
                token,
                profile,
                PLAYLIST_PREFIX,
                PLAYLIST_PUBLIC,
                "genre",
                buckets["genre"],
            )
        )
        results.extend(
            sync_bucket_playlists(
                token,
                profile,
                PLAYLIST_PREFIX,
                PLAYLIST_PUBLIC,
                "combo",
                buckets["combo"],
            )
        )
        session["latest_results"] = results
        session["flash_message"] = (
            f"Built {len(results)} playlists across decade, genre, and combo buckets."
        )
    except Exception as exc:
        logger.exception("build_all_playlists failed: %s", exc)
        session["flash_message"] = f"Playlist build failed: {exc}"
    session["debug_blob"] = build_debug_snapshot(spotify_user_id) if DEBUG_MODE else None
    return redirect(url_for("index"))


@app.route("/admin/unmatched")
def admin_unmatched():
    spotify_user_id = session.get("spotify_user_id")
    if not spotify_user_id:
        session["flash_message"] = "Connect Spotify first."
        return redirect(url_for("index"))
    rows = []
    for row in get_unmatched_tracks():
        row = dict(row)
        row["artist_names"] = " / ".join(
            (row.get("artist_names") or "").split("|||")
        ) if row.get("artist_names") else ""
        row["override_csv"] = ", ".join(
            get_track_override_genres(row["spotify_track_id"])
        )
        rows.append(row)
    return render_template_string(
        ADMIN_PAGE,
        app_name=APP_NAME,
        rows=rows,
        message=session.pop("flash_message", None),
    )


@app.post("/admin/enrich-selected")
def enrich_selected_route():
    track_ids = request.form.getlist("track_ids")
    if not track_ids:
        session["flash_message"] = "No tracks selected."
        return redirect(url_for("admin_unmatched"))
    summary = enrich_selected_tracks(track_ids)
    session["flash_message"] = (
        f"Selected enrichment complete: enriched {summary['tracks_enriched']}, "
        f"failed {summary['tracks_failed']}."
    )
    return redirect(url_for("admin_unmatched"))


@app.route("/admin/track/<spotify_track_id>")
def track_detail(spotify_track_id):
    detail = get_track_detail(spotify_track_id)
    if not detail:
        session["flash_message"] = "Track not found."
        return redirect(url_for("admin_unmatched"))
    return render_template_string(
        DETAIL_PAGE,
        app_name=APP_NAME,
        detail=detail,
        pretty=json.dumps(dict(detail), indent=2),
    )


@app.post("/admin/track/<spotify_track_id>/override")
def save_override(spotify_track_id):
    genres_csv = request.form.get("genres", "")
    genres = [g.strip() for g in genres_csv.split(",") if g.strip()]
    replace_genre_overrides(spotify_track_id, genres)
    reclassify_track(spotify_track_id)
    session["flash_message"] = (
        f"Saved {len(genres)} override genre(s) for {spotify_track_id}."
    )
    return redirect(url_for("admin_unmatched"))


@app.route("/admin/track/<spotify_track_id>/reclassify")
def reclassify_route(spotify_track_id):
    result = reclassify_track(spotify_track_id)
    session["flash_message"] = (
        f"Reclassified {spotify_track_id} into "
        f"{', '.join([g[0] for g in result['genres']]) or 'no genres'}."
    )
    return redirect(url_for("track_detail", spotify_track_id=spotify_track_id))


@app.route("/admin/track/<spotify_track_id>/enrich")
def enrich_one_route(spotify_track_id):
    summary = enrich_selected_tracks([spotify_track_id])
    session["flash_message"] = (
        f"Track enrichment complete: enriched {summary['tracks_enriched']}, "
        f"failed {summary['tracks_failed']}."
    )
    return redirect(url_for("track_detail", spotify_track_id=spotify_track_id))


@app.route("/logout")
def logout():
    session.clear()
    session["flash_message"] = "Session cleared."
    return redirect(url_for("index"))


@app.route("/debug/spotify-config")
def debug_spotify_config():
    return jsonify(
        {
            "redirect_uri": SPOTIFY_REDIRECT_URI,
            "client_id_present": bool(SPOTIFY_CLIENT_ID),
            "client_secret_present": bool(SPOTIFY_CLIENT_SECRET),
            "playlist_public": PLAYLIST_PUBLIC,
            "requested_scopes": SCOPES,
            "mb_batch_size": MB_BATCH_SIZE,
            "mb_background_enabled": MB_BACKGROUND_ENABLED,
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=DEBUG_MODE)