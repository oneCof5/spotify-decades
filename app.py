import base64
import json
import logging
import os
import secrets
import sqlite3
import time
from collections import defaultdict
from urllib.parse import urlencode

import requests
from flask import Flask, g, redirect, render_template_string, request, session, url_for

# ---- Config ----

APP_NAME = os.getenv("APP_NAME", "Spotify Decades")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://localhost:8080/callback")
SECRET_KEY = os.getenv("FLASK_SECRET_KEY", secrets.token_hex(32))
DB_PATH = os.getenv("DATABASE_PATH", "/data/spotify_decades.db")
PLAYLIST_PREFIX = os.getenv("PLAYLIST_PREFIX", "My")
PLAYLIST_PUBLIC = os.getenv("PLAYLIST_PUBLIC", "false").lower() == "true"
PORT = int(os.getenv("PORT", "8080"))
DEBUG_MODE = os.getenv("APP_DEBUG", "true").lower() == "true"
LOG_PATH = os.getenv("LOG_PATH", "/logs/app.log")

SCOPES = (
    "user-library-read "
    "user-read-private "
    "playlist-read-private "
    "playlist-modify-private "
    "playlist-modify-public"
)
SPOTIFY_ACCOUNTS = "https://accounts.spotify.com"
SPOTIFY_API = "https://api.spotify.com/v1"

# ---- Logging (stdout + /logs/app.log) ----

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s in %(module)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ---- Flask app ----

app = Flask(__name__)
app.secret_key = SECRET_KEY

