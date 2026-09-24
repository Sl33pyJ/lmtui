# ------ Imports ------
import asyncio
import math
import random
import subprocess
import textwrap
import time

from rich.markup import escape

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Footer, Header, Input, Static

from lmtui.applescript import (
    Track,
    cycle_repeat,
    get_now_playing,
    next_track,
    play_pause,
    previous_track,
    toggle_shuffle,
    volume_down,
    volume_up,
)
from lmtui.lyrics import Lyrics, LyricLine, fetch_lyrics
from lmtui.library import (
    add_current_to_playlist,
    current_track_count_in,
    get_playlist_tracks,
    list_playlists,
    play_playlist_track,
    remove_playlist_track,
    smart_next,
    smart_previous,
)
from lmtui.queue import get_queue
from lmtui.screens import (
    AddToPlaylistScreen,
    LibraryBrowserScreen,
    QueueScreen,
)
from lmtui.worker import MusicWorker
from lmtui.visualizer import CavaVisualizer


# ------ Layout thresholds ------

COMPACT_WIDTH = 100
COMPACT_HEIGHT = 32
MINIMAL_HEIGHT = 22


# ------ Helpers ------

def _fmt_time(seconds: int) -> str:
    if seconds < 0:
        seconds = 0
    m, s = divmod(seconds, 60)
    return f"{m}:{s:02d}"


