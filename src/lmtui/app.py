# ------ Imports ------
import asyncio

from textual.app import App, ComposeResult
from textual.containers import Container
from textual.reactive import reactive
from textual.widgets import Footer, Header, Label, ProgressBar, Static

from lmtui.applescript import Track, get_now_playing


# ------ Controller ------

class MusicController:
    """Async wrapper around the synchronous AppleScript layer."""

    async def now_playing(self) -> Track | None:
        return await asyncio.to_thread(get_now_playing)


# ------ Now Playing panel ------

class NowPlayingPanel(Static):
    """Displays the current track, artist, album, and playback progress."""

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
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.controller = MusicController()

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="main"):
            yield NowPlayingPanel(id="now-playing")
        yield Footer()

    def on_mount(self) -> None:
        asyncio.create_task(self.refresh_now_playing())
        self.set_interval(1.0, self.refresh_now_playing)

    async def refresh_now_playing(self) -> None:
        track = await self.controller.now_playing()
        panel = self.query_one(NowPlayingPanel)
        panel.track = track

    def action_refresh(self) -> None:
        asyncio.create_task(self.refresh_now_playing())
