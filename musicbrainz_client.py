import time
import requests
from db import env_or_file

MB_API = "https://musicbrainz.org/ws/2"
MB_USER_AGENT = env_or_file("MUSICBRAINZ_USER_AGENT", "spotify-decades-plus/1.0 (contact: replace-with-email)")
_last_call_at = 0.0

def _throttle():
    global _last_call_at
    delta = time.time() - _last_call_at
    if delta < 1.05:
        time.sleep(1.05 - delta)

def mb_request(path: str, params=None, access_token: str | None = None):
    global _last_call_at
    _throttle()
    params = params or {}
    params["fmt"] = "json"
    headers = {
        "User-Agent": MB_USER_AGENT,
        "Accept": "application/json",
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    resp = requests.get(f"{MB_API}{path}", params=params, headers=headers, timeout=30)
    _last_call_at = time.time()
    resp.raise_for_status()
    return resp.json()

def lookup_isrc(isrc: str, access_token: str | None = None) -> dict:
    return mb_request(f"/isrc/{isrc}", access_token=access_token)

def search_recording(title: str, artist: str | None = None, release: str | None = None, access_token: str | None = None):
    q = f'recording:"{title}"'
    if artist:
        q += f' AND artist:"{artist}"'
    if release:
        q += f' AND release:"{release}"'
    return mb_request("/recording", {"query": q, "limit": 10}, access_token=access_token)

def get_recording(recording_id: str, access_token: str | None = None):
    return mb_request(f"/recording/{recording_id}", {"inc": "releases+genres+artist-credits"}, access_token=access_token)

def get_release_group(release_group_id: str, access_token: str | None = None):
    return mb_request(f"/release-group/{release_group_id}", {"inc": "genres"}, access_token=access_token)