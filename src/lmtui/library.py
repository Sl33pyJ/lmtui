# ------ Imports ------
import subprocess


# ------ Playlists ------

_LIST_PLAYLISTS_SCRIPT = '''
tell application "Music"
    set out to ""
    repeat with p in user playlists
        if not (smart of p) then
            set out to out & (name of p) & linefeed
        end if
    end repeat
    return out
end tell
'''


def list_playlists(timeout: float = 5.0) -> list[str]:
    """Return the names of all non-smart user playlists, sorted."""
    try:
        result = subprocess.run(
            ["osascript", "-e", _LIST_PLAYLISTS_SCRIPT],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    if result.returncode != 0:
        return []

    names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return sorted(names, key=str.lower)


# ------ Add current track to a playlist ------
# Two-stage strategy ported from the user's `madd` zsh function:
#   1. Try direct duplicate (works for owned/purchased tracks)
#   2. On failure, add to library, poll for the entry, then duplicate
#      the library instance into the playlist. Required for Apple
#      Music subscription (URL) tracks.

_ADD_TO_PLAYLIST_SCRIPT = '''
on run argv
    set plName to item 1 of argv
    tell application "Music"
        if player state is stopped then return "NO_TRACK"
        set t to current track
        set trackName to name of t
        set trackArtist to artist of t

        -- Attempt 1: direct duplicate.
        try
            duplicate t to playlist plName
            try
                set loved of t to true
            end try
            return "OK"
        end try

        -- Attempt 2: subscription track. Add to library, poll, then
        -- duplicate the library instance.
        try
            duplicate t to source "Library"
        end try

        repeat 30 times
            delay 0.5
            try
                set matches to (every track of source "Library" ¬
                    whose name is trackName and artist is trackArtist)
                if (count of matches) > 0 then
                    duplicate (item 1 of matches) to playlist plName
                    try
                        set loved of t to true
                    end try
                    return "OK"
                end if
            end try
        end repeat

        return "TIMEOUT"
    end tell
end run
'''


def add_current_to_playlist(playlist_name: str, timeout: float = 20.0) -> bool:
    """Loved + add current track to `playlist_name`. Returns True on success."""
    try:
        result = subprocess.run(
            ["osascript", "-e", _ADD_TO_PLAYLIST_SCRIPT, playlist_name],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False

    return result.returncode == 0 and result.stdout.strip() == "OK"


# ------ Playlist tracks ------
# Emits one line per track, tab-separated. Leading index lets us say
# `play track N of user playlist "X"` without re-fetching.

_GET_PLAYLIST_TRACKS_SCRIPT = '''
on run argv
    set plName to item 1 of argv
    tell application "Music"
        set pl to user playlist plName
        set out to ""
        set i to 0
        repeat with t in tracks of pl
            set i to i + 1
            set out to out & i & tab & (name of t) & tab & (artist of t) & tab & (album of t) & linefeed
        end repeat
        return out
    end tell
end run
'''


def get_playlist_tracks(playlist_name: str, timeout: float = 30.0) -> list[dict]:
    """
    Return the tracks in a user playlist as a list of dicts with keys
    `index`, `name`, `artist`, `album`. The index is 1-based and can be
    passed to `play_playlist_track`.
    """
    try:
        result = subprocess.run(
            ["osascript", "-e", _GET_PLAYLIST_TRACKS_SCRIPT, playlist_name],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    if result.returncode != 0:
        return []

    tracks: list[dict] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        try:
            idx = int(parts[0])
        except ValueError:
            continue
        tracks.append({
            "index": idx,
            "name": parts[1],
            "artist": parts[2],
            "album": parts[3],
        })
    return tracks


# ------ Play from playlist ------

_PLAY_PLAYLIST_TRACK_SCRIPT = '''
on run argv
    set plName to item 1 of argv
    set trackIdx to (item 2 of argv) as integer
    tell application "Music"
        play track trackIdx of user playlist plName
    end tell
    return "OK"
end run
'''


def play_playlist_track(playlist_name: str, index: int, timeout: float = 5.0) -> bool:
    """Start playback of track N (1-based) inside the given playlist."""
    try:
        result = subprocess.run(
            [
                "osascript",
                "-e",
                _PLAY_PLAYLIST_TRACK_SCRIPT,
                playlist_name,
                str(index),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False

    return result.returncode == 0 and result.stdout.strip() == "OK"
