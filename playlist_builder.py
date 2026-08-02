from collections import defaultdict
from classify import pick_year, year_to_decade_label
from db import get_playlist_source_rows
from spotify_client import create_playlist, find_existing_playlists, replace_playlist_items, update_playlist_details

def build_playlist_buckets(spotify_user_id: str):
    rows = get_playlist_source_rows(spotify_user_id)
    decade_buckets = defaultdict(list)
    genre_buckets = defaultdict(list)
    combo_buckets = defaultdict(list)
    seen_bucket_track = set()

    for row in rows:
        uri = row["spotify_uri"]
        if not uri:
            continue

        year = pick_year(row["first_release_date"], row["album_release_date"])
        decade = year_to_decade_label(year)
        genre = row["genre"]

        if decade:
            key = ("decade", decade, uri)
            if key not in seen_bucket_track:
                decade_buckets[decade].append(uri)
                seen_bucket_track.add(key)

        if genre:
            key = ("genre", genre, uri)
            if key not in seen_bucket_track:
                genre_buckets[genre].append(uri)
                seen_bucket_track.add(key)

        if decade and genre:
            combo_name = f"{genre} {decade}"
            key = ("combo", combo_name, uri)
            if key not in seen_bucket_track:
                combo_buckets[combo_name].append(uri)
                seen_bucket_track.add(key)

    return {
        "decade": dict(sorted(decade_buckets.items())),
        "genre": dict(sorted(genre_buckets.items())),
        "combo": dict(sorted(combo_buckets.items())),
    }

def sync_bucket_playlists(access_token: str, profile: dict, playlist_prefix: str, public: bool, bucket_type: str, grouped: dict[str, list[str]]):
    existing = find_existing_playlists(access_token, profile["id"])
    results = []

    for label, uris in grouped.items():
        if not uris:
            continue

        playlist_name = f"{playlist_prefix} {label}"
        description = f"Auto-generated from liked songs by {bucket_type}."
        playlist = existing.get(playlist_name)
        action = "updated"

        if playlist:
            update_playlist_details(access_token, playlist["id"], playlist_name, public, description)
        else:
            playlist = create_playlist(access_token, playlist_name, public, description)
            action = "created"

        replace_playlist_items(access_token, playlist["id"], uris)
        results.append({
            "type": bucket_type,
            "label": label,
            "count": len(uris),
            "name": playlist_name,
            "url": playlist.get("external_urls", {}).get("spotify", "#"),
            "action": action,
        })

    return results