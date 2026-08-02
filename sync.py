import json
import sqlite3
import time

from classify import classify_track, normalize_text
from db import (
    get_db,
    get_token_row,
    get_track_override_genres,
    get_tracks_for_enrichment_after,
    get_unhydrated_artist_ids,
    get_pending_musicbrainz_count,
    replace_track_genres,
    save_spotify_track,
    upsert_mb_match,
    upsert_mb_recording,
    upsert_mb_release_group,
    upsert_spotify_artist_detail,
    build_cursor_from_row,
)
from musicbrainz_client import get_recording, get_release_group, lookup_isrc, search_recording
from spotify_client import all_saved_tracks, fetch_artists


def commit_with_retry(db, attempts=5, base_sleep=0.5):
    for i in range(attempts):
        try:
            db.commit()
            return
        except sqlite3.OperationalError as exc:
            if "database is locked" not in str(exc).lower() or i == attempts - 1:
                raise
            time.sleep(base_sleep * (2**i))


def sync_saved_library(access_token: str, spotify_user_id: str):
    db = get_db()
    items = all_saved_tracks(access_token)
    count = 0
    failed = 0

    for item in items:
        track = item.get("track") or {}
        if track.get("is_local") or not track.get("id") or not track.get("uri"):
            continue
        try:
            save_spotify_track(spotify_user_id, item)
            commit_with_retry(db)
            count += 1
        except Exception:
            failed += 1
            try:
                commit_with_retry(db)
            except Exception:
                pass

    return {"saved_tracks": count, "failed_tracks": failed}


def sync_artist_metadata(access_token: str, batch_size: int = 100):
    ids = get_unhydrated_artist_ids(batch_size)
    if not ids:
        return {"artists_updated": 0}
    artists = fetch_artists(access_token, ids)
    db = get_db()
    updated = 0
    for artist in artists:
        if artist and artist.get("id"):
            upsert_spotify_artist_detail(artist)
            updated += 1
            commit_with_retry(db)
    return {"artists_updated": updated}


def score_recording_candidate(
    track_title: str,
    primary_artist: str | None,
    track_duration_ms: int | None,
    candidate: dict,
) -> float:
    score = 0.0
    cand_title = normalize_text(candidate.get("title", ""))
    if normalize_text(track_title) == cand_title:
        score += 60
    if primary_artist:
        credits = candidate.get("artist-credit") or []
        names = " ".join([x.get("name", "") for x in credits if isinstance(x, dict)])
        if normalize_text(primary_artist) in normalize_text(names):
            score += 25
    if track_duration_ms and candidate.get("length"):
        diff = abs(track_duration_ms - int(candidate["length"]))
        if diff <= 2000:
            score += 15
        elif diff <= 5000:
            score += 8
    return score


def _select_release_group_id(recording: dict) -> str | None:
    releases = recording.get("releases") or []
    for release in releases:
        rg = release.get("release-group") or {}
        if rg.get("id"):
            return rg["id"]
    return None


def _musicbrainz_access_token() -> str | None:
    row = get_token_row("musicbrainz", "self")
    return row["access_token"] if row and row["access_token"] else None


