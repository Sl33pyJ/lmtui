# ------ Imports ------
import asyncio

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Label, ListItem, ListView, Static

from lmtui.applescript import Track


# ------ Widgets ------

class PlaylistItem(ListItem):
    """A single playlist row in a list."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self.playlist_name = name

    def compose(self) -> ComposeResult:
        yield Label(self.playlist_name)


# ------ AddToPlaylist modal ------

class AddToPlaylistScreen(ModalScreen):
    """Overlay for picking a target playlist for the current track."""

    BINDINGS = [
        ("escape", "dismiss_modal", "Cancel"),
    ]

    def __init__(self, track: Track, playlists: list[str]) -> None:
        super().__init__()
        self.track = track
        self.playlists = playlists

    def compose(self) -> ComposeResult:
        with Vertical(id="playlist-dialog"):
            yield Static(
                f"Add \u201c{self.track.name}\u201d to playlist:",
                id="dialog-title",
            )
            with ListView(id="playlist-list"):
                for name in self.playlists:
                    yield PlaylistItem(name)
            yield Static(
                "Enter to confirm \u00b7 Esc to cancel",
                id="dialog-hint",
            )

    def on_mount(self) -> None:
        self.query_one(ListView).focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, PlaylistItem):
            self.dismiss(item.playlist_name)

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)


# ------ LibraryBrowser modal ------

class LibraryBrowserScreen(ModalScreen):
    """
    Full-screen library browser. Playlists on the left, tracks on the
    right. Enter on a playlist loads its tracks. Enter on a track starts
    playback and closes the browser.
    """

    BINDINGS = [
        ("escape", "dismiss_modal", "Close"),
        ("q", "dismiss_modal", "Close"),
    ]

    def __init__(self, controller) -> None:
        super().__init__()
        self.controller = controller
        self._current_playlist: str = ""
        self._current_tracks: list[dict] = []

    def compose(self) -> ComposeResult:
        with Horizontal(id="library-layout"):
            with Vertical(id="library-sidebar"):
                yield Static("Playlists", id="library-sidebar-title")
                yield ListView(id="library-playlists")
            with Vertical(id="library-main"):
                yield Static("Select a playlist \u2192", id="library-tracks-title")
                yield DataTable(id="library-tracks", cursor_type="row")

    def on_mount(self) -> None:
        table = self.query_one("#library-tracks", DataTable)
        table.add_columns("#", "Title", "Artist", "Album")
        asyncio.create_task(self._load_playlists())

    async def _load_playlists(self) -> None:
        names = await self.controller.list_playlists()
        lv = self.query_one("#library-playlists", ListView)
        for name in names:
            lv.append(PlaylistItem(name))
        lv.focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, PlaylistItem):
            asyncio.create_task(self._load_tracks(item.playlist_name))

    async def _load_tracks(self, playlist_name: str) -> None:
        title = self.query_one("#library-tracks-title", Static)
        title.update(f"Loading \u201c{playlist_name}\u201d\u2026")

        table = self.query_one("#library-tracks", DataTable)
        table.clear()

        tracks = await self.controller.get_playlist_tracks(playlist_name)
        self._current_playlist = playlist_name
        self._current_tracks = tracks

        for t in tracks:
            table.add_row(
                str(t["index"]),
                t["name"],
                t["artist"],
                t["album"],
            )

        title.update(f"\u201c{playlist_name}\u201d \u2014 {len(tracks)} tracks")
        table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row = event.cursor_row
        if row < 0 or row >= len(self._current_tracks):
            return
        track = self._current_tracks[row]
        asyncio.create_task(self._play_and_close(self._current_playlist, track["index"]))

    async def _play_and_close(self, playlist_name: str, index: int) -> None:
        await self.controller.play_playlist_track(playlist_name, index)
        self.dismiss()

    def action_dismiss_modal(self) -> None:
        self.dismiss()
