# ------ Imports ------
import subprocess
from dataclasses import dataclass


# ------ Data model ------

@dataclass(frozen=True)
class Track:
    """A snapshot of the current track in Music.app."""
    name: str
    artist: str
    album: str
    duration: int
    position: int
    state: str


# ------ AppleScript source ------
# Guarded by `application "Music" is running` so polling never launches
# Music.app as a side effect. Fields are newline-delimited for parsing safety.

_NOW_PLAYING_SCRIPT = '''
if application "Music" is running then
    tell application "Music"
        if player state is stopped then
            return "STOPPED"
        end if
        set trackName      to name of current track
        set trackArtist    to artist of current track
        set trackAlbum     to album of current track
        set trackDuration  to duration of current track
        set playerPosition to player position
        set playerState    to player state as string
        return trackName & "\\n" & trackArtist & "\\n" & trackAlbum ¬
            & "\\n" & (trackDuration as string) ¬
            & "\\n" & (playerPosition as string) ¬
            & "\\n" & playerState
    end tell
else
    return "NOT_RUNNING"
end if
'''


# ------ Public API ------

def get_now_playing(timeout: float = 2.0) -> Track | None:
    """Return the current Music.app track, or None on any failure."""
    try:
        result = subprocess.run(
            ["osascript", "-e", _NOW_PLAYING_SCRIPT],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None

    if result.returncode != 0:
        return None

    raw = result.stdout.strip()
    if raw in ("", "STOPPED", "NOT_RUNNING"):
        return None

    parts = raw.split("\n")
    if len(parts) < 6:
        return None

    try:
        return Track(
            name=parts[0],
            artist=parts[1],
            album=parts[2],
            duration=int(float(parts[3])),
            position=int(float(parts[4])),
            state=parts[5].strip(),
        )
    except (ValueError, IndexError):
        return None
