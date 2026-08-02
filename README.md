# Spotify Decades Plus

A Flask app that syncs your Spotify liked songs into SQLite, enriches them with MusicBrainz metadata, normalizes genres, and builds Spotify playlists by decade, genre, and decade+genre.

## Included files
- `app.py`
- `sync.py`
- `db.py`
- `classify.py`
- `playlist_builder.py`
- `spotify_client.py`
- `musicbrainz_client.py`
- `musicbrainz_oauth.py`
- `Dockerfile`
- `requirements.txt`
- `.env.sample`
- `docker-compose.yml.sample`

## Features
- Spotify OAuth login and saved-track sync
- Local SQLite metadata store
- MusicBrainz lookup by ISRC first, then search fallback
- Manual admin UI for unmatched tracks and genre overrides
- Playlist creation for decade, genre, and combined buckets
- Optional MusicBrainz OAuth callback route at `/musicbrainz/callback`
- Secret-aware config loading with `*_FILE` support

## Docker secrets support
Docker Compose secrets are mounted as files under `/run/secrets/<name>`, and this app supports the `*_FILE` convention to read secret values from those mounted files instead of plain environment variables. [web:41]

## docker run with secrets
Use a bind-mounted secrets directory and point `*_FILE` variables at those secret files, because file-based secret paths are the practical secure pattern for containers. [web:41][web:44]

```bash
docker build -t spotify-decades-plus .

docker run -d \
  --name spotify-decades-plus \
  -p 8080:8080 \
  -v $(pwd)/data:/data \
  -v $(pwd)/logs:/logs \
  -v $(pwd)/secrets:/run/secrets:ro \
  -e SPOTIFY_REDIRECT_URI=http://localhost:8080/callback \
  -e MUSICBRAINZ_REDIRECT_URI=http://localhost:8080/musicbrainz/callback \
  -e SPOTIFY_CLIENT_ID_FILE=/run/secrets/spotify_client_id.txt \
  -e SPOTIFY_CLIENT_SECRET_FILE=/run/secrets/spotify_client_secret.txt \
  -e FLASK_SECRET_KEY_FILE=/run/secrets/flask_secret_key.txt \
  -e MUSICBRAINZ_CLIENT_ID_FILE=/run/secrets/musicbrainz_client_id.txt \
  -e MUSICBRAINZ_CLIENT_SECRET_FILE=/run/secrets/musicbrainz_client_secret.txt \
  -e MUSICBRAINZ_USER_AGENT_FILE=/run/secrets/musicbrainz_user_agent.txt \
  spotify-decades-plus
```

## MusicBrainz OAuth callback
Register your MusicBrainz application with a redirect URI that exactly matches your configured callback, such as `http://localhost:8080/musicbrainz/callback`, because MusicBrainz OAuth2 redirects back to that URI and exchanges the code at `https://musicbrainz.org/oauth2/token`. [web:29]

## Setup
1. Copy `.env.sample` to `.env`.
2. Put secrets in `./secrets/*.txt` if using Docker secrets.
3. Update your Spotify app redirect URI to `/callback`.
4. If using MusicBrainz OAuth, register the app and set the redirect URI to `/musicbrainz/callback`.
5. Start with `docker compose -f docker-compose.yml.sample up --build -d`.

## Runtime flow
1. Connect Spotify.
2. Sync library.
3. Sync all with MusicBrainz, or enrich selected tracks from the admin screen.
4. Fix unmatched tracks or genre overrides in the admin UI.
5. Build playlists.

## Notes
- MusicBrainz public WS/2 lookups work without OAuth, but a meaningful `User-Agent` is still required and clients should respect rate limits. [web:14][web:10]
- Spotify artist genres are treated as fallback hints, while MusicBrainz genres and release-group dates are preferred where available. [page:1][page:2]
- Playlist modification depends on Spotify playlist scopes and playlist visibility. [web:22][web:24]