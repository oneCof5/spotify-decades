import json
import os
import sqlite3
import time
from flask import g


def env_or_file(name: str, default: str = "") -> str:
    file_name = f"{name}_FILE"
    file_path = os.getenv(file_name)
    if file_path:
        try:
            with open(file_path, "r", encoding="utf-8") as fh:
                return fh.read().strip()
        except FileNotFoundError:
            return default
    return os.getenv(name, default)


DB_PATH = env_or_file("DATABASE_PATH", "/data/spotify_decades.db")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tokens (
  provider TEXT NOT NULL,
  account_id TEXT NOT NULL,
  refresh_token TEXT,
  access_token TEXT,
  token_type TEXT,
  scope TEXT,
  expires_at INTEGER,
  created_at INTEGER NOT NULL,
  PRIMARY KEY (provider, account_id)
);

CREATE TABLE IF NOT EXISTS saved_tracks (
  spotify_user_id TEXT NOT NULL,
  spotify_track_id TEXT NOT NULL,
  added_at TEXT,
  PRIMARY KEY (spotify_user_id, spotify_track_id)
);

CREATE TABLE IF NOT EXISTS spotify_tracks (
  spotify_track_id TEXT PRIMARY KEY,
  spotify_uri TEXT NOT NULL,
  name TEXT NOT NULL,
  duration_ms INTEGER,
  explicit INTEGER,
  popularity INTEGER,
  track_number INTEGER,
  disc_number INTEGER,
  isrc TEXT,
  spotify_album_id TEXT,
  album_name TEXT,
  album_release_date TEXT,
  album_release_date_precision TEXT,
  raw_json TEXT,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS spotify_artists (
  spotify_artist_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  genres_json TEXT,
  raw_json TEXT,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS track_artists (
  spotify_track_id TEXT NOT NULL,
  spotify_artist_id TEXT NOT NULL,
  artist_order INTEGER NOT NULL,
  PRIMARY KEY (spotify_track_id, spotify_artist_id)
);

CREATE TABLE IF NOT EXISTS mb_recordings (
  mb_recording_id TEXT PRIMARY KEY,
  title TEXT,
  first_release_date TEXT,
  artist_credit TEXT,
  disambiguation TEXT,
  raw_json TEXT,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS mb_release_groups (
  mb_release_group_id TEXT PRIMARY KEY,
  title TEXT,
  primary_type TEXT,
  first_release_date TEXT,
  genres_json TEXT,
  raw_json TEXT,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS mb_track_matches (
  spotify_track_id TEXT PRIMARY KEY,
  match_method TEXT,
  mb_recording_id TEXT,
  mb_release_group_id TEXT,
  score REAL,
  status TEXT NOT NULL DEFAULT 'pending',
  last_error TEXT,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS track_genres (
  spotify_track_id TEXT NOT NULL,
  genre TEXT NOT NULL,
  source TEXT NOT NULL,
  weight REAL NOT NULL DEFAULT 1.0,
  PRIMARY KEY (spotify_track_id, genre, source)
);

CREATE TABLE IF NOT EXISTS genre_overrides (
  spotify_track_id TEXT NOT NULL,
  genre TEXT NOT NULL,
  PRIMARY KEY (spotify_track_id, genre)
);

CREATE INDEX IF NOT EXISTS idx_saved_tracks_user ON saved_tracks(spotify_user_id);
CREATE INDEX IF NOT EXISTS idx_track_artists_track ON track_artists(spotify_track_id, artist_order);
CREATE INDEX IF NOT EXISTS idx_spotify_tracks_isrc ON spotify_tracks(isrc);
CREATE INDEX IF NOT EXISTS idx_mb_matches_status ON mb_track_matches(status);
CREATE INDEX IF NOT EXISTS idx_track_genres_genre ON track_genres(genre);
CREATE INDEX IF NOT EXISTS idx_spotify_tracks_name ON spotify_tracks(name, spotify_track_id);
"""


def get_db():
    if "db" not in g:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        g.db = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA busy_timeout = 30000")
    return g.db


def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.executescript(SCHEMA_SQL)
    db.commit()


def upsert_token(
    provider: str,
    account_id: str,
    refresh_token: str | None = None,
    access_token: str | None = None,
    token_type: str | None = None,
    scope: str | None = None,
    expires_at: int | None = None,
):
    db = get_db()
    db.execute(
        """
        INSERT INTO tokens (provider, account_id, refresh_token, access_token, token_type, scope, expires_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(provider, account_id) DO UPDATE SET
          refresh_token = COALESCE(excluded.refresh_token, tokens.refresh_token),
          access_token = COALESCE(excluded.access_token, tokens.access_token),
          token_type = COALESCE(excluded.token_type, tokens.token_type),
          scope = COALESCE(excluded.scope, tokens.scope),
          expires_at = COALESCE(excluded.expires_at, tokens.expires_at),
          created_at = excluded.created_at
        """,
        (
            provider,
            account_id,
            refresh_token,
            access_token,
            token_type,
            scope,
            expires_at,
            int(time.time()),
        ),
    )
    db.commit()


def get_token_row(provider: str, account_id: str):
    return get_db().execute(
        "SELECT * FROM tokens WHERE provider = ? AND account_id = ?",
        (provider, account_id),
    ).fetchone()


def save_spotify_track(spotify_user_id: str, item: dict):
    track = item.get("track") or {}
    album = track.get("album") or {}
    external_ids = track.get("external_ids") or {}
    artists = track.get("artists") or []
    now = int(time.time())
    db = get_db()

    db.execute(
        """
        INSERT INTO saved_tracks (spotify_user_id, spotify_track_id, added_at)
        VALUES (?, ?, ?)
        ON CONFLICT(spotify_user_id, spotify_track_id) DO UPDATE SET
          added_at = excluded.added_at
        """,
        (spotify_user_id, track["id"], item.get("added_at")),
    )

    db.execute(
        """
        INSERT INTO spotify_tracks (
          spotify_track_id, spotify_uri, name, duration_ms, explicit, popularity,
          track_number, disc_number, isrc, spotify_album_id, album_name,
          album_release_date, album_release_date_precision, raw_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(spotify_track_id) DO UPDATE SET
          spotify_uri = excluded.spotify_uri,
          name = excluded.name,
          duration_ms = excluded.duration_ms,
          explicit = excluded.explicit,
          popularity = excluded.popularity,
          track_number = excluded.track_number,
          disc_number = excluded.disc_number,
          isrc = excluded.isrc,
          spotify_album_id = excluded.spotify_album_id,
          album_name = excluded.album_name,
          album_release_date = excluded.album_release_date,
          album_release_date_precision = excluded.album_release_date_precision,
          raw_json = excluded.raw_json,
          updated_at = excluded.updated_at
        """,
        (
            track["id"],
            track["uri"],
            track.get("name"),
            track.get("duration_ms"),
            int(bool(track.get("explicit"))),
            track.get("popularity"),
            track.get("track_number"),
            track.get("disc_number"),
            external_ids.get("isrc"),
            album.get("id"),
            album.get("name"),
            album.get("release_date"),
            album.get("release_date_precision"),
            json.dumps(track),
            now,
        ),
    )

    db.execute("DELETE FROM track_artists WHERE spotify_track_id = ?", (track["id"],))
    for idx, artist in enumerate(artists):
        db.execute(
            """
            INSERT INTO spotify_artists (spotify_artist_id, name, genres_json, raw_json, updated_at)
            VALUES (?, ?, COALESCE((SELECT genres_json FROM spotify_artists WHERE spotify_artist_id = ?), NULL),
                    COALESCE((SELECT raw_json FROM spotify_artists WHERE spotify_artist_id = ?), NULL), ?)
            ON CONFLICT(spotify_artist_id) DO UPDATE SET
              name = excluded.name,
              updated_at = excluded.updated_at
            """,
            (
                artist["id"],
                artist.get("name", "Unknown Artist"),
                artist["id"],
                artist["id"],
                now,
            ),
        )
        db.execute(
            "INSERT INTO track_artists (spotify_track_id, spotify_artist_id, artist_order) VALUES (?, ?, ?)",
            (track["id"], artist["id"], idx),
        )


def upsert_spotify_artist_detail(artist: dict):
    get_db().execute(
        """
        INSERT INTO spotify_artists (spotify_artist_id, name, genres_json, raw_json, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(spotify_artist_id) DO UPDATE SET
          name = excluded.name,
          genres_json = excluded.genres_json,
          raw_json = excluded.raw_json,
          updated_at = excluded.updated_at
        """,
        (
            artist["id"],
            artist.get("name", "Unknown Artist"),
            json.dumps(artist.get("genres") or []),
            json.dumps(artist),
            int(time.time()),
        ),
    )


def get_unhydrated_artist_ids(limit: int = 50):
    rows = get_db().execute(
        "SELECT spotify_artist_id FROM spotify_artists WHERE raw_json IS NULL OR genres_json IS NULL LIMIT ?",
        (limit,),
    ).fetchall()
    return [r[0] for r in rows]


def get_tracks_for_enrichment_after(
    limit: int = 100,
    cursor: str | None = None,
    selected_ids: list[str] | None = None,
    include_matched: bool = False,
):
    db = get_db()
    where = "WHERE 1=1"
    params: list = []
    if selected_ids:
        placeholders = ",".join(["?"] * len(selected_ids))
        where += f" AND st.spotify_track_id IN ({placeholders})"
        params.extend(selected_ids)
    elif not include_matched:
        where += " AND (mm.spotify_track_id IS NULL OR mm.status IN ('pending', 'failed'))"
    if cursor:
        where += " AND (st.name, st.spotify_track_id) > (?, ?)"
        params.extend([cursor_name_from_cursor(cursor), cursor.split("::", 1)[1] if "::" in cursor else cursor])
    params.append(limit)
    return db.execute(
        f"""
        SELECT st.spotify_track_id, st.name, st.duration_ms, st.isrc, st.album_name, st.album_release_date,
               GROUP_CONCAT(sa.name, '|||') AS artist_names
        FROM spotify_tracks st
        LEFT JOIN track_artists ta ON ta.spotify_track_id = st.spotify_track_id
        LEFT JOIN spotify_artists sa ON sa.spotify_artist_id = ta.spotify_artist_id
        LEFT JOIN mb_track_matches mm ON mm.spotify_track_id = st.spotify_track_id
        {where}
        GROUP BY st.spotify_track_id
        ORDER BY st.name, st.spotify_track_id
        LIMIT ?
        """,
        params,
    ).fetchall()


def cursor_name_from_cursor(cursor: str) -> str:
    return cursor.split("::", 1)[0] if "::" in cursor else ""


def build_cursor_from_row(row) -> str:
    return f"{row['name']}::{row['spotify_track_id']}"


def get_pending_musicbrainz_count(after_cursor: str | None = None):
    db = get_db()
    if after_cursor:
        name = cursor_name_from_cursor(after_cursor)
        rowid = after_cursor.split("::", 1)[1] if "::" in after_cursor else after_cursor
        return db.execute(
            """
            SELECT COUNT(*)
            FROM spotify_tracks st
            LEFT JOIN mb_track_matches mm ON mm.spotify_track_id = st.spotify_track_id
            WHERE (mm.spotify_track_id IS NULL OR mm.status IN ('pending', 'failed'))
              AND (st.name, st.spotify_track_id) > (?, ?)
            """,
            (name, rowid),
        ).fetchone()[0]
    return db.execute(
        """
        SELECT COUNT(*)
        FROM spotify_tracks st
        LEFT JOIN mb_track_matches mm ON mm.spotify_track_id = st.spotify_track_id
        WHERE mm.spotify_track_id IS NULL OR mm.status IN ('pending', 'failed')
        """,
    ).fetchone()[0]


def upsert_mb_match(
    spotify_track_id: str,
    match_method: str,
    mb_recording_id: str | None,
    mb_release_group_id: str | None,
    score: float,
    status: str,
    last_error: str | None = None,
):
    get_db().execute(
        """
        INSERT INTO mb_track_matches (
          spotify_track_id, match_method, mb_recording_id, mb_release_group_id, score, status, last_error, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(spotify_track_id) DO UPDATE SET
          match_method = excluded.match_method,
          mb_recording_id = excluded.mb_recording_id,
          mb_release_group_id = excluded.mb_release_group_id,
          score = excluded.score,
          status = excluded.status,
          last_error = excluded.last_error,
          updated_at = excluded.updated_at
        """,
        (
            spotify_track_id,
            match_method,
            mb_recording_id,
            mb_release_group_id,
            score,
            status,
            last_error,
            int(time.time()),
        ),
    )


def upsert_mb_recording(recording: dict):
    get_db().execute(
        """
        INSERT INTO mb_recordings (mb_recording_id, title, first_release_date, artist_credit, disambiguation, raw_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(mb_recording_id) DO UPDATE SET
          title = excluded.title,
          first_release_date = excluded.first_release_date,
          artist_credit = excluded.artist_credit,
          disambiguation = excluded.disambiguation,
          raw_json = excluded.raw_json,
          updated_at = excluded.updated_at
        """,
        (
            recording["id"],
            recording.get("title"),
            recording.get("first-release-date"),
            json.dumps(recording.get("artist-credit") or []),
            recording.get("disambiguation"),
            json.dumps(recording),
            int(time.time()),
        ),
    )


def upsert_mb_release_group(release_group: dict):
    get_db().execute(
        """
        INSERT INTO mb_release_groups (mb_release_group_id, title, primary_type, first_release_date, genres_json, raw_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(mb_release_group_id) DO UPDATE SET
          title = excluded.title,
          primary_type = excluded.primary_type,
          first_release_date = excluded.first_release_date,
          genres_json = excluded.genres_json,
          raw_json = excluded.raw_json,
          updated_at = excluded.updated_at
        """,
        (
            release_group["id"],
            release_group.get("title"),
            release_group.get("primary-type"),
            release_group.get("first-release-date"),
            json.dumps(release_group.get("genres") or []),
            json.dumps(release_group),
            int(time.time()),
        ),
    )


def replace_track_genres(spotify_track_id: str, genres: list[tuple[str, str, float]]):
    db = get_db()
    db.execute(
        "DELETE FROM track_genres WHERE spotify_track_id = ? AND source != 'override'",
        (spotify_track_id,),
    )
    for genre, source, weight in genres:
        db.execute(
            "INSERT OR REPLACE INTO track_genres (spotify_track_id, genre, source, weight) VALUES (?, ?, ?, ?)",
            (spotify_track_id, genre, source, weight),
        )


def get_playlist_source_rows(spotify_user_id: str):
    return get_db().execute(
        """
        SELECT st.spotify_track_id, st.spotify_uri, st.album_release_date,
               mm.status, mrg.first_release_date, tg.genre
        FROM saved_tracks sv
        JOIN spotify_tracks st ON st.spotify_track_id = sv.spotify_track_id
        LEFT JOIN mb_track_matches mm ON mm.spotify_track_id = st.spotify_track_id
        LEFT JOIN mb_release_groups mrg ON mrg.mb_release_group_id = mm.mb_release_group_id
        LEFT JOIN track_genres tg ON tg.spotify_track_id = st.spotify_track_id
        WHERE sv.spotify_user_id = ?
        ORDER BY st.name
        """,
        (spotify_user_id,),
    ).fetchall()


def get_unmatched_tracks(limit: int = 200):
    return get_db().execute(
        """
        SELECT st.spotify_track_id, st.name, st.album_name, st.album_release_date, st.isrc,
               mm.status, mm.last_error, mm.score,
               GROUP_CONCAT(sa.name, '|||') AS artist_names
        FROM spotify_tracks st
        JOIN saved_tracks sv ON sv.spotify_track_id = st.spotify_track_id
        LEFT JOIN track_artists ta ON ta.spotify_track_id = st.spotify_track_id
        LEFT JOIN spotify_artists sa ON sa.spotify_artist_id = ta.spotify_artist_id
        LEFT JOIN mb_track_matches mm ON mm.spotify_track_id = st.spotify_track_id
        WHERE mm.spotify_track_id IS NULL OR mm.status != 'matched'
        GROUP BY st.spotify_track_id
        ORDER BY st.name
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def get_track_override_genres(spotify_track_id: str):
    rows = get_db().execute(
        "SELECT genre FROM genre_overrides WHERE spotify_track_id = ? ORDER BY genre",
        (spotify_track_id,),
    ).fetchall()
    return [r[0] for r in rows]


def replace_genre_overrides(spotify_track_id: str, genres: list[str]):
    db = get_db()
    db.execute("DELETE FROM genre_overrides WHERE spotify_track_id = ?", (spotify_track_id,))
    for genre in sorted({g.strip() for g in genres if g and g.strip()}):
        db.execute(
            "INSERT INTO genre_overrides (spotify_track_id, genre) VALUES (?, ?)",
            (spotify_track_id, genre),
        )
    db.commit()


def get_track_detail(spotify_track_id: str):
    return get_db().execute(
        """
        SELECT st.*, mm.status AS match_status, mm.score AS match_score, mm.last_error,
               mm.mb_recording_id, mm.mb_release_group_id, mrg.first_release_date,
               mrg.genres_json AS mb_genres_json
        FROM spotify_tracks st
        LEFT JOIN mb_track_matches mm ON mm.spotify_track_id = st.spotify_track_id
        LEFT JOIN mb_release_groups mrg ON mrg.mb_release_group_id = mm.mb_release_group_id
        WHERE st.spotify_track_id = ?
        """,
        (spotify_track_id,),
    ).fetchone()