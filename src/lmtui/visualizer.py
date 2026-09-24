# ------ Imports ------
import os
import select
import subprocess
import threading
import time
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

    Reading strategy: raw bytes via os.read(), triggered by select()
    with a 0.5s timeout. Two failure modes are avoided:

      1. `for line in proc.stdout` (buffered text iterator) can block
         indefinitely inside CPython's internal readline, especially
         when the child produces high-frequency small writes.

      2. Bare blocking os.read() never returns if cava stops writing
         without closing the pipe cleanly.

    select() with a timeout guarantees the loop wakes up regularly to
    check _stop and the process state, so the watchdog can respawn.

    Watchdog: if the process died or no fresh frame has arrived in
    STALE_AFTER_SECONDS, respawn cava. Triggered from get_frame().
    """

    STALE_AFTER_SECONDS = 3.0
    RESTART_BACKOFF_SECONDS = 2.0
    READ_CHUNK = 65536

    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._latest: list[int] = []
        self._config_path: Path | None = None
        self._last_frame_time: float = 0.0
        self._last_restart: float = 0.0

    # ------ Public API ------

    def start(self) -> bool:
        """Launch cava. Returns False if cava isn't installed."""
        config_dir = Path.home() / ".cache" / "lmtui"
        config_dir.mkdir(parents=True, exist_ok=True)
        self._config_path = config_dir / "cava.conf"
        self._config_path.write_text(_build_config())

        self._stop.clear()
        return self._spawn()

    def get_frame(self) -> list[int]:
        """
        Return the latest frame. Non-blocking, never raises. Also runs
        the watchdog — this is the only place restart logic triggers,
        and it's called from the UI tick.
        """
        self._watchdog()
        with self._lock:
            return list(self._latest)

    def stop(self) -> None:
        """Stop the subprocess and reader thread cleanly."""
        self._stop.set()
        self._kill_process()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    # ------ Internals ------

    def _spawn(self) -> bool:
        """Kill any existing process, start a new cava + reader thread."""
        self._kill_process()

        if self._stop.is_set():
            return False

        try:
            self._process = subprocess.Popen(
                ["cava", "-p", str(self._config_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,  # unbuffered; we do our own reads
            )
        except FileNotFoundError:
            self._process = None
            return False

        self._last_frame_time = time.monotonic()
        self._thread = threading.Thread(
            target=self._read_loop,
            daemon=True,
            name="cava-reader",
        )
        self._thread.start()
        return True

    def _kill_process(self) -> None:
        if self._process is None:
            return
        try:
            self._process.terminate()
            try:
                self._process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                self._process.kill()
        except Exception:
            pass
        self._process = None

    def _read_loop(self) -> None:
        """
        Read raw bytes from cava's stdout using select() with a short
        timeout. Wakes up at least twice per second to check _stop and
        the process state, even if cava has gone quiet.
        """
        proc = self._process
        if proc is None or proc.stdout is None:
            return

        fd = proc.stdout.fileno()
        buf = b""

        while not self._stop.is_set():
            if proc.poll() is not None:
                break

            # Wait up to 0.5s for cava to write something.
            try:
                ready, _, _ = select.select([fd], [], [], 0.5)
            except (OSError, ValueError):
                break

            if not ready:
                # Timeout — nothing to read yet, just loop back and
                # re-check stop/process state.
                continue

            try:
                chunk = os.read(fd, self.READ_CHUNK)
            except (OSError, BlockingIOError):
                break

            if not chunk:
                break  # EOF — cava closed its stdout

            buf += chunk

            # Parse every complete frame currently in the buffer.
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line:
                    continue
                try:
                    text = line.decode("ascii", errors="ignore")
                    values = [int(v) for v in text.split(";") if v]
                except ValueError:
                    continue
                if not values:
                    continue
                with self._lock:
                    self._latest = values
                    self._last_frame_time = time.monotonic()

    def _watchdog(self) -> None:
        """Respawn cava if the process died or frames have gone stale."""
        if self._stop.is_set():
            return
        if self._config_path is None:
            return

        now = time.monotonic()

        # Don't respawn more than once every RESTART_BACKOFF_SECONDS —
        # prevents a fork bomb if cava is fundamentally broken.
        if now - self._last_restart < self.RESTART_BACKOFF_SECONDS:
            return

        proc_dead = (
            self._process is None
            or self._process.poll() is not None
        )
        stale = (now - self._last_frame_time) > self.STALE_AFTER_SECONDS

        if proc_dead or stale:
            self._last_restart = now
            self._spawn()