PAGE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ app_name }}</title>
  <style>
    :root { color-scheme: light dark; --bg:#0f1115; --surface:#171a20; --muted:#a7b0be; --text:#f3f6fb; --accent:#1db954; --accent2:#15883d; --border:#2a2f38; --danger:#ff6b6b; --warning:#f4c95d; --ok:#79d48f; }
    @media (prefers-color-scheme: light) { :root { --bg:#f6f8fb; --surface:#ffffff; --muted:#566072; --text:#111827; --accent:#1db954; --accent2:#168f42; --border:#dbe2ea; --danger:#c0392b; --warning:#9a6b00; --ok:#1f7a36; } }
    * { box-sizing: border-box; }
    body { margin:0; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); }
    .wrap { max-width: 1080px; margin: 0 auto; padding: 28px 18px 56px; }
    .hero,.card { background: var(--surface); border:1px solid var(--border); border-radius:18px; box-shadow:0 10px 30px rgba(0,0,0,.08); }
    .hero { padding:28px; margin-bottom:20px; }
    .card { padding:20px; margin-bottom:16px; }
    h1,h2,h3 { margin:0 0 12px; line-height:1.15; }
    p { margin:0 0 12px; color: var(--muted); }
    .actions { display:flex; gap:12px; flex-wrap:wrap; margin-top:16px; }
    .btn { display:inline-flex; align-items:center; justify-content:center; min-height:44px; padding:0 16px; border-radius:12px; text-decoration:none; border:1px solid var(--border); color:var(--text); font-weight:600; }
    .btn-primary { background:var(--accent); color:#08130c; border-color:transparent; }
    .btn-primary:hover { background:var(--accent2); }
    .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:16px; }
    .pill { display:inline-block; padding:6px 10px; border-radius:999px; background:rgba(29,185,84,.14); font-size:14px; }
    .kv { display:grid; grid-template-columns:220px 1fr; gap:8px 12px; }
    .kv div { padding:8px 0; border-bottom:1px solid var(--border); }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; word-break: break-all; }
    .msg-ok { color:var(--ok); }
    .msg-warn { color:var(--warning); }
    .msg-err { color:var(--danger); }
    table { width:100%; border-collapse:collapse; }
    th,td { text-align:left; padding:10px 8px; border-bottom:1px solid var(--border); vertical-align:top; }
    th { color:var(--text); }
    .small { font-size:14px; }
    pre { background:#0b0d11; color:#d6e2f0; border-radius:14px; padding:14px; overflow:auto; border:1px solid var(--border); font-size:13px; }
    code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <span class="pill">Spotify Web API</span>
      <h1>{{ app_name }}</h1>
      <p>Connect your Spotify account, scan your liked songs, and sync playlists grouped by decade using a hosted redirect URL.</p>
      <div class="actions">
        {% if not connected %}
          <a class="btn btn-primary" href="{{ url_for('login') }}">Connect Spotify</a>
        {% else %}
          <a class="btn btn-primary" href="{{ url_for('build_playlists') }}">Sync decade playlists</a>
          <a class="btn" href="{{ url_for('debug_info') }}">Refresh debug info</a>
          <a class="btn" href="{{ url_for('logout') }}">Disconnect</a>
          <a class="btn" href="{{ url_for('reset_tokens') }}">Reset stored token</a>
        {% endif %}
      </div>
    </section>

    {% if message %}
    <section class="card">
      <h2>Status</h2>
      <p class="{{ message_class }}">{{ message }}</p>
    </section>
    {% endif %}

    <section class="grid">
      <article class="card">
        <h2>Deployment</h2>
        <div class="kv small">
          <div>Redirect URI</div><div class="mono">{{ redirect_uri }}</div>
          <div>Playlist visibility</div><div>{{ 'Public' if playlist_public else 'Private' }}</div>
          <div>Scopes requested</div><div class="mono">{{ scopes }}</div>
          <div>Debug mode</div><div>{{ debug_enabled }}</div>
        </div>
      </article>
      <article class="card">
        <h2>Checks</h2>
        <ul>
          <li>Uses <code>/me/playlists</code> and <code>/playlists/{id}/items</code> for idempotent sync.</li>
          <li>Existing decade playlists are updated instead of duplicated.</li>
          <li>Debug panel shows scopes, account, and playlist create/add tests.</li>
        </ul>
      </article>
    </section>

    {% if connected and profile %}
    <section class="card">
      <h2>Connected account</h2>
      <div class="kv">
        <div>User</div><div>{{ profile.get('display_name') or profile.get('id') }}</div>
        <div>Spotify ID</div><div class="mono">{{ profile.get('id') }}</div>
        <div>Product</div><div>{{ profile.get('product', 'Unavailable') }}</div>
        <div>Email</div><div>{{ profile.get('email', 'Not shared') }}</div>
      </div>
    </section>
    {% endif %}

    {% if result_rows %}
    <section class="card">
      <h2>Latest run</h2>
      <table>
        <thead>
          <tr><th>Decade</th><th>Tracks</th><th>Action</th><th>Playlist</th></tr>
        </thead>
        <tbody>
          {% for row in result_rows %}
          <tr>
            <td>{{ row['decade'] }}</td>
            <td>{{ row['count'] }}</td>
            <td>{{ row['action'] }}</td>
            <td><a href="{{ row['url'] }}" target="_blank" rel="noopener noreferrer">{{ row['name'] }}</a></td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </section>
    {% endif %}

    {% if debug and debug_enabled %}
    <section class="card">
      <h2>Debug</h2>
      <pre>{{ debug }}</pre>
    </section>
    {% endif %}
  </div>
</body>
</html>
"""

# ---- DB helpers ----

def get_db():
    if "db" not in g:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.execute(
#if not suitable, I'll just provide the app.py patch with instructions—no external references.

        "CREATE TABLE IF NOT EXISTS tokens ("
        "spotify_user_id TEXT PRIMARY KEY,"
        "refresh_token TEXT NOT NULL,"
        "scope TEXT,"
        "created_at INTEGER NOT NULL)"
    )
    db.commit()

# ---- Spotify helpers ----

def spotify_token_request(data: dict) -> dict:
    auth = base64.b64encode(f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()).decode()
    resp = requests.post(
        f"{SPOTIFY_ACCOUNTS}/api/token",
        data=data,
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def spotify_request(method: str, path: str, access_token: str, params=None, payload=None):
    url = path if path.startswith("http") else f"{SPOTIFY_API}{path}"
    logger.info("Spotify %s %s", method, url)
    return requests.request(
        method,
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        params=params,
        json=payload,
        timeout=30,
    )


def parse_json(resp):
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text}


def get_stored_token_row(spotify_user_id: str):
    return (
        get_db()
        .execute(
            "SELECT spotify_user_id, refresh_token, scope, created_at "
            "FROM tokens WHERE spotify_user_id = ?",
            (spotify_user_id,),
        )
        .fetchone()
    )


def get_access_token_for_user(spotify_user_id: str) -> str:
    row = get_stored_token_row(spotify_user_id)
    if not row:
        raise RuntimeError("No refresh token stored for this user.")
    data = spotify_token_request(
        {"grant_type": "refresh_token", "refresh_token": row["refresh_token"]}
    )
    if data.get("refresh_token"):
        get_db().execute(
            "UPDATE tokens SET refresh_token = ? WHERE spotify_user_id = ?",
            (data["refresh_token"], spotify_user_id),
        )
        get_db().commit()
    return data["access_token"]


def current_profile():
    spotify_user_id = session.get("spotify_user_id")
    if not spotify_user_id:
        return None
    try:
        token = get_access_token_for_user(spotify_user_id)
        resp = spotify_request("GET", "/me", token)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.exception("current_profile failed: %s", exc)
        return None


def all_saved_tracks(access_token: str):
    items = []
    path = "/me/tracks"
    params = {"limit": 50}
    while path:
        resp = spotify_request("GET", path, access_token, params=params)
        resp.raise_for_status()
        data = resp.json()
        items.extend(data.get("items", []))
        next_url = data.get("next")
        path = next_url if next_url else None
        params = None
    return items

# ---- Decade grouping (simple year only) ----

def decade_from_year(year: int) -> str:
    return f"{year // 10 * 10}s"


def infer_original_year(track: dict):
    """Simplified: use album.release_date year only, no /search calls."""
    album = track.get("album") or {}
    release_date = album.get("release_date")
    if release_date and len(release_date) >= 4 and release_date[:4].isdigit():
        return int(release_date[:4])
    return None


def group_tracks(saved_items):
    decades = defaultdict(list)
    seen = set()
    for item in saved_items:
        track = item.get("track") or {}
        if track.get("is_local") or not track.get("id") or not track.get("uri"):
            continue
        if track["id"] in seen:
            continue
        year = infer_original_year(track)
        if year is None:
            continue
        decades[decade_from_year(year)].append(track["uri"])
        seen.add(track["id"])
    return dict(sorted(decades.items()))

# ---- Playlist sync (idempotent create/update) ----

def get_current_user_playlists(access_token: str, owner_id: str):
    playlists = []
    path = "/me/playlists"
    params = {"limit": 50}
    while path:
        resp = spotify_request("GET", path, access_token, params=params)
        resp.raise_for_status()
        data = resp.json()
        for item in data.get("items", []):
            owner = item.get("owner") or {}
            if owner.get("id") == owner_id:
                playlists.append(item)
        next_url = data.get("next")
        path = next_url if next_url else None
        params = None
    return playlists


def find_existing_playlists(access_token: str, owner_id: str):
    return {
        (playlist.get("name") or "").strip(): playlist
        for playlist in get_current_user_playlists(access_token, owner_id)
    }


def update_playlist_details(access_token: str, playlist_id: str, name: str, description: str):
    resp = spotify_request(
        "PUT",
        f"/playlists/{playlist_id}",
        access_token,
        payload={"name": name, "public": PLAYLIST_PUBLIC, "description": description},
    )
    if resp.status_code >= 400:
        raise requests.HTTPError(response=resp)


def replace_playlist_items(access_token: str, playlist_id: str, uris: list[str]):
    # Replace all items with first batch (up to 100)
    first_chunk = uris[:100]
    resp = spotify_request(
        "PUT",
        f"/playlists/{playlist_id}/items",
        access_token,
        payload={"uris": first_chunk},
    )
    if resp.status_code >= 400:
        raise requests.HTTPError(response=resp)
    # Append remaining items
    for i in range(100, len(uris), 100):
        add_resp = spotify_request(
            "POST",
            f"/playlists/{playlist_id}/items",
            access_token,
            payload={"uris": uris[i : i + 100]},
        )
        if add_resp.status_code >= 400:
            raise requests.HTTPError(response=add_resp)


def create_or_update_decade_playlists(access_token: str, profile: dict, grouped: dict):
    results = []
    owner_id = profile["id"]
    existing = find_existing_playlists(access_token, owner_id)
    for decade, uris in grouped.items():
        if not uris:
            continue
        playlist_name = f"{PLAYLIST_PREFIX} {decade}"
        description = "Auto-generated from liked songs by album release decade."
        playlist = existing.get(playlist_name)
        action = "updated"
        if playlist:
            update_playlist_details(access_token, playlist["id"], playlist_name, description)
        else:
            resp = spotify_request(
                "POST",
                "/me/playlists",
                access_token,
                payload={
                    "name": playlist_name,
                    "public": PLAYLIST_PUBLIC,
                    "description": description,
                },
            )
            if resp.status_code >= 400:
                raise requests.HTTPError(response=resp)
            playlist = resp.json()
            action = "created"
        replace_playlist_items(access_token, playlist["id"], uris)
        results.append(
            {
                "decade": decade,
                "count": len(uris),
                "name": playlist_name,
                "url": playlist.get("external_urls", {}).get("spotify", "#"),
                "action": action,
            }
        )
    return results

# ---- Debug snapshot (no year diagnostics) ----

def build_debug_snapshot(spotify_user_id=None):
    snap = {
        "env": {
            "redirect_uri": SPOTIFY_REDIRECT_URI,
            "playlist_public": PLAYLIST_PUBLIC,
            "requested_scopes": SCOPES,
            "client_id_present": bool(SPOTIFY_CLIENT_ID),
            "client_secret_present": bool(SPOTIFY_CLIENT_SECRET),
        },
        "session": {
            "spotify_user_id": session.get("spotify_user_id"),
            "oauth_state_present": bool(session.get("oauth_state")),
        },
    }
    if not spotify_user_id:
        spotify_user_id = session.get("spotify_user_id")
    if spotify_user_id:
        row = get_stored_token_row(spotify_user_id)
        if row:
            snap["db"] = {
                "spotify_user_id": row["spotify_user_id"],
                "stored_scope": row["scope"],
                "created_at_epoch": row["created_at"],
            }
        try:
            access_token = get_access_token_for_user(spotify_user_id)
            me_resp = spotify_request("GET", "/me", access_token)
            snap["api_me_status"] = me_resp.status_code
            snap["api_me_body"] = parse_json(me_resp)
            test_create = spotify_request(
                "POST",
                "/me/playlists",
                access_token,
                payload={
                    "name": f"{PLAYLIST_PREFIX} debug test",
                    "public": False,
                    "description": "Temporary test playlist for diagnostics.",
                },
            )
            snap["playlist_create_status"] = test_create.status_code
            snap["playlist_create_body"] = parse_json(test_create)
            if test_create.status_code < 400:
                created = test_create.json()
                snap["playlist_create_success_id"] = created.get("id")
                test_add = spotify_request(
                    "POST", f"/playlists/{created['id']}/items", access_token, payload={"uris": []}
                )
                snap["playlist_add_items_status"] = test_add.status_code
                snap["playlist_add_items_body"] = parse_json(test_add)
        except requests.HTTPError as exc:
            snap["error"] = {
                "status_code": exc.response.status_code if exc.response is not None else None,
                "body": parse_json(exc.response) if exc.response is not None else str(exc),
            }
        except Exception as exc:
            snap["error"] = {"message": str(exc)}
    return json.dumps(snap, indent=2)

# ---- Routes ----

@app.before_request
def ensure_db():
    init_db()


@app.route("/")
def index():
    profile = current_profile()
    return render_template_string(
        PAGE,
        app_name=APP_NAME,
        connected=bool(profile),
        profile=profile,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        playlist_public=PLAYLIST_PUBLIC,
        scopes=SCOPES,
        debug_enabled=DEBUG_MODE,
        message=session.pop("flash_message", None),
        message_class=session.pop("flash_class", ""),
        result_rows=session.pop("latest_results", None),
        debug=session.get("debug_blob"),
    )


@app.route("/login")
def login():
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        session["flash_message"] = "Missing Spotify client credentials in the container environment."
        session["flash_class"] = "msg-err"
        return redirect(url_for("index"))
    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state
    params = {
        "client_id": SPOTIFY_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": SPOTIFY_REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
        "show_dialog": "true",
    }
    return redirect(f"{SPOTIFY_ACCOUNTS}/authorize?{urlencode(params)}")


@app.route("/callback")
def callback():
    if request.args.get("error"):
        session["flash_message"] = f"Spotify authorization failed: {request.args['error']}"
        session["flash_class"] = "msg-err"
        return redirect(url_for("index"))
    if request.args.get("state") != session.get("oauth_state"):
        session["flash_message"] = "OAuth state mismatch. Login was rejected for safety."
        session["flash_class"] = "msg-err"
        return redirect(url_for("index"))
    code = request.args.get("code")
    if not code:
        session["flash_message"] = "Spotify did not return an authorization code."
        session["flash_class"] = "msg-err"
        return redirect(url_for("index"))
    data = spotify_token_request(
        {"grant_type": "authorization_code", "code": code, "redirect_uri": SPOTIFY_REDIRECT_URI}
    )
    access_token = data["access_token"]
    refresh_token = data.get("refresh_token")
    me_resp = spotify_request("GET", "/me", access_token)
    me_resp.raise_for_status()
    me = me_resp.json()
    if not refresh_token:
        session["flash_message"] = "Spotify did not return a refresh token."
        session["flash_class"] = "msg-err"
        return redirect(url_for("index"))
    get_db().execute(
        "INSERT INTO tokens (spotify_user_id, refresh_token, scope, created_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(spotify_user_id) DO UPDATE SET "
        "refresh_token = excluded.refresh_token, "
        "scope = excluded.scope, "
        "created_at = excluded.created_at",
        (me["id"], refresh_token, data.get("scope", ""), int(time.time())),
    )
    get_db().commit()
    session["spotify_user_id"] = me["id"]
    session["debug_blob"] = build_debug_snapshot(me["id"]) if DEBUG_MODE else None
    session["flash_message"] = f"Connected Spotify account for {me.get('display_name') or me['id']}."
    session["flash_class"] = "msg-ok"
    return redirect(url_for("index"))


@app.route("/build-playlists")
def build_playlists():
    spotify_user_id = session.get("spotify_user_id")
    if not spotify_user_id:
        session["flash_message"] = "Connect Spotify first."
        session["flash_class"] = "msg-warn"
        return redirect(url_for("index"))
    try:
        access_token = get_access_token_for_user(spotify_user_id)
        profile_resp = spotify_request("GET", "/me", access_token)
        profile_resp.raise_for_status()
        profile = profile_resp.json()
        saved = all_saved_tracks(access_token)
        grouped = group_tracks(saved)
        results = create_or_update_decade_playlists(access_token, profile, grouped)
        session["latest_results"] = results
        session["flash_message"] = f"Processed {len(results)} playlist(s) from {len(saved)} liked tracks scanned."
        session["flash_class"] = "msg-ok"
    except requests.HTTPError as exc:
        body = parse_json(exc.response) if exc.response is not None else str(exc)
        code = exc.response.status_code if exc.response is not None else "unknown"
        logger.exception("Spotify API error during build_playlists: %s", body)
        session["flash_message"] = f"Spotify API error: {code} {json.dumps(body)}"
        session["flash_class"] = "msg-err"
    except Exception as exc:
        logger.exception("build_playlists failed: %s", exc)
        session["flash_message"] = f"Run failed: {exc}"
        session["flash_class"] = "msg-err"
    session["debug_blob"] = build_debug_snapshot(spotify_user_id) if DEBUG_MODE else None
    return redirect(url_for("index"))


@app.route("/debug")
def debug_info():
    spotify_user_id = session.get("spotify_user_id")
    session["debug_blob"] = build_debug_snapshot(spotify_user_id) if DEBUG_MODE else "Debug disabled"
    session["flash_message"] = "Debug snapshot refreshed."
    session["flash_class"] = "msg-ok"
    return redirect(url_for("index"))


@app.route("/reset-tokens")
def reset_tokens():
    spotify_user_id = session.get("spotify_user_id")
    if spotify_user_id:
        get_db().execute("DELETE FROM tokens WHERE spotify_user_id = ?", (spotify_user_id,))
        get_db().commit()
    session.clear()
    session["flash_message"] = "Stored token deleted. Log in again to authorize a fresh token."
    session["flash_class"] = "msg-warn"
    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    session.clear()
    session["flash_message"] = "Session cleared. Stored refresh tokens remain unless reset separately."
    session["flash_class"] = "msg-warn"
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)