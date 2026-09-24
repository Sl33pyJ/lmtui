# ------ Imports ------
import subprocess
import threading
from pathlib import Path


# ------ Config builder ------
# We use the user's existing cava config for the [input] section (so
# whatever device/BlackHole setup they have keeps working), but we
# always override [output] to raw stdout so we can parse frames.

_OUTPUT_BLOCK = """
[output]
method = raw
raw_target = /dev/stdout
data_format = ascii
ascii_max_range = 100
bar_delimiter = 59
frame_delimiter = 10

[color]
background = 'default'
foreground = 'default'
"""

_FALLBACK_CONFIG = """
[general]
framerate = 60
bars = 64

[input]
method = coreaudio
source = auto
"""


def _build_config() -> str:
    """Read user's cava config, strip [output], append our own."""
    user_config = Path.home() / ".config" / "cava" / "config"

    if not user_config.exists():
        return _FALLBACK_CONFIG + _OUTPUT_BLOCK

    lines = user_config.read_text().split("\n")
    result: list[str] = []
    skip = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            skip = (stripped == "[output]")
            if skip:
                continue
        if not skip:
            result.append(line)

    return "\n".join(result) + "\n" + _OUTPUT_BLOCK


# ------ Cava subprocess ------

class CavaVisualizer:
    """
    Runs cava as a subprocess and exposes the latest audio frame as a
    list of ints (0-100), one per bar.

    Consumer polls `get_frame()` from the UI thread; the actual read
    from cava's stdout happens on a background thread.
    """

    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._latest: list[int] = []
        self._config_path: Path | None = None

    def start(self) -> bool:
        """Launch cava. Returns False if cava isn't installed."""
        config_dir = Path.home() / ".cache" / "lmtui"
        config_dir.mkdir(parents=True, exist_ok=True)
        self._config_path = config_dir / "cava.conf"
        self._config_path.write_text(_build_config())

        try:
            self._process = subprocess.Popen(
                ["cava", "-p", str(self._config_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=1,
                text=True,
            )
        except FileNotFoundError:
            return False

        self._thread = threading.Thread(
            target=self._read_loop, daemon=True, name="cava-reader"
        )
        self._thread.start()
        return True

    def _read_loop(self) -> None:
        assert self._process is not None
        assert self._process.stdout is not None
        for line in self._process.stdout:
            if self._stop.is_set():
                break
            line = line.strip()
            if not line:
                continue
            try:
                values = [int(v) for v in line.split(";") if v]
            except ValueError:
                continue
            with self._lock:
                self._latest = values

    def get_frame(self) -> list[int]:
        with self._lock:
            return list(self._latest)

    def stop(self) -> None:
        self._stop.set()
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
