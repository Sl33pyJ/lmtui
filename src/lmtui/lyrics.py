# ------ Imports ------
import re
from dataclasses import dataclass
from typing import Optional

import httpx


# ------ Data model ------

@dataclass(frozen=True)
class LyricLine:
    """One line of lyrics with its start time in seconds (0 for unsynced)."""
    time: float
    text: str


@dataclass(frozen=True)
class Lyrics:
    """A fetched lyrics record, either synced or plain."""
    synced: bool
    lines: list[LyricLine]
    plain: str  # raw plain text, used when synced is empty

    @property
    def is_empty(self) -> bool:
        return not self.lines and not self.plain


# ------ LRC parser ------
# LRC format: [mm:ss.xx] text  — sometimes [mm:ss] with no fraction.
# We collect all lines, sort by time, and ignore timestamps we can't
# parse. Metadata tags like [ar:Artist] are skipped.

_LRC_LINE_RE = re.compile(
    r"^\[(\d+):(\d+)(?:\.(\d+))?\]\s*(.*)$"
)


def _parse_lrc(raw: str) -> list[LyricLine]:
    lines: list[LyricLine] = []
    for line in raw.splitlines():
        match = _LRC_LINE_RE.match(line.strip())
        if not match:
            continue
        minutes, seconds, fraction, text = match.groups()
        try:
            time = int(minutes) * 60 + int(seconds)
            if fraction:
                # .xx or .xxx — normalize to fractional seconds
                time += int(fraction) / (10 ** len(fraction))
        except ValueError:
            continue
        lines.append(LyricLine(time=time, text=text or " "))
    lines.sort(key=lambda l: l.time)
    return lines


# ------ LRCLIB API client ------

_LRCLIB_URL = "https://lrclib.net/api/get"

# LRCLIB asks clients to identify themselves in the User-Agent header.
# Without this, requests may be rejected.
_HEADERS = {
    "User-Agent": "lmtui v0.1.0 (https://github.com/Sl33pyJ/lmtui)",
}


def fetch_lyrics(
    track_name: str,
    artist_name: str,
    album_name: str = "",
    duration: int = 0,
    timeout: float = 5.0,
) -> Optional[Lyrics]:
    """
    Fetch lyrics from LRCLIB for a given track.

    Returns None if the track has no lyrics, if the request fails,
    or if the API returns anything other than a valid match. Duration
    is optional but substantially improves match accuracy — LRCLIB
    requires a ±2 second match.
    """
    params: dict[str, str | int] = {
        "track_name": track_name,
        "artist_name": artist_name,
    }
    if album_name:
        params["album_name"] = album_name
    if duration > 0:
        params["duration"] = duration

    try:
        response = httpx.get(
            _LRCLIB_URL,
            params=params,
            headers=_HEADERS,
            timeout=timeout,
            follow_redirects=True,
        )
    except (httpx.HTTPError, httpx.TimeoutException):
        return None

    if response.status_code == 404:
        return None
    if response.status_code != 200:
        return None

    try:
        data = response.json()
    except ValueError:
        return None

    if data.get("instrumental"):
        return None

    plain = data.get("plainLyrics") or ""
    synced_raw = data.get("syncedLyrics") or ""

    if synced_raw:
        parsed = _parse_lrc(synced_raw)
        if parsed:
            return Lyrics(synced=True, lines=parsed, plain=plain)

    if plain:
        # Unsynced — one "line" per row, no timestamps
        return Lyrics(
            synced=False,
            lines=[LyricLine(time=0.0, text=line) for line in plain.splitlines()],
            plain=plain,
        )

    return None