def _enrich_rows(rows):
    db = get_db()
    enriched = 0
    failed = 0
    mb_access_token = _musicbrainz_access_token()

    for row in rows:
        spotify_track_id = row["spotify_track_id"]
        title = row["name"]
        duration_ms = row["duration_ms"]
        isrc = row["isrc"]
        album_name = row["album_name"]
        artist_names = (row["artist_names"] or "").split("|||") if row["artist_names"] else []
        primary_artist = artist_names[0] if artist_names else None

        try:
            selected = None
            method = None

            if isrc:
                isrc_data = lookup_isrc(isrc, access_token=mb_access_token)
                recordings = isrc_data.get("recordings") or []
                if recordings:
                    selected = max(
                        recordings,
                        key=lambda c: score_recording_candidate(
                            title, primary_artist, duration_ms, c
                        ),
                    )
                    method = "isrc"

            if not selected:
                search_data = search_recording(
                    title, primary_artist, album_name, access_token=mb_access_token
                )
                recordings = search_data.get("recordings") or []
                if recordings:
                    selected = max(
                        recordings,
                        key=lambda c: score_recording_candidate(
                            title, primary_artist, duration_ms, c
                        ),
                    )
                    method = "search"

            if not selected:
                upsert_mb_match(
                    spotify_track_id,
                    "none",
                    None,
                    None,
                    0.0,
                    "failed",
                    "No MusicBrainz match found",
                )
                commit_with_retry(db)
                failed += 1
                continue

            recording = get_recording(selected["id"], access_token=mb_access_token)
            upsert_mb_recording(recording)

            release_group_id = _select_release_group_id(recording)
            release_group = None
            if release_group_id:
                release_group = get_release_group(
                    release_group_id, access_token=mb_access_token
                )
                upsert_mb_release_group(release_group)

            upsert_mb_match(
                spotify_track_id,
                method or "search",
                recording.get("id"),
                release_group_id,
                score_recording_candidate(title, primary_artist, duration_ms, selected),
                "matched",
                None,
            )

            mb_genres = [g.get("name", "") for g in (release_group or {}).get("genres") or []]
            artist_rows = db.execute(
                """
                SELECT sa.genres_json
                FROM track_artists ta
                JOIN spotify_artists sa ON sa.spotify_artist_id = ta.spotify_artist_id
                WHERE ta.spotify_track_id = ?
                ORDER BY ta.artist_order
                """,
                (spotify_track_id,),
            ).fetchall()

            spotify_genres = []
            for artist_row in artist_rows:
                spotify_genres.extend(json.loads(artist_row["genres_json"] or "[]"))

            overrides = get_track_override_genres(spotify_track_id)
            genres = classify_track(mb_genres, spotify_genres, overrides)
            replace_track_genres(spotify_track_id, genres)
            commit_with_retry(db)
            enriched += 1

        except Exception as exc:
            upsert_mb_match(
                spotify_track_id,
                "error",
                None,
                None,
                0.0,
                "failed",
                str(exc),
            )
            commit_with_retry(db)
            failed += 1

    return {"tracks_enriched": enriched, "tracks_failed": failed}


def sync_next_musicbrainz_batch(limit: int = 5, cursor: str | None = None):
    """
    Process the next batch of tracks after `cursor`, returning summary and
    a properly formatted next_cursor using build_cursor_from_row().
    """
    rows = get_tracks_for_enrichment_after(
        limit=limit,
        cursor=cursor,
        include_matched=False,
    )
    summary = _enrich_rows(rows)
    summary["tracks_scanned"] = len(rows)

    if rows:
        # Use name::spotify_track_id cursor to avoid ordering edge cases.
        summary["next_cursor"] = build_cursor_from_row(rows[-1])
    else:
        summary["next_cursor"] = cursor

    summary["pending_remaining"] = get_pending_musicbrainz_count(
        after_cursor=summary["next_cursor"]
    )
    return summary


def sync_all_musicbrainz(limit: int = 100000):
    rows = get_tracks_for_enrichment_after(
        limit=limit,
        cursor=None,
        include_matched=True,
    )
    summary = _enrich_rows(rows)
    summary["tracks_scanned"] = len(rows)
    return summary


def enrich_unmatched_tracks(limit: int = 25):
    rows = get_tracks_for_enrichment_after(
        limit=limit,
        cursor=None,
        include_matched=False,
    )
    return _enrich_rows(rows)


def enrich_selected_tracks(track_ids: list[str]):
    rows = get_tracks_for_enrichment_after(
        limit=max(len(track_ids), 1),
        cursor=None,
        selected_ids=track_ids,
        include_matched=True,
    )
    return _enrich_rows(rows)


def reclassify_track(spotify_track_id: str):
    db = get_db()
    row = db.execute(
        """
        SELECT mrg.genres_json
        FROM spotify_tracks st
        LEFT JOIN mb_track_matches mm ON mm.spotify_track_id = st.spotify_track_id
        LEFT JOIN mb_release_groups mrg ON mrg.mb_release_group_id = mm.mb_release_group_id
        WHERE st.spotify_track_id = ?
        """,
        (spotify_track_id,),
    ).fetchone()

    mb_genres = json.loads((row["genres_json"] if row and row["genres_json"] else "[]"))
    mb_genre_names = [
        g.get("name", "") if isinstance(g, dict) else str(g) for g in mb_genres
    ]

    artist_rows = db.execute(
        """
        SELECT sa.genres_json
        FROM track_artists ta
        JOIN spotify_artists sa ON sa.spotify_artist_id = ta.spotify_artist_id
        WHERE ta.spotify_track_id = ?
        ORDER BY ta.artist_order
        """,
        (spotify_track_id,),
    ).fetchall()

    spotify_genres = []
    for artist_row in artist_rows:
        spotify_genres.extend(json.loads(artist_row["genres_json"] or "[]"))

    overrides = get_track_override_genres(spotify_track_id)
    genres = classify_track(mb_genre_names, spotify_genres, overrides)
    replace_track_genres(spotify_track_id, genres)
    commit_with_retry(db)
    return {"spotify_track_id": spotify_track_id, "genres": genres}