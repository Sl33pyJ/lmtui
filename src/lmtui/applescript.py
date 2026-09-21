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



# ------ Playback control scripts ------
# Every script is wrapped in `if application "Music" is running then` so
# they never launch Music.app as a side effect of pressing a key.
# All return a simple "OK" or "NOT_RUNNING" string.

def _run_script(script: str, timeout: float = 2.0) -> bool:
    """Run an AppleScript snippet. Return True on success, False on any failure."""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "OK"


_PLAY_PAUSE_SCRIPT = '''
if application "Music" is running then
    tell application "Music" to playpause
    return "OK"
else
    return "NOT_RUNNING"
end if
'''

_NEXT_TRACK_SCRIPT = '''
if application "Music" is running then
    tell application "Music" to next track
    return "OK"
else
    return "NOT_RUNNING"
end if
'''

_PREVIOUS_TRACK_SCRIPT = '''
if application "Music" is running then
    tell application "Music" to previous track
    return "OK"
else
    return "NOT_RUNNING"
end if
'''

_TOGGLE_SHUFFLE_SCRIPT = '''
if application "Music" is running then
    tell application "Music"
        set shuffle enabled to not shuffle enabled
    end tell
    return "OK"
else
    return "NOT_RUNNING"
end if
'''

_VOLUME_UP_SCRIPT = '''
if application "Music" is running then
    tell application "Music"
        set currentVolume to sound volume
        if currentVolume < 100 then
            set currentVolume to currentVolume + 10
            if currentVolume > 100 then set currentVolume to 100
            set sound volume to currentVolume
        end if
    end tell
    return "OK"
else
    return "NOT_RUNNING"
end if
'''

_VOLUME_DOWN_SCRIPT = '''
if application "Music" is running then
    tell application "Music"
        set currentVolume to sound volume
        if currentVolume > 0 then
            set currentVolume to currentVolume - 10
            if currentVolume < 0 then set currentVolume to 0
            set sound volume to currentVolume
        end if
    end tell
    return "OK"
else
    return "NOT_RUNNING"
end if
'''

# ------ Playback control API ------

def play_pause() -> bool:
    """Toggle play/pause. Returns True if Music.app accepted the command."""
    return _run_script(_PLAY_PAUSE_SCRIPT)


def next_track() -> bool:
    """Skip to the next track."""
    return _run_script(_NEXT_TRACK_SCRIPT)


def previous_track() -> bool:
    """Go back to the previous track."""
    return _run_script(_PREVIOUS_TRACK_SCRIPT)


def toggle_shuffle() -> bool:
    """Flip the shuffle state."""
    return _run_script(_TOGGLE_SHUFFLE_SCRIPT)


def volume_up() -> bool:
    """Increase Music.app volume by 10 (capped at 100)."""
    return _run_script(_VOLUME_UP_SCRIPT)


def volume_down() -> bool:
    """Decrease Music.app volume by 10 (floored at 0)."""
    return _run_script(_VOLUME_DOWN_SCRIPT)
