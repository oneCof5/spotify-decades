import base64
from urllib.parse import urlencode
import requests
from db import env_or_file

SPOTIFY_CLIENT_ID = env_or_file("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = env_or_file("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = env_or_file("SPOTIFY_REDIRECT_URI", "http://localhost:8080/callback")
SPOTIFY_ACCOUNTS = "https://accounts.spotify.com"
SPOTIFY_API = "https://api.spotify.com/v1"

SCOPES = (
    "user-library-read "
    "user-read-private "
    "playlist-read-private "
    "playlist-modify-private "
    "playlist-modify-public"
)

def build_authorize_url(state: str) -> str:
    return f"{SPOTIFY_ACCOUNTS}/authorize?{urlencode({'client_id': SPOTIFY_CLIENT_ID, 'response_type': 'code', 'redirect_uri': SPOTIFY_REDIRECT_URI, 'scope': SCOPES, 'state': state, 'show_dialog': 'true'})}"

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

def get_me(access_token: str) -> dict:
    resp = spotify_request("GET", "/me", access_token)
    resp.raise_for_status()
    return resp.json()

def all_saved_tracks(access_token: str):
    items = []
    path = "/me/tracks"
    params = {"limit": 50}
    while path:
        resp = spotify_request("GET", path, access_token, params=params)
        resp.raise_for_status()
        data = resp.json()
        items.extend(data.get("items", []))
        path = data.get("next") or None
        params = None
    return items

def fetch_artists(access_token: str, artist_ids: list[str]) -> list[dict]:
    if not artist_ids:
        return []
    items = []
    # for i in range(0, len(artist_ids), 50):
    #     chunk = artist_ids[i:i+50]
    #     resp = spotify_request("GET", "/artists", access_token, params={"ids": ",".join(chunk)})
    #     resp.raise_for_status()
    #     items.extend(resp.json().get("artists") or [])
    for artist_id in artist_ids:
        resp = spotify_request("GET", f"/artists/{artist_id}", access_token)
        resp.raise_for_status()
        items.append(resp.json())
    return items

def get_current_user_playlists(access_token: str, owner_id: str):
    playlists = []
    path = "/me/playlists"
    params = {"limit": 50}
    while path:
        resp = spotify_request("GET", path, access_token, params=params)
        resp.raise_for_status()
        data = resp.json()
        for item in data.get("items", []):
            if (item.get("owner") or {}).get("id") == owner_id:
                playlists.append(item)
        path = data.get("next") or None
        params = None
    return playlists

def find_existing_playlists(access_token: str, owner_id: str):
    return {(p.get("name") or "").strip(): p for p in get_current_user_playlists(access_token, owner_id)}

def create_playlist(access_token: str, name: str, public: bool, description: str):
    resp = spotify_request("POST", "/me/playlists", access_token, payload={"name": name, "public": public, "description": description})
    resp.raise_for_status()
    return resp.json()

def update_playlist_details(access_token: str, playlist_id: str, name: str, public: bool, description: str):
    resp = spotify_request("PUT", f"/playlists/{playlist_id}", access_token, payload={"name": name, "public": public, "description": description})
    resp.raise_for_status()

def replace_playlist_items(access_token: str, playlist_id: str, uris: list[str]):
    first = uris[:100]
    resp = spotify_request("PUT", f"/playlists/{playlist_id}/items", access_token, payload={"uris": first})
    resp.raise_for_status()
    for i in range(100, len(uris), 100):
        add_resp = spotify_request("POST", f"/playlists/{playlist_id}/items", access_token, payload={"uris": uris[i:i+100]})
        add_resp.raise_for_status()