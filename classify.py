import re

CANONICAL_GENRES = {
    "alternative": ["alternative", "alternative rock", "alt-rock", "indie rock", "college rock"],
    "hard rock": ["hard rock", "arena rock", "classic hard rock"],
    "heavy metal": ["heavy metal", "metal", "thrash metal", "speed metal", "doom metal", "power metal", "black metal", "death metal", "nu metal"],
    "punk": ["punk", "punk rock", "pop punk", "hardcore punk", "post-punk"],
    "grunge": ["grunge"],
    "industrial": ["industrial", "industrial rock", "industrial metal"],
    "synthpop": ["synthpop", "new wave", "electropop"],
    "hip hop": ["hip hop", "rap", "gangsta rap", "alternative hip hop"],
    "pop": ["pop", "dance pop", "power pop", "electropop"],
    "electronic": ["electronic", "electronica", "edm", "house", "techno", "trance", "ambient"],
    "rock": ["rock", "classic rock", "album rock", "alternative metal"],
}

def normalize_text(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9\\s-]", "", value)
    value = re.sub(r"\\s+", " ", value)
    return value

def canonicalize_genre(raw_genre: str) -> str | None:
    text = normalize_text(raw_genre)
    if not text:
        return None
    for canonical, aliases in CANONICAL_GENRES.items():
        if text == canonical or text in aliases:
            return canonical.title()
    if "metal" in text:
        return "Heavy Metal"
    if "alternative" in text:
        return "Alternative"
    if "hard rock" in text:
        return "Hard Rock"
    if "punk" in text:
        return "Punk"
    if "grunge" in text:
        return "Grunge"
    if "industrial" in text:
        return "Industrial"
    if "synth" in text or "new wave" in text:
        return "Synthpop"
    if "hip hop" in text or text == "rap":
        return "Hip Hop"
    if "electro" in text or text in {"house", "techno", "trance", "ambient"}:
        return "Electronic"
    if text == "rock" or text.endswith(" rock"):
        return "Rock"
    if "pop" in text:
        return "Pop"
    return None

def year_to_decade_label(year: int | None) -> str | None:
    if not year or year < 1900 or year > 2100:
        return None
    return f"{year // 10 * 10}s"

def pick_year(mb_first_release_date: str | None, spotify_album_release_date: str | None) -> int | None:
    for value in [mb_first_release_date, spotify_album_release_date]:
        if value and len(value) >= 4 and value[:4].isdigit():
            return int(value[:4])
    return None

def classify_track(mb_genres: list[str], spotify_artist_genres: list[str], override_genres: list[str] | None = None):
    seen = set()
    results = []
    for genre in (override_genres or []):
        canonical = canonicalize_genre(genre) or genre.strip().title()
        if canonical and canonical not in seen:
            results.append((canonical, "override", 10.0))
            seen.add(canonical)
    for genre in mb_genres:
        canonical = canonicalize_genre(genre)
        if canonical and canonical not in seen:
            results.append((canonical, "musicbrainz", 3.0))
            seen.add(canonical)
    for genre in spotify_artist_genres:
        canonical = canonicalize_genre(genre)
        if canonical and canonical not in seen:
            results.append((canonical, "spotify_artist", 1.0))
            seen.add(canonical)
    return results