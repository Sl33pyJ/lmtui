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
# Two-stage strategy: try direct duplicate (owned tracks); fall back to
# library-then-duplicate (subscription tracks).

_ADD_TO_PLAYLIST_SCRIPT = '''
on run argv
    set plName to item 1 of argv
    tell application "Music"
        if player state is stopped then return "NO_TRACK"
        set t to current track
        set trackName to name of t
        set trackArtist to artist of t

        try
            duplicate t to playlist plName
            try
                set loved of t to true
            end try
            return "OK"
        end try

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


# ------ Duplicate detection ------

_CURRENT_COUNT_SCRIPT = '''
on run argv
    set plName to item 1 of argv
    tell application "Music"
        if player state is stopped then return "0"
        set t to current track
        set trackName to name of t
        set trackArtist to artist of t
        try
            set pl to user playlist plName
            set matches to (every track of pl ¬
                whose name is trackName and artist is trackArtist)
            return (count of matches) as string
        on error
            return "0"
        end try
    end tell
end run
'''


def current_track_count_in(playlist_name: str, timeout: float = 5.0) -> int:
    """Count how many times the current track appears in a playlist."""
    try:
        result = subprocess.run(
            ["osascript", "-e", _CURRENT_COUNT_SCRIPT, playlist_name],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return 0

    if result.returncode != 0:
        return 0

    try:
        return int(result.stdout.strip())
    except ValueError:
        return 0


# ------ Playlist tracks ------

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
    """Return list of dicts with keys: index, name, artist, album."""
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
# play_playlist_track jumps directly to track N.
# smart_next / smart_previous read the current playlist context and
# compute the target track ourselves, because Music.app's native
# `next track` and `previous track` don't respect playlist order when
# a track was played directly.

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

_SMART_STEP_SCRIPT = '''
on run argv
    set direction to item 1 of argv
    tell application "Music"
        if player state is stopped then return "STOPPED"

        try
            set pl to current playlist
        on error
            return "NO_CONTEXT"
        end try

        set curName to name of current track
        set curArtist to artist of current track
        set curIdx to 0
        set i to 0
        repeat with t in tracks of pl
            set i to i + 1
            if (name of t is curName) and (artist of t is curArtist) then
                set curIdx to i
                exit repeat
            end if
        end repeat

        if curIdx is 0 then return "NOT_FOUND"

        set trackCount to count of tracks of pl

        if direction is "next" then
            if curIdx >= trackCount then return "END_OF_PLAYLIST"
            play track (curIdx + 1) of pl
            return "OK"
        else if direction is "previous" then
            if curIdx <= 1 then
                play track curIdx of pl
                return "OK"
            end if
            play track (curIdx - 1) of pl
            return "OK"
        end if

        return "BAD_DIRECTION"
    end tell
end run
'''


def play_playlist_track(playlist_name: str, index: int, timeout: float = 5.0) -> bool:
    """Jump directly to track N (1-based) of a user playlist."""
    try:
        result = subprocess.run(
            ["osascript", "-e", _PLAY_PLAYLIST_TRACK_SCRIPT, playlist_name, str(index)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "OK"


def smart_next(timeout: float = 5.0) -> bool:
    """Advance one track within the current playlist context."""
    try:
        result = subprocess.run(
            ["osascript", "-e", _SMART_STEP_SCRIPT, "next"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "OK"


def smart_previous(timeout: float = 5.0) -> bool:
    """Step back one track within the current playlist context."""
    try:
        result = subprocess.run(
            ["osascript", "-e", _SMART_STEP_SCRIPT, "previous"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "OK"


# ------ Remove from playlist ------

_REMOVE_PLAYLIST_TRACK_SCRIPT = '''
on run argv
    set plName to item 1 of argv
    set trackIdx to (item 2 of argv) as integer
    tell application "Music"
        delete track trackIdx of user playlist plName
    end tell
    return "OK"
end run
'''


def remove_playlist_track(playlist_name: str, index: int, timeout: float = 5.0) -> bool:
    """Remove track N (1-based) from a user playlist."""
    try:
        result = subprocess.run(
            ["osascript", "-e", _REMOVE_PLAYLIST_TRACK_SCRIPT, playlist_name, str(index)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False

    return result.returncode == 0 and result.stdout.strip() == "OK"
