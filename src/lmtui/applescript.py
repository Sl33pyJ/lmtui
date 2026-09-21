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
    duration: int      # total length in seconds
    position: int      # current playback position in seconds
    state: str         # "playing", "paused", "stopped"
    shuffle: bool
    repeat: str        # "off", "all", "one"
    playlist: str      # name of the current playlist, or "" if none


# ------ AppleScript source ------
# Nine newline-delimited fields. Newline-separated (not tab/comma)
# because track and album names occasionally contain those characters.
# The playlist field uses "NONE" as a sentinel because an empty last
# field gets eaten by Python's .strip().

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
        set shuffleState   to shuffle enabled as string
        set repeatState    to song repeat as string
        try
            set playlistName to name of current playlist
        on error
            set playlistName to "NONE"
        end try
        return trackName & "\\n" & trackArtist & "\\n" & trackAlbum ¬
            & "\\n" & (trackDuration as string) ¬
            & "\\n" & (playerPosition as string) ¬
            & "\\n" & playerState ¬
            & "\\n" & shuffleState ¬
            & "\\n" & repeatState ¬
            & "\\n" & playlistName
    end tell
else
    return "NOT_RUNNING"
end if
'''


# ------ Playback control scripts ------

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

_CYCLE_REPEAT_SCRIPT = '''
if application "Music" is running then
    tell application "Music"
        set currentRepeat to song repeat as string
        if currentRepeat is "off" then
            set song repeat to all
        else if currentRepeat is "all" then
            set song repeat to one
        else
            set song repeat to off
        end if
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


# ------ Public API ------

def get_now_playing(timeout: float = 2.0) -> Track | None:
    """
    Return the current Music.app track plus playback state, or None if
    nothing is playing, Music.app isn't running, or the call fails.
    """
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
    if len(parts) < 9:
        return None

    try:
        return Track(
            name=parts[0],
            artist=parts[1],
            album=parts[2],
            duration=int(float(parts[3])),
            position=int(float(parts[4])),
            state=parts[5].strip(),
            shuffle=parts[6].strip().lower() == "true",
            repeat=parts[7].strip() or "off",
            playlist="" if parts[8].strip() == "NONE" else parts[8].strip(),
        )
    except (ValueError, IndexError):
        return None


def play_pause() -> bool:
    return _run_script(_PLAY_PAUSE_SCRIPT)


def next_track() -> bool:
    return _run_script(_NEXT_TRACK_SCRIPT)


def previous_track() -> bool:
    return _run_script(_PREVIOUS_TRACK_SCRIPT)


def toggle_shuffle() -> bool:
    return _run_script(_TOGGLE_SHUFFLE_SCRIPT)


def cycle_repeat() -> bool:
    return _run_script(_CYCLE_REPEAT_SCRIPT)


def volume_up() -> bool:
    return _run_script(_VOLUME_UP_SCRIPT)


def volume_down() -> bool:
    return _run_script(_VOLUME_DOWN_SCRIPT)
