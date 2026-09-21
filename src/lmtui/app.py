# ------ Imports ------
import asyncio
import random
import subprocess

from rich.markup import escape

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import Footer, Header, Input, Label, Static

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


# ------ Queue panel (left) ------

class QueuePanel(Vertical):
    """Queue list: current track on top, then upcoming tracks."""

    current_track: reactive[Track | None] = reactive(None)
    _last_key: str = ""

    def __init__(self, controller: MusicController, **kwargs) -> None:
        super().__init__(**kwargs)
        self.controller = controller

    def compose(self) -> ComposeResult:
        yield Label("♪ Queue", id="queue-title")
        with VerticalScroll(id="queue-scroll"):
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
        tracks = data["tracks"]
        playlist = data["playlist"] or ""
        current = self.current_track

        lines: list[str] = []

        if playlist:
            lines.append(f"[#6c7086]from[/] [#cba6f7]{escape(playlist)}[/]\n")

        if current is not None:
            lines.append("[#6c7086]▶ now playing[/]")
            lines.append(
                f"  [#cba6f7 bold]{escape(current.name)}[/]\n"
                f"  [#cdd6f4]{escape(current.artist)}[/]\n"
            )

        if tracks:
            lines.append("[#6c7086]up next[/]")
            for i, t in enumerate(tracks, start=1):
                name = escape(t["name"])
                artist = escape(t["artist"])
                lines.append(
                    f"  [#6c7086]{i:>2}.[/]  [#a6e3a1]{name}[/]\n"
                    f"        [#6c7086]{artist}[/]"
                )
        else:
            lines.append("[#6c7086](queue empty)[/]")

        content.update("\n".join(lines))


# ------ Visualizer panel (right top) ------

class VisualizerPanel(Vertical):
    """Placeholder for a cava-driven visualizer."""

    def compose(self) -> ComposeResult:
        yield Label("♪ Visualizer", id="viz-title")
        yield Static(
            "[#6c7086]cava integration coming soon\n\n"
            "meanwhile, run[/] [#cba6f7]cava[/] "
            "[#6c7086]in a split pane[/]",
            id="viz-content",
        )


# ------ Lyrics panel (right bottom) ------

class LyricsPanel(Vertical):
    """Placeholder for LRCLIB-fetched lyrics."""

    def compose(self) -> ComposeResult:
        yield Label("♪ Lyrics", id="lyr-title")
        yield Static(
            "[#6c7086]LRCLIB integration coming soon\n\n"
            "synced lyrics will highlight the current line[/]",
            id="lyr-content",
        )


# ------ Small album art (for the bar) ------

class SmallAlbumArt(Static):
    """
    Fixed-size album art for the now-playing bar. 8 cells wide renders
    as 8 pixels square (4 lines tall) via half-blocks.
    """

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


# ------ Now playing bar (bottom) ------

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
        bar_w = 50
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
                yield QueuePanel(self.controller, id="queue-panel")
                with Vertical(id="right-column"):
                    yield VisualizerPanel(id="visualizer-panel")
                    yield LyricsPanel(id="lyrics-panel")
            yield NowPlayingBar(id="now-playing-bar")
        yield Footer()

    def on_mount(self) -> None:
        asyncio.create_task(self.refresh_now_playing())
        self.set_interval(1.0, self.refresh_now_playing)

    def on_unmount(self) -> None:
        self.controller.worker.shutdown()

    async def refresh_now_playing(self) -> None:
        track = await self.controller.now_playing()

        bar = self.query_one(NowPlayingBar)
        bar.track = track

        queue = self.query_one(QueuePanel)
        queue.current_track = track

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
