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
#      the library instance into the playlist. This is required for
#      Apple Music subscription (URL) tracks, which don't respond to
#      a bare `duplicate ... to playlist`.

_ADD_TO_PLAYLIST_SCRIPT = '''
on run argv
    set plName to item 1 of argv
    tell application "Music"
        if player state is stopped then return "NO_TRACK"
        set t to current track
        set trackName to name of t
        set trackArtist to artist of t

        -- Attempt 1: direct duplicate. Works for owned/purchased tracks.
        try
            duplicate t to playlist plName
            try
                set loved of t to true
            end try
            return "OK"
        end try

        -- Attempt 2: URL/subscription track. Add to library first, then
        -- duplicate the library instance to the playlist.
        try
            duplicate t to source "Library"
        end try

        -- Wait up to ~15s for Music.app to surface the library entry.
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
