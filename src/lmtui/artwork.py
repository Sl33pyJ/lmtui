# ------ Imports ------
import subprocess
import tempfile
from pathlib import Path


# ------ Cache location ------
# Per-user temp directory, cleaned by macOS on reboot. The file is
# overwritten on every extraction, so no accumulation.
_CACHE_PATH = Path(tempfile.gettempdir()) / "lmtui-art.png"


# ------ AppleScript source ------
# Writes the raw PNG data straight to disk. Earlier attempts to coerce
# `data` to text failed with error -1700. Writing bytes avoids that
# entirely and is the approach Apple recommends for binary output.
_EXTRACT_SCRIPT = f'''
tell application "Music"
    if player state is not stopped then
        set artData to data of artwork 1 of current track
        set outFile to open for access POSIX file "{_CACHE_PATH}" with write permission
        set eof outFile to 0
        write artData to outFile
        close access outFile
        return "OK"
    else
        return "NO_TRACK"
    end if
end tell
'''


# ------ Public API ------

def extract_artwork(timeout: float = 3.0) -> Path | None:
    """
    Ask Music.app for the current track's artwork, write it to a temp
    PNG, and return the path. Returns None if nothing is playing, the
    track has no artwork, or the call fails/times out.

    Safe to call repeatedly — the file is overwritten each time.
    """
    try:
        result = subprocess.run(
            ["osascript", "-e", _EXTRACT_SCRIPT],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None

    if result.returncode != 0:
        return None
    if result.stdout.strip() != "OK":
        return None
    if not _CACHE_PATH.exists() or _CACHE_PATH.stat().st_size == 0:
        return None

    return _CACHE_PATH
