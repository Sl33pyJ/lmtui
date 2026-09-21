# ------ Imports ------
import asyncio
import random
import subprocess

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal
from textual.reactive import reactive
from textual.widgets import Footer, Header, Input, Label, ProgressBar, Static

from lmtui.applescript import (
    Track,
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


# ------ Shuffle query ------

def _read_shuffle() -> bool:
    """Return True if Music.app's shuffle is currently on."""
    try:
        r = subprocess.run(
            ["osascript", "-e", 'tell application "Music" to get shuffle enabled'],
            capture_output=True, text=True, timeout=3.0,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return r.returncode == 0 and r.stdout.strip().lower() == "true"


# ------ Controller ------

class MusicController:
    """
    Async wrapper around the synchronous AppleScript layer.

    Also owns lmtui's playback state: which playlist we're playing from
    and which index of that playlist is current. This is separate from
    whatever Music.app thinks, because Music.app's own track tracking
    can't distinguish duplicate entries (same name+artist+album).
    """

    def __init__(self) -> None:
        self.worker = MusicWorker()

        # Playback state. None until the user starts a track from
        # lmtui's library browser or queue view.
        self._queue_playlist: str | None = None
        self._queue_order: list[int] = []   # 1-based indices into the playlist
        self._queue_pos: int = -1           # index into _queue_order
        self._shuffle: bool = False

    # ------ Fast operations (default thread pool) ------

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

    # ------ Slow operations (worker thread) ------

    async def get_playlist_tracks(self, name: str) -> list[dict]:
        return await self.worker.run(get_playlist_tracks, name)

    async def remove_playlist_track(self, name: str, index: int) -> bool:
        return await self.worker.run(remove_playlist_track, name, index)

    async def get_queue(self) -> dict:
        return await self.worker.run(get_queue)

    # ------ Playback state ------

    async def jump_to_track(
        self, playlist_name: str, index: int, total_tracks: int
    ) -> bool:
        """
        Play a specific track and set up queue state so subsequent
        next/previous calls are reliable.

        `total_tracks` is needed so we can build the full order for
        shuffle and advance past the end.
        """
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
        """Advance within the lmtui queue; fall back to scanning."""
        if self._can_step(forward=True):
            self._queue_pos += 1
            idx = self._queue_order[self._queue_pos]
            await self.worker.run(
                play_playlist_track, self._queue_playlist, idx
            )
            return

        if self._shuffle and self._queue_playlist:
            # Wrap: reshuffle the tail and restart.
            await self._reshuffle_from_current()
            if self._can_step(forward=True):
                self._queue_pos += 1
                idx = self._queue_order[self._queue_pos]
                await self.worker.run(
                    play_playlist_track, self._queue_playlist, idx
                )
                return

        # No queue state or at the end: fall back.
        ok = await asyncio.to_thread(smart_next)
        if not ok:
            await asyncio.to_thread(next_track)

    async def previous(self) -> None:
        if self._can_step(forward=False):
            self._queue_pos -= 1
            idx = self._queue_order[self._queue_pos]
            await self.worker.run(
                play_playlist_track, self._queue_playlist, idx
            )
            return

        # At the top: restart current track, matching Music.app behavior.
        if self._queue_playlist and 0 <= self._queue_pos:
            idx = self._queue_order[self._queue_pos]
            await self.worker.run(
                play_playlist_track, self._queue_playlist, idx
            )
            return

        ok = await asyncio.to_thread(smart_previous)
        if not ok:
            await asyncio.to_thread(previous_track)

    async def toggle_shuffle(self) -> None:
        """
        Toggle both lmtui's internal shuffle AND Music.app's shuffle so
        the UI reflects reality. Our shuffle regenerates the order of
        the tracks after the current position.
        """
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

    async def _reshuffle_from_current(self) -> None:
        """After reaching the end of a shuffled order, reshuffle from 1."""
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


# ------ Album art ------

class AlbumArt(Static):
    """Renders current track's artwork as Unicode half-blocks."""

    track_key: reactive[str] = reactive("")

    def compose(self) -> ComposeResult:
        yield Static("", id="art-content")

    def watch_track_key(self, key: str) -> None:
        if not key:
            self.query_one("#art-content", Static).update("")
            return
        asyncio.create_task(self._render_art())

    async def _render_art(self) -> None:
        from lmtui.artwork import extract_artwork
        from lmtui.halfblock import image_to_halfblocks

        path = await asyncio.to_thread(extract_artwork)
        if path is None:
            self.query_one("#art-content", Static).update("")
            return

        rendered = await asyncio.to_thread(image_to_halfblocks, path, 40)
        self.query_one("#art-content", Static).update(rendered)


# ------ Now Playing panel ------

class NowPlayingPanel(Static):
    """Displays current track, artist, album, and playback progress."""

    track: reactive[Track | None] = reactive(None)

    def compose(self) -> ComposeResult:
        yield Label("♪ Now Playing", id="np-title")
        yield Static("— nothing playing —", id="np-track")
        yield Static("", id="np-artist")
        yield Static("", id="np-album")
        yield ProgressBar(total=100, show_eta=False, id="np-progress")

    def watch_track(self, track: Track | None) -> None:
        track_w  = self.query_one("#np-track",    Static)
        artist_w = self.query_one("#np-artist",   Static)
        album_w  = self.query_one("#np-album",    Static)
        prog_w   = self.query_one("#np-progress", ProgressBar)

        if track is None:
            track_w.update("— nothing playing —")
            artist_w.update("")
            album_w.update("")
            prog_w.progress = 0
            return

        track_w.update(track.name)
        artist_w.update(track.artist)
        album_w.update(track.album)

        if track.duration > 0:
            pct = (track.position / track.duration) * 100
            prog_w.progress = min(pct, 100.0)
        else:
            prog_w.progress = 0.0


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
        ("a", "add_to_playlist", "Add"),
        ("l", "open_library", "Library"),
        ("u", "open_queue", "Up Next"),
        ("r", "refresh", "Refresh"),
        ("q", "quit", "Quit"),
    ]

    INPUT_SENSITIVE_ACTIONS = {
        "play_pause", "next_track", "previous_track",
        "volume_up", "volume_down", "shuffle",
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
            with Horizontal(id="now-playing-row"):
                yield AlbumArt(id="album-art")
                yield NowPlayingPanel(id="now-playing")
        yield Footer()

    def on_mount(self) -> None:
        asyncio.create_task(self.refresh_now_playing())
        self.set_interval(1.0, self.refresh_now_playing)

    def on_unmount(self) -> None:
        self.controller.worker.shutdown()

    async def refresh_now_playing(self) -> None:
        track = await self.controller.now_playing()

        panel = self.query_one(NowPlayingPanel)
        panel.track = track

        art = self.query_one(AlbumArt)
        if track is None:
            art.track_key = ""
        else:
            art.track_key = f"{track.name}|{track.artist}"

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
        track = self.query_one(NowPlayingPanel).track
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
                severity="warning",
                timeout=4,
            )
            return

        self.notify(f"Adding to \u201c{playlist_name}\u201d\u2026", timeout=2)
        ok = await self.controller.add_current_to_playlist(playlist_name)
        if ok:
            self.notify(
                f"♥  Added to \u201c{playlist_name}\u201d",
                severity="information",
                timeout=3,
            )
        else:
            self.notify("Could not add track", severity="error", timeout=3)

    # ------ Library & queue actions ------

    def action_open_library(self) -> None:
        self.push_screen(LibraryBrowserScreen(self.controller))

    def action_open_queue(self) -> None:
        self.push_screen(QueueScreen(self.controller))
