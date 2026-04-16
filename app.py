import base64
import os
import secrets
import sqlite3
import time
from collections import defaultdict
from urllib.parse import urlencode

import requests
from flask import Flask, g, redirect, render_template_string, request, session, url_for

APP_NAME = os.getenv("APP_NAME", "Spotify Decades")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://localhost:8080/callback")
SECRET_KEY = os.getenv("FLASK_SECRET_KEY", secrets.token_hex(32))
DB_PATH = os.getenv("DATABASE_PATH", "/data/spotify_decades.db")
PLAYLIST_PREFIX = os.getenv("PLAYLIST_PREFIX", "My")
PLAYLIST_PUBLIC = os.getenv("PLAYLIST_PUBLIC", "false").lower() == "true"
PORT = int(os.getenv("PORT", "8080"))

SCOPES = "user-library-read playlist-modify-private playlist-modify-public"
SPOTIFY_ACCOUNTS = "https://accounts.spotify.com"
SPOTIFY_API = "https://api.spotify.com/v1"

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
    :root {
      color-scheme: light dark;
      --bg: #0f1115;
      --surface: #171a20;
      --muted: #a7b0be;
      --text: #f3f6fb;
      --accent: #1db954;
      --accent-2: #15883d;
      --border: #2a2f38;
      --danger: #ff6b6b;
      --warning: #f4c95d;
      --ok: #79d48f;
    }
    @media (prefers-color-scheme: light) {
      :root {
        --bg: #f6f8fb;
        --surface: #ffffff;
        --muted: #566072;
        --text: #111827;
        --accent: #1db954;
        --accent-2: #168f42;
        --border: #dbe2ea;
        --danger: #c0392b;
        --warning: #9a6b00;
        --ok: #1f7a36;
      }
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Inter, system-ui, sans-serif;
      background: linear-gradient(180deg, var(--bg), color-mix(in srgb, var(--bg) 86%, #000 14%));
      color: var(--text);
    }
    .wrap { max-width: 980px; margin: 0 auto; padding: 32px 20px 64px; }
    .hero, .card {
      background: color-mix(in srgb, var(--surface) 94%, transparent);
      border: 1px solid var(--border);
      border-radius: 18px;
      box-shadow: 0 10px 40px rgba(0,0,0,.12);
    }
    .hero { padding: 28px; margin-bottom: 24px; }
    .card { padding: 22px; margin-bottom: 18px; }
    h1,h2,h3 { margin: 0 0 12px; line-height: 1.15; }
    p { margin: 0 0 12px; color: var(--muted); }
    .actions { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 16px; }
    .btn {
      display: inline-flex; align-items: center; justify-content: center;
      min-height: 44px; padding: 0 16px; border-radius: 12px; text-decoration: none;
      border: 1px solid var(--border); color: var(--text); font-weight: 600;
    }
    .btn-primary { background: var(--accent); color: #08130c; border-color: transparent; }
    .btn-primary:hover { background: var(--accent-2); }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 16px; }
    .pill { display: inline-block; padding: 6px 10px; border-radius: 999px; background: rgba(29,185,84,.14); color: var(--text); font-size: 14px; }
    .kv { display: grid; grid-template-columns: 180px 1fr; gap: 8px 12px; }
    .kv div { padding: 8px 0; border-bottom: 1px solid var(--border); }
    .mono { font-family: ui-monospace, SFMono-Regular, monospace; word-break: break-all; }
    .msg-ok { color: var(--ok); }
    .msg-warn { color: var(--warning); }
    .msg-err { color: var(--danger); }
    ul { margin: 8px 0 0 18px; color: var(--muted); }
    table { width: 100%; border-collapse: collapse; margin-top: 10px; }
    th, td { text-align: left; padding: 10px 8px; border-bottom: 1px solid var(--border); }
    th { color: var(--text); }
    .small { font-size: 14px; }
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <span class="pill">Spotify Web API</span>
      <h1>{{ app_name }}</h1>
      <p>Connect your Spotify account, scan your liked songs, and build playlists grouped by release decade using a hosted redirect URL.</p>
      {% if not connected %}
      <div class="actions">
        <a class="btn btn-primary" href="{{ url_for('login') }}">Connect Spotify</a>
      </div>
      {% else %}
      <div class="actions">
        <a class="btn btn-primary" href="{{ url_for('build_playlists') }}">Create decade playlists</a>
        <a class="btn" href="{{ url_for('logout') }}">Disconnect</a>
      </div>
      {% endif %}
    </section>

    {% if message %}
    <section class="card">
      <h2>Status</h2>
      <p class="{{ message_class }}">{{ message }}</p>
    </section>
    {% endif %}

    <section class="grid">
      <article class="card">
        <h2>Deployment values</h2>
        <div class="kv small">
          <div>Redirect URI</div><div class="mono">{{ redirect_uri }}</div>
          <div>Playlist visibility</div><div>{{ 'Public' if playlist_public else 'Private' }}</div>
          <div>Scopes</div><div class="mono">{{ scopes }}</div>
        </div>
      </article>

      <article class="card">
        <h2>How it works</h2>
        <ul>
          <li>Uses Authorization Code flow and checks OAuth state.</li>
          <li>Stores refresh tokens in SQLite inside the container volume.</li>
          <li>Refreshes access tokens automatically before API calls.</li>
          <li>Creates one playlist per decade from your liked songs.</li>
        </ul>
      </article>
    </section>

    {% if connected and profile %}
    <section class="card">
      <h2>Connected account</h2>
      <div class="kv">
        <div>User</div><div>{{ profile.get('display_name') or profile.get('id') }}</div>
        <div>Spotify ID</div><div class="mono">{{ profile.get('id') }}</div>
        <div>Email</div><div>{{ profile.get('email', 'Not shared') }}</div>
      </div>
    </section>
    {% endif %}

    {% if result_rows %}
    <section class="card">
      <h2>Latest run</h2>
      <table>
        <thead><tr><th>Decade</th><th>Tracks</th><th>Playlist</th></tr></thead>
        <tbody>
        {% for row in result_rows %}
          <tr>
            <td>{{ row['decade'] }}</td>
            <td>{{ row['count'] }}</td>
            <td><a href="{{ row['url'] }}" target="_blank" rel="noopener noreferrer">{{ row['name'] }}</a></td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
    </section>
    {% endif %}
  </div>
</body>
</html>
"""

def get_db():
    if 'db' not in g:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exc):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    db = get_db()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS tokens (
            spotify_user_id TEXT PRIMARY KEY,
            refresh_token TEXT NOT NULL,
            scope TEXT,
            created_at INTEGER NOT NULL
        )
        """
    )
    db.commit()

def spotify_token_request(data):
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

def spotify_get(path, access_token, params=None):
    resp = requests.get(
        f"{SPOTIFY_API}{path}",
        headers={"Authorization": f"Bearer {access_token}"},
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()

def spotify_post(path, access_token, payload=None):
    resp = requests.post(
        f"{SPOTIFY_API}{path}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json() if resp.text else {}

def get_access_token_for_user(spotify_user_id):
    row = get_db().execute(
        'SELECT refresh_token FROM tokens WHERE spotify_user_id = ?',
        (spotify_user_id,),
    ).fetchone()
    if not row:
        raise RuntimeError('No refresh token stored for this user.')
    data = spotify_token_request({
        'grant_type': 'refresh_token',
        'refresh_token': row['refresh_token'],
    })
    if data.get('refresh_token'):
        get_db().execute(
            'UPDATE tokens SET refresh_token = ? WHERE spotify_user_id = ?',
            (data['refresh_token'], spotify_user_id),
        )
        get_db().commit()
    return data['access_token']

def current_profile():
    spotify_user_id = session.get('spotify_user_id')
    if not spotify_user_id:
        return None
    try:
        token = get_access_token_for_user(spotify_user_id)
        return spotify_get('/me', token)
    except Exception:
        return None

def all_saved_tracks(access_token):
    items = []
    path = '/me/tracks'
    params = {'limit': 50}
    while path:
        data = spotify_get(path, access_token, params=params)
        items.extend(data.get('items', []))
        next_url = data.get('next')
        if next_url:
            path = next_url.replace(SPOTIFY_API, '')
            params = None
        else:
            path = None
    return items

def decade_from_release_date(release_date):
    year = int(release_date[:4])
    return f"{year // 10 * 10}s"

def group_tracks(saved_items):
    decades = defaultdict(list)
    seen = set()
    for item in saved_items:
        track = item.get('track') or {}
        if track.get('is_local') or not track.get('id') or not track.get('uri'):
            continue
        if track['id'] in seen:
            continue
        album = track.get('album') or {}
        release_date = album.get('release_date')
        if not release_date or len(release_date) < 4 or not release_date[:4].isdigit():
            continue
        decades[decade_from_release_date(release_date)].append(track['uri'])
        seen.add(track['id'])
    return dict(sorted(decades.items()))

def create_or_replace_decade_playlists(access_token, profile, grouped):
    results = []
    user_id = profile['id']
    for decade, uris in grouped.items():
        if not uris:
            continue
        playlist = spotify_post(f'/users/{user_id}/playlists', access_token, {
            'name': f'{PLAYLIST_PREFIX} {decade}',
            'public': PLAYLIST_PUBLIC,
            'description': 'Auto-generated from liked songs by album release decade.',
        })
        for i in range(0, len(uris), 100):
            spotify_post(f"/playlists/{playlist['id']}/tracks", access_token, {'uris': uris[i:i+100]})
        results.append({
            'decade': decade,
            'count': len(uris),
            'name': playlist['name'],
            'url': playlist.get('external_urls', {}).get('spotify', '#'),
        })
    return results

@app.before_request
def ensure_db():
    init_db()

@app.route('/')
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
        message=session.pop('flash_message', None),
        message_class=session.pop('flash_class', ''),
        result_rows=session.pop('latest_results', None),
    )

@app.route('/login')
def login():
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        session['flash_message'] = 'Missing Spotify client credentials in the container environment.'
        session['flash_class'] = 'msg-err'
        return redirect(url_for('index'))
    state = secrets.token_urlsafe(24)
    session['oauth_state'] = state
    params = {
        'client_id': SPOTIFY_CLIENT_ID,
        'response_type': 'code',
        'redirect_uri': SPOTIFY_REDIRECT_URI,
        'scope': SCOPES,
        'state': state,
        'show_dialog': 'true',
    }
    return redirect(f"{SPOTIFY_ACCOUNTS}/authorize?{urlencode(params)}")

@app.route('/callback')
def callback():
    if request.args.get('error'):
        session['flash_message'] = f"Spotify authorization failed: {request.args['error']}"
        session['flash_class'] = 'msg-err'
        return redirect(url_for('index'))
    if request.args.get('state') != session.get('oauth_state'):
        session['flash_message'] = 'OAuth state mismatch. Login was rejected for safety.'
        session['flash_class'] = 'msg-err'
        return redirect(url_for('index'))
    code = request.args.get('code')
    if not code:
        session['flash_message'] = 'Spotify did not return an authorization code.'
        session['flash_class'] = 'msg-err'
        return redirect(url_for('index'))
    data = spotify_token_request({
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': SPOTIFY_REDIRECT_URI,
    })
    access_token = data['access_token']
    refresh_token = data.get('refresh_token')
    me = spotify_get('/me', access_token)
    if not refresh_token:
        session['flash_message'] = 'Spotify did not return a refresh token.'
        session['flash_class'] = 'msg-err'
        return redirect(url_for('index'))
    get_db().execute(
        """INSERT INTO tokens (spotify_user_id, refresh_token, scope, created_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(spotify_user_id) DO UPDATE SET
             refresh_token = excluded.refresh_token,
             scope = excluded.scope,
             created_at = excluded.created_at""",
        (me['id'], refresh_token, data.get('scope', ''), int(time.time())),
    )
    get_db().commit()
    session['spotify_user_id'] = me['id']
    session['flash_message'] = f"Connected Spotify account for {me.get('display_name') or me['id']}."
    session['flash_class'] = 'msg-ok'
    return redirect(url_for('index'))

@app.route('/build-playlists')
def build_playlists():
    spotify_user_id = session.get('spotify_user_id')
    if not spotify_user_id:
        session['flash_message'] = 'Connect Spotify first.'
        session['flash_class'] = 'msg-warn'
        return redirect(url_for('index'))
    try:
        access_token = get_access_token_for_user(spotify_user_id)
        profile = spotify_get('/me', access_token)
        saved = all_saved_tracks(access_token)
        grouped = group_tracks(saved)
        results = create_or_replace_decade_playlists(access_token, profile, grouped)
        session['latest_results'] = results
        session['flash_message'] = f"Created {len(results)} playlist(s) from {len(saved)} liked tracks scanned."
        session['flash_class'] = 'msg-ok'
    except requests.HTTPError as exc:
        body = exc.response.text[:200] if exc.response is not None else str(exc)
        code = exc.response.status_code if exc.response is not None else 'unknown'
        session['flash_message'] = f"Spotify API error: {code} {body}"
        session['flash_class'] = 'msg-err'
    except Exception as exc:
        session['flash_message'] = f"Run failed: {exc}"
        session['flash_class'] = 'msg-err'
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    session['flash_message'] = 'Session cleared. Stored refresh tokens remain in the database volume until removed manually.'
    session['flash_class'] = 'msg-warn'
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT)
