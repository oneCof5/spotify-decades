# Spotify Decades Docker Service

This container runs a small Flask web app that handles Spotify OAuth redirects at a hosted URL such as `https://spotify-decades.mydomain.com/callback` and then builds playlists from your liked songs grouped by release decade.

## What it does

- Hosts `/login` to start Spotify Authorization Code flow
- Hosts `/callback` as the exact redirect URI Spotify sends users back to
- Stores Spotify refresh tokens in a SQLite database mounted at `/data`
- Refreshes access tokens automatically before Spotify API calls
- Creates one playlist per decade from your liked tracks

## Spotify app setup

1. Go to the Spotify Developer Dashboard and create an app.
2. Add this exact Redirect URI to the app:
   - `https://spotify-decades.mydomain.com/callback`
3. Copy the Client ID and Client Secret into `.env`.

The redirect URI must exactly match the value configured in the authorize request and token exchange, including scheme, host, path, casing, and trailing slash behavior.

## Local build

```bash
docker compose up --build -d
```

The app listens on port `8080` inside the container.

## Reverse proxy example

Put Nginx, Traefik, Caddy, or your existing ingress in front of the container and forward `spotify-decades.mydomain.com` to `http://spotify-decades:8080` or to the Docker host port.

### Nginx example

```nginx
server {
    listen 80;
    server_name spotify-decades.mydomain.com;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

Use your normal TLS setup so the public site is served over HTTPS.

## Run flow

1. Open `https://spotify-decades.mydomain.com`
2. Click **Connect Spotify**
3. Approve access in Spotify
4. Spotify redirects back to `/callback`
5. Click **Create decade playlists**

## Notes

- The service requests `user-library-read`, `playlist-modify-private`, and `playlist-modify-public`.
- Access tokens expire after about one hour, so the app stores and uses a refresh token for future API calls.
- Playlist grouping is based on the album `release_date` returned by Spotify.
- The current implementation creates new playlists on each run instead of updating existing ones.

## Hardening ideas

- Put the app behind your existing SSO or a simple access gate if this will be public on the internet.
- Encrypt stored refresh tokens or move them to a secrets-backed database.
- Add user-specific cleanup or idempotent update logic for existing decade playlists.
