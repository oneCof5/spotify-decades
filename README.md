# Spotify Decades Docker Service

This container runs a small Flask web app that handles Spotify OAuth redirects at a hosted URL such as `https://spotify-decades.mydomain.com/callback` and then syncs playlists from your liked songs grouped by decade.

## What changed

- Existing playlists like `My 1970s` are reused instead of duplicated.
- Playlist contents are replaced in place, then topped up in batches over 100 items.
- The app uses `/playlists/{id}/items` for playlist item updates.
- A heuristic tries to infer an earlier original release year for remasters and deluxe editions.
- Debug output includes year-mismatch diagnostics.

## Playlist sync behavior

The app reads the current user playlists, filters to playlists owned by the connected user, and matches by exact playlist name. If `My 1980s` already exists, it updates that playlist and replaces its full track list. If it does not exist, the app creates it.

## Original release year heuristic

Spotify’s standard saved-tracks data exposes album `release_date`, but that often reflects a remaster, deluxe release, or digital reissue instead of the earliest release. Spotify’s Web API does not expose a simple universal `original_release_year` field in the saved-tracks response, so this app uses best-effort logic:

- Start with the track’s album `release_date` year.
- If the track title or album title contains markers like `Remastered`, `Deluxe`, `Expanded`, `Anniversary`, or `Reissue`, search Spotify for alternate catalog matches with the same track and primary artist.
- Pick the earliest matching year found.
- Record mismatches in the debug panel for review.

This improves many obvious remaster cases, but it is not perfect. The most accurate next step would be enriching Spotify tracks with MusicBrainz or Discogs data and caching canonical release years.