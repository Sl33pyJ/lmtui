# ------ Imports ------
import subprocess


# ------ Queue extraction ------
# Music.app doesn't expose its real "Up Next" queue via AppleScript —
# that's a UI-layer feature. What we can do is ask which playlist is
# currently playing, find the current track's position, and return what
# follows. Good approximation for non-shuffled playback.
#
# The script iterates the playlist once to find the current track, then
# collects up to `max_upcoming` tracks after it. The scan is capped at
# 5000 tracks to avoid pathological cases on huge library "playlists".
#
# Output format:
#   Line 0: PLAYLIST<tab><playlist name>
#   Lines 1..N: <index><tab><name><tab><artist><tab><album>
#
# Error sentinels (single line on stdout):
#   STOPPED      — nothing playing
#   NO_PLAYLIST  — current playlist couldn't be resolved
#   NOT_FOUND    — current track isn't in the resolved playlist

_GET_QUEUE_SCRIPT = '''
on run argv
    set maxUpcoming to (item 1 of argv) as integer
    tell application "Music"
        if player state is stopped then return "STOPPED"

        try
            set pl to current playlist
        on error
            return "NO_PLAYLIST"
        end try

        set plName to name of pl
        set currentName to name of current track
        set currentArtist to artist of current track

        set out to "PLAYLIST" & tab & plName & linefeed
        set i to 0
        set foundCurrent to false
        set shown to 0
        set maxScan to 5000

        repeat with t in tracks of pl
            set i to i + 1
            if i > maxScan then exit repeat

            if foundCurrent then
                if shown < maxUpcoming then
                    set out to out & i & tab & (name of t) & tab & (artist of t) & tab & (album of t) & linefeed
                    set shown to shown + 1
                else
                    exit repeat
                end if
            else
                if (name of t is currentName) and (artist of t is currentArtist) then
                    set foundCurrent to true
                end if
            end if
        end repeat

        if not foundCurrent then return "NOT_FOUND"

        return out
    end tell
end run
'''


# ------ Public API ------

def get_queue(max_upcoming: int = 50, timeout: float = 15.0) -> dict:
    """
    Return the currently-playing playlist and the tracks after the
    current one.

    Returns a dict with keys:
      playlist: str | None
      tracks: list[dict]  — each with keys: index, name, artist, album

    If nothing is playing, or the current playlist can't be resolved
    (e.g. the user started playback from a single-track selection),
    `playlist` is None and `tracks` is empty.
    """
    try:
        result = subprocess.run(
            ["osascript", "-e", _GET_QUEUE_SCRIPT, str(max_upcoming)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {"playlist": None, "tracks": []}

    if result.returncode != 0:
        return {"playlist": None, "tracks": []}

    raw = result.stdout.strip()
    if raw in ("", "STOPPED", "NO_PLAYLIST", "NOT_FOUND"):
        return {"playlist": None, "tracks": []}

    lines = raw.splitlines()
    header = lines[0].split("\t")
    if len(header) < 2 or header[0] != "PLAYLIST":
        return {"playlist": None, "tracks": []}

    playlist_name = header[1]

    tracks: list[dict] = []
    for line in lines[1:]:
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

    return {"playlist": playlist_name, "tracks": tracks}