def _read_shuffle() -> bool:
    try:
        r = subprocess.run(
            ["osascript", "-e", 'tell application "Music" to get shuffle enabled'],
            capture_output=True, text=True, timeout=3.0,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return r.returncode == 0 and r.stdout.strip().lower() == "true"


def _read_repeat() -> str:
    try:
        r = subprocess.run(
            ["osascript", "-e", 'tell application "Music" to get song repeat'],
            capture_output=True, text=True, timeout=3.0,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "off"
    if r.returncode != 0:
        return "off"
    val = r.stdout.strip().lower()
    return val if val in ("off", "all", "one") else "off"


# ------ Controller ------

class MusicController:
    """Async wrapper around AppleScript. Owns playback state for reliable next/prev."""

    def __init__(self) -> None:
        self.worker = MusicWorker()

        self._queue_playlist: str | None = None
        self._queue_order: list[int] = []
        self._queue_pos: int = -1
        self._shuffle: bool = False
        self._repeat: str = "off"

    async def now_playing(self) -> Track | None:
        return await asyncio.to_thread(get_now_playing)

    async def play_pause(self) -> None:
        await asyncio.to_thread(play_pause)

    async def volume_up(self) -> None:
        await asyncio.to_thread(volume_up)

    async def volume_down(self) -> None:
        await asyncio.to_thread(volume_down)

    async def list_playlists(self) -> list[str]:
        return await asyncio.to_thread(list_playlists)

    async def add_current_to_playlist(self, name: str) -> bool:
        return await asyncio.to_thread(add_current_to_playlist, name)

    async def current_track_count_in(self, name: str) -> int:
        return await asyncio.to_thread(current_track_count_in, name)

    async def shuffle_enabled(self) -> bool:
        return await asyncio.to_thread(_read_shuffle)

    async def get_playlist_tracks(self, name: str) -> list[dict]:
        return await self.worker.run(get_playlist_tracks, name)

    async def remove_playlist_track(self, name: str, index: int) -> bool:
        return await self.worker.run(remove_playlist_track, name, index)

    async def get_queue(self) -> dict:
        return await self.worker.run(get_queue)

    async def jump_to_track(
        self, playlist_name: str, index: int, total_tracks: int
    ) -> bool:
        indices = list(range(1, total_tracks + 1))

        if self._shuffle and total_tracks > 1:
            rest = [i for i in indices if i != index]
            random.shuffle(rest)
            order = [index] + rest
            pos = 0
        else:
            order = indices
            pos = index - 1

        self._queue_playlist = playlist_name
        self._queue_order = order
        self._queue_pos = pos

        return await self.worker.run(play_playlist_track, playlist_name, index)

    async def next(self) -> None:
        if self._can_step(forward=True):
            self._queue_pos += 1
            idx = self._queue_order[self._queue_pos]
            await self.worker.run(play_playlist_track, self._queue_playlist, idx)
            return

        if self._shuffle and self._queue_playlist:
            await self._reshuffle_from_current()
            if self._can_step(forward=True):
                self._queue_pos += 1
                idx = self._queue_order[self._queue_pos]
                await self.worker.run(play_playlist_track, self._queue_playlist, idx)
                return

        if self._repeat == "all" and self._queue_playlist and self._queue_order:
            self._queue_pos = 0
            idx = self._queue_order[0]
            await self.worker.run(play_playlist_track, self._queue_playlist, idx)
            return

        ok = await asyncio.to_thread(smart_next)
        if not ok:
            await asyncio.to_thread(next_track)

    async def previous(self) -> None:
        if self._can_step(forward=False):
            self._queue_pos -= 1
            idx = self._queue_order[self._queue_pos]
            await self.worker.run(play_playlist_track, self._queue_playlist, idx)
            return

        if self._queue_playlist and 0 <= self._queue_pos:
            idx = self._queue_order[self._queue_pos]
            await self.worker.run(play_playlist_track, self._queue_playlist, idx)
            return

        ok = await asyncio.to_thread(smart_previous)
        if not ok:
            await asyncio.to_thread(previous_track)

    async def toggle_shuffle(self) -> None:
        await asyncio.to_thread(toggle_shuffle)
        self._shuffle = not self._shuffle

        if not self._queue_playlist or self._queue_pos < 0:
            return

        current = self._queue_order[self._queue_pos]
        all_indices = list(range(1, len(self._queue_order) + 1))

        if self._shuffle:
            rest = [i for i in all_indices if i != current]
            random.shuffle(rest)
            self._queue_order = [current] + rest
            self._queue_pos = 0
        else:
            self._queue_order = all_indices
            self._queue_pos = current - 1

    async def cycle_repeat(self) -> None:
        await asyncio.to_thread(cycle_repeat)
        self._repeat = await asyncio.to_thread(_read_repeat)

    async def _reshuffle_from_current(self) -> None:
        if not self._queue_playlist:
            return
        n = len(self._queue_order)
        order = list(range(1, n + 1))
        random.shuffle(order)
        self._queue_order = order
        self._queue_pos = 0

    def _can_step(self, *, forward: bool) -> bool:
        if not self._queue_playlist or not self._queue_order:
            return False
        if self._queue_pos < 0:
            return False
        if forward:
            return self._queue_pos < len(self._queue_order) - 1
        return self._queue_pos > 0


# ------ Queue panel ------

class QueuePanel(Vertical):
    """
    Queue list: current track on top, then upcoming tracks.

    Auto-heights to content. In minimal mode (very short terminal)
    it collapses further.
    """

    current_track: reactive[Track | None] = reactive(None)
    _last_key: str = ""
    _cached_data: dict | None = None

    def __init__(self, controller: MusicController, **kwargs) -> None:
        super().__init__(**kwargs)
        self.controller = controller

    def compose(self) -> ComposeResult:
        yield Static("", id="queue-content")

    def watch_current_track(self, track: Track | None) -> None:
        key = f"{track.name}|{track.artist}" if track else ""
        if key == self._last_key:
            return
        self._last_key = key
        asyncio.create_task(self._load_queue())

    async def _load_queue(self) -> None:
        content = self.query_one("#queue-content", Static)
        content.update("[#6c7086]loading…[/]")

        data = await self.controller.get_queue()
        self._cached_data = data
        self._render_from_cache()

    def _render_from_cache(self) -> None:
        if self._cached_data is None:
            return

        data = self._cached_data
        tracks = data["tracks"]
        playlist = data["playlist"] or ""
        current = self.current_track

        lines: list[str] = []

        if playlist:
            lines.append(f"[#6c7086]from[/] [#cba6f7]{escape(playlist)}[/]")

        if current is not None:
            if lines:
                lines.append("")
            lines.append("[#6c7086]▶ now playing[/]")
            lines.append(f"  [#f5c2e7 bold]{escape(current.name)}[/]")
            lines.append(f"  [#cdd6f4]{escape(current.artist)}[/]")
        else:
            lines.append("[#6c7086]— nothing playing —[/]")

        if tracks:
            lines.append("")
            lines.append("[#6c7086]up next[/]")
            for i, t in enumerate(tracks, start=1):
                name = escape(t["name"])
                artist = escape(t["artist"])
                lines.append(f"  [#6c7086]{i:>2}.[/]  [#a6e3a1]{name}[/]")
                lines.append(f"        [#6c7086]{artist}[/]")

        content = self.query_one("#queue-content", Static)
        content.update("\n".join(lines))


# ------ Visualizer panel ------

class VisualizerPanel(Vertical):
    """
    Audio visualizer. Reads the box dimensions on every frame so the
    bars always exactly fill the panel. Forces a redraw on resize so
    the bars don't render at a stale size.
    """

    FPS = 30
    PEAK_HEIGHT_FRACTION = 0.9

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._cava = CavaVisualizer()
        self._running = False
        self._scale_max: float = 30.0
        self._last_values: list[int] = []

    def compose(self) -> ComposeResult:
        yield Static("", id="viz-content")

    def on_mount(self) -> None:
        if self._cava.start():
            self._running = True
            self.set_interval(1.0 / self.FPS, self._tick)
        else:
            self.query_one("#viz-content", Static).update(
                "[#f38ba8]cava not found[/]\n\n"
                "[#6c7086]Install it with:[/] "
                "[#cba6f7]brew install cava[/]"
            )

    def on_resize(self, event) -> None:
        # Force a redraw with the last known values so the bars
        # resize instantly with the window instead of waiting for
        # the next cava frame.
        if self._last_values:
            content = self.query_one("#viz-content", Static)
            content.update(self._render_frame(self._last_values))

    def on_unmount(self) -> None:
        if self._running:
            self._cava.stop()

    def _tick(self) -> None:
        values = self._cava.get_frame()
        if not values:
            return
        self._last_values = values
        content = self.query_one("#viz-content", Static)
        content.update(self._render_frame(values))

    def _render_frame(self, values: list[int]) -> str:
        if not values:
            return ""

        try:
            size = self.query_one("#viz-content", Static).content_size
        except Exception:
            return ""
        width, height = size.width, size.height
        if width < 4 or height < 2:
            return ""

        # Resample cava output to exactly `width` columns.
        step = len(values) / width
        sampled = [
            values[min(int(i * step), len(values) - 1)]
            for i in range(width)
        ]

        # Auto-scale: rolling max with slow decay.
        frame_max = float(max(sampled)) if sampled else 1.0
        self._scale_max = max(self._scale_max * 0.98, frame_max, 1.0)

        scaled = [
            math.sqrt(min(v / self._scale_max, 1.0))
            for v in sampled
        ]

        def color_for(row: int) -> str:
            if height <= 1:
                return "#a6e3a1"
            t = 1.0 - (row / (height - 1))
            if t < 0.25: return "#a6e3a1"
            if t < 0.50: return "#f9e2af"
            if t < 0.75: return "#fab387"
            return "#f38ba8"

        peak_h = height * self.PEAK_HEIGHT_FRACTION
        lines: list[str] = []
        for row in range(height):
            dist_from_bottom = height - row
            color = color_for(row)
            cells: list[str] = []
            for norm in scaled:
                bar_h = norm * peak_h
                if bar_h >= dist_from_bottom:
                    cells.append(f"[{color}]\u2588[/]")
                elif bar_h >= dist_from_bottom - 0.5:
                    cells.append(f"[{color}]\u2584[/]")
                elif bar_h >= dist_from_bottom - 0.75:
                    cells.append(f"[{color}]\u2581[/]")
                else:
                    cells.append(" ")
            lines.append("".join(cells))
        return "\n".join(lines)


# ------ Lyrics panel ------

class LyricsPanel(Vertical):
    """
    Displays lyrics for the current track with a track header.
    Re-wraps and re-windows on every tick so it always fits.
    """

    FPS = 4

    def __init__(self, controller, **kwargs) -> None:
        super().__init__(**kwargs)
        self.controller = controller

        self._lyrics: Lyrics | None = None
        self._track_key: str = ""
        self._base_pos: float = 0.0
        self._base_time: float = 0.0
        self._last_highlight: int = -1
        self._last_size: tuple[int, int] = (0, 0)
        self._fetching: bool = False

    def compose(self) -> ComposeResult:
        yield Static("♪ Lyrics", id="lyr-header")
        yield Static("", id="lyr-content")

    def on_mount(self) -> None:
        self.set_interval(1.0 / self.FPS, self._tick)

    def on_resize(self, event) -> None:
        # Force re-wrap on resize.
        if self._lyrics is not None and self._lyrics.synced:
            try:
                size = self.query_one("#lyr-content", Static).content_size
                self._last_size = (size.width, size.height)
            except Exception:
                pass
            self.query_one("#lyr-content", Static).update(
                self._render_synced(self._lyrics, self._last_highlight)
            )

    def update_track(self, track: Track | None) -> None:
        header = self.query_one("#lyr-header", Static)

        if track is None:
            if self._track_key:
                self._track_key = ""
                self._lyrics = None
                header.update("♪ Lyrics")
                self.query_one("#lyr-content", Static).update(
                    "[#6c7086]— nothing playing —[/]"
                )
            return

        key = f"{track.name}|{track.artist}"
        header.update(f"[#cba6f7]♪  {escape(track.name)}  —  {escape(track.artist)}[/]")

        if key == self._track_key:
            self._base_pos = float(track.position)
            self._base_time = time.monotonic()
            return

        self._track_key = key
        self._base_pos = float(track.position)
        self._base_time = time.monotonic()
        self._lyrics = None
        self._last_highlight = -1
        self.query_one("#lyr-content", Static).update(
            "[#6c7086]loading…[/]"
        )

        if not self._fetching:
            asyncio.create_task(self._load(track))

    async def _load(self, track: Track) -> None:
        self._fetching = True
        try:
            result = await asyncio.to_thread(
                fetch_lyrics,
                track.name,
                track.artist,
                track.album,
                track.duration,
            )
        finally:
            self._fetching = False

        if result is None:
            self.query_one("#lyr-content", Static).update(
                "[#6c7086](no lyrics found)[/]"
            )
            return

        self._lyrics = result
        self._last_highlight = 0
        self._render_static(result)

    def _render_static(self, lyrics: Lyrics) -> None:
        content = self.query_one("#lyr-content", Static)
        if lyrics.synced:
            content.update(self._render_synced(lyrics, 0))
        else:
            text = "\n".join(escape(line.text) for line in lyrics.lines)
            content.update(f"[#cdd6f4]{text}[/]")

    def _tick(self) -> None:
        if self._lyrics is None or not self._lyrics.synced:
            return

        pos = self._base_pos + (time.monotonic() - self._base_time)
        idx = self._current_line_index(self._lyrics.lines, pos)

        try:
            size = self.query_one("#lyr-content", Static).content_size
            current_size = (size.width, size.height)
        except Exception:
            current_size = (0, 0)

        if idx != self._last_highlight or current_size != self._last_size:
            self._last_highlight = idx
            self._last_size = current_size
            self.query_one("#lyr-content", Static).update(
                self._render_synced(self._lyrics, idx)
            )

    @staticmethod
    def _current_line_index(lines: list[LyricLine], pos: float) -> int:
        idx = 0
        for i, line in enumerate(lines):
            if line.time <= pos:
                idx = i
            else:
                break
        return idx

    def _render_synced(self, lyrics: Lyrics, current_idx: int) -> str:
        try:
            size = self.query_one("#lyr-content", Static).content_size
            width = max(size.width, 20)
            height = max(size.height, 4)
        except Exception:
            width, height = 60, 20

        wrapped: list[tuple[int, str]] = []
        for i, line in enumerate(lyrics.lines):
            chunks = textwrap.wrap(
                line.text, width=width - 2, break_long_words=False
            ) or [""]
            for chunk in chunks:
                wrapped.append((i, chunk))

        if not wrapped:
            return ""

        current_visual = 0
        for vi, (orig_i, _) in enumerate(wrapped):
            if orig_i <= current_idx:
                current_visual = vi
            else:
                break

        window = height
        half = window // 2
        start = max(0, current_visual - half)
        end = min(len(wrapped), start + window)
        if end - start < window and start > 0:
            start = max(0, end - window)

        out: list[str] = []
        for vi in range(start, end):
            orig_i, text = wrapped[vi]
            text_esc = escape(text)
            if orig_i == current_idx:
                out.append(f"[#cba6f7 bold]{text_esc}[/]")
            elif orig_i < current_idx:
                out.append(f"[#6c7086]{text_esc}[/]")
            else:
                out.append(f"[#cdd6f4]{text_esc}[/]")

        return "\n".join(out)


# ------ Small album art ------

class SmallAlbumArt(Static):
    """Fixed-size album art for the now-playing bar."""

    track_key: reactive[str] = reactive("")
    WIDTH_CELLS = 8

    def compose(self) -> ComposeResult:
        yield Static("", id="np-art-content")

    def watch_track_key(self, key: str) -> None:
        if not key:
            self.query_one("#np-art-content", Static).update("")
            return
        asyncio.create_task(self._load_art())

    async def _load_art(self) -> None:
        from lmtui.artwork import extract_artwork
        from lmtui.halfblock import image_to_halfblocks

        path = await asyncio.to_thread(extract_artwork)
        if path is None:
            self.query_one("#np-art-content", Static).update("")
            return

        rendered = await asyncio.to_thread(
            image_to_halfblocks, path, self.WIDTH_CELLS
        )
        self.query_one("#np-art-content", Static).update(rendered)


# ------ Now playing bar ------

class NowPlayingBar(Container):
    """Full-width bottom strip: small art + track info + progress + state."""

    track: reactive[Track | None] = reactive(None)

    def compose(self) -> ComposeResult:
        with Horizontal(id="np-bar-inner"):
            yield SmallAlbumArt(id="np-art")
            with Vertical(id="np-bar-text"):
                yield Static("", id="np-line1")
                yield Static("", id="np-line2")

    def watch_track(self, track: Track | None) -> None:
        line1 = self.query_one("#np-line1", Static)
        line2 = self.query_one("#np-line2", Static)
        art = self.query_one(SmallAlbumArt)

        if track is None:
            line1.update("[#6c7086]▶  — nothing playing —[/]")
            line2.update("")
            art.track_key = ""
            return

        art.track_key = f"{track.name}|{track.artist}"

        shuf = (
            "[#a6e3a1]⇄ on[/]" if track.shuffle
            else "[#6c7086]⇄ off[/]"
        )
        rep_state = track.repeat or "off"
        rep = (
            f"[#a6e3a1]⟳ {rep_state}[/]" if rep_state != "off"
            else "[#6c7086]⟳ off[/]"
        )
        src = (
            f"   [#6c7086]from[/] [#cba6f7]{escape(track.playlist)}[/]"
            if track.playlist else ""
        )

        line1.update(
            f"[#cba6f7]▶[/]  "
            f"[#a6e3a1]{escape(track.name)}[/]  "
            f"[#6c7086]—[/]  "
            f"[#cdd6f4]{escape(track.artist)}[/]"
            f"{src}      {shuf}  {rep}"
        )

        pos = track.position
        dur = track.duration
        # Progress bar width adapts to the available cell width,
        # so on a narrow terminal the bar shrinks instead of
        # overflowing.
        try:
            bar_w = max(10, min(50, self.size.width - 30))
        except Exception:
            bar_w = 30
        if dur > 0:
            filled = int((pos / dur) * bar_w)
            filled = max(0, min(filled, bar_w))
        else:
            filled = 0
        bar = (
            "[#cba6f7]" + "▬" * filled + "[/]"
            + "[#45475a]" + "░" * (bar_w - filled) + "[/]"
        )

        line2.update(
            f"[#6c7086]{_fmt_time(pos):>4}[/]  {bar}  [#6c7086]{_fmt_time(dur)}[/]"
        )


# ------ Main application ------

class LmTuiApp(App):
    """The Lossless Music TUI."""

    CSS_PATH = "styles.tcss"
    TITLE = "lmtui"
    SUB_TITLE = "Lossless Music TUI"

    BINDINGS = [
        ("space", "play_pause", "Play/Pause"),
        ("n", "next_track", "Next"),
        ("p", "previous_track", "Prev"),
        ("]", "volume_up", "Vol +"),
        ("[", "volume_down", "Vol -"),
        ("s", "shuffle", "Shuffle"),
        ("r", "repeat", "Repeat"),
        ("a", "add_to_playlist", "Add"),
        ("l", "open_library", "Library"),
        ("u", "open_queue", "Queue"),
        ("ctrl+r", "refresh", "Refresh"),
        ("q", "quit", "Quit"),
    ]

    INPUT_SENSITIVE_ACTIONS = {
        "play_pause", "next_track", "previous_track",
        "volume_up", "volume_down", "shuffle", "repeat",
        "add_to_playlist", "open_library", "open_queue", "refresh",
    }

    def __init__(self) -> None:
        super().__init__()
        self.controller = MusicController()

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        if action in self.INPUT_SENSITIVE_ACTIONS:
            if isinstance(self.focused, Input):
                return None
        return True

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="main"):
            with Horizontal(id="body-row"):
                with Vertical(id="left-panel"):
                    yield QueuePanel(self.controller, id="queue-panel")
                    yield VisualizerPanel(id="visualizer-panel")
                with Vertical(id="right-column"):
                    yield LyricsPanel(self.controller, id="lyrics-panel")
            yield NowPlayingBar(id="now-playing-bar")
        yield Footer()

    def on_mount(self) -> None:
        self._apply_layout_classes()
        asyncio.create_task(self.refresh_now_playing())
        self.set_interval(1.0, self.refresh_now_playing)

    def on_resize(self, event) -> None:
        self._apply_layout_classes()

    def _apply_layout_classes(self) -> None:
        """Toggle .compact / .minimal on the screen based on size."""
        w = self.size.width
        h = self.size.height
        screen = self.screen

        if w < COMPACT_WIDTH or h < COMPACT_HEIGHT:
            screen.add_class("compact")
        else:
            screen.remove_class("compact")

        if h < MINIMAL_HEIGHT:
            screen.add_class("minimal")
        else:
            screen.remove_class("minimal")

    def on_unmount(self) -> None:
        self.controller.worker.shutdown()

    async def refresh_now_playing(self) -> None:
        track = await self.controller.now_playing()

        bar = self.query_one(NowPlayingBar)
        bar.track = track

        queue = self.query_one(QueuePanel)
        queue.current_track = track

        lyrics = self.query_one(LyricsPanel)
        lyrics.update_track(track)

    # ------ Control actions ------

    def action_refresh(self) -> None:
        asyncio.create_task(self.refresh_now_playing())

    def action_play_pause(self) -> None:
        asyncio.create_task(self._control(self.controller.play_pause))

    def action_next_track(self) -> None:
        asyncio.create_task(self._control(self.controller.next))

    def action_previous_track(self) -> None:
        asyncio.create_task(self._control(self.controller.previous))

    def action_shuffle(self) -> None:
        asyncio.create_task(self._control(self.controller.toggle_shuffle))

    def action_repeat(self) -> None:
        asyncio.create_task(self._control(self.controller.cycle_repeat))

    def action_volume_up(self) -> None:
        asyncio.create_task(self._control(self.controller.volume_up))

    def action_volume_down(self) -> None:
        asyncio.create_task(self._control(self.controller.volume_down))

    async def _control(self, action) -> None:
        await action()
        await self.refresh_now_playing()

    # ------ Add-to-playlist action ------

    def action_add_to_playlist(self) -> None:
        asyncio.create_task(self._open_playlist_picker())

    async def _open_playlist_picker(self) -> None:
        track = self.query_one(NowPlayingBar).track
        if track is None:
            self.notify("Nothing playing", severity="warning", timeout=2)
            return

        playlists = await self.controller.list_playlists()
        if not playlists:
            self.notify("No playlists found", severity="warning", timeout=2)
            return

        self.push_screen(
            AddToPlaylistScreen(track, playlists),
            callback=self._on_playlist_picked,
        )

    def _on_playlist_picked(self, playlist_name: str | None) -> None:
        if playlist_name is None:
            return
        asyncio.create_task(self._add_to_playlist(playlist_name))

    async def _add_to_playlist(self, playlist_name: str) -> None:
        count = await self.controller.current_track_count_in(playlist_name)
        if count > 0:
            plural = "copy" if count == 1 else "copies"
            self.notify(
                f"Already in \u201c{playlist_name}\u201d ({count} {plural})",
                severity="warning", timeout=4,
            )
            return

        self.notify(f"Adding to \u201c{playlist_name}\u201d\u2026", timeout=2)
        ok = await self.controller.add_current_to_playlist(playlist_name)
        if ok:
            self.notify(
                f"♥  Added to \u201c{playlist_name}\u201d",
                severity="information", timeout=3,
            )
        else:
            self.notify("Could not add track", severity="error", timeout=3)

    # ------ Library & queue actions ------

    def action_open_library(self) -> None:
        self.push_screen(LibraryBrowserScreen(self.controller))

    def action_open_queue(self) -> None:
        self.push_screen(QueueScreen(self.controller))
