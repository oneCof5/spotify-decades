from urllib.parse import urlencode
import requests
from db import env_or_file

MUSICBRAINZ_BASE = "https://musicbrainz.org"
MUSICBRAINZ_CLIENT_ID = env_or_file("MUSICBRAINZ_CLIENT_ID")
MUSICBRAINZ_CLIENT_SECRET = env_or_file("MUSICBRAINZ_CLIENT_SECRET")
MUSICBRAINZ_REDIRECT_URI = env_or_file("MUSICBRAINZ_REDIRECT_URI", "http://localhost:8080/musicbrainz/callback")
MUSICBRAINZ_SCOPE = env_or_file("MUSICBRAINZ_SCOPE", "profile")

def build_mb_authorize_url(state: str) -> str:
    params = {
        "response_type": "code",
        "client_id": MUSICBRAINZ_CLIENT_ID,
        "redirect_uri": MUSICBRAINZ_REDIRECT_URI,
        "scope": MUSICBRAINZ_SCOPE,
        "state": state,
    }
    return f"{MUSICBRAINZ_BASE}/oauth2/authorize?{urlencode(params)}"

def exchange_mb_code(code: str) -> dict:
    resp = requests.post(
        f"{MUSICBRAINZ_BASE}/oauth2/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": MUSICBRAINZ_CLIENT_ID,
            "client_secret": MUSICBRAINZ_CLIENT_SECRET,
            "redirect_uri": MUSICBRAINZ_REDIRECT_URI,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()

def refresh_mb_token(refresh_token: str) -> dict:
    resp = requests.post(
        f"{MUSICBRAINZ_BASE}/oauth2/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": MUSICBRAINZ_CLIENT_ID,
            "client_secret": MUSICBRAINZ_CLIENT_SECRET,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()