# ------ Imports ------
import asyncio

from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    DataTable,
    Input,
    Label,
    ListItem,
    ListView,
    Static,
)

from lmtui.applescript import Track


# ------ Widgets ------

class PlaylistItem(ListItem):
    """A single playlist row in a list."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self.playlist_name = name

    def compose(self) -> ComposeResult:
        yield Label(self.playlist_name)


class SearchInput(Input):
    """
    Input that intercepts Escape to clear itself and return focus to
    the tracks table, rather than letting Escape bubble up and close
    the modal.
    """

    BINDINGS = [
        ("escape", "cancel_search", "Cancel"),
    ]

    def action_cancel_search(self) -> None:
        self.value = ""
        # Find our sibling table and focus it.
        table = self.screen.query_one("#library-tracks", DataTable)
        table.focus()


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
    Full-screen library browser.

    Playlists on the left, tracks on the right. Press `/` to filter the
    current playlist's tracks by name, artist, or album. Enter on a
    track starts playback and closes the browser.
    """

    BINDINGS = [
        ("escape", "dismiss_modal", "Close"),
        ("q", "dismiss_modal", "Close"),
        ("slash", "focus_search", "Search"),
    ]

    def __init__(self, controller) -> None:
        super().__init__()
        self.controller = controller
        self._current_playlist: str = ""
        self._all_tracks: list[dict] = []
        self._filter: str = ""

    def compose(self) -> ComposeResult:
        with Horizontal(id="library-layout"):
            with Vertical(id="library-sidebar"):
                yield Static("Playlists", id="library-sidebar-title")
                yield ListView(id="library-playlists")
            with Vertical(id="library-main"):
                yield Static("Select a playlist \u2192", id="library-tracks-title")
                yield SearchInput(
                    placeholder="Filter tracks\u2026  (Esc to clear)",
                    id="library-search",
                )
                yield DataTable(id="library-tracks", cursor_type="row")

    def on_mount(self) -> None:
        table = self.query_one("#library-tracks", DataTable)
        table.add_columns("#", "Title", "Artist", "Album")
        # Search hidden until the user asks for it.
        self.query_one("#library-search").display = False
        asyncio.create_task(self._load_playlists())

    # ------ Search binding guards ------

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        # If the search input has focus, let it handle single-letter
        # keys itself (including `q` which would otherwise close the
        # modal).
        if action == "dismiss_modal":
            if isinstance(self.focused, SearchInput):
                return None
        return True

    # ------ Playlists ------

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

    # ------ Tracks ------

    async def _load_tracks(self, playlist_name: str) -> None:
        title = self.query_one("#library-tracks-title", Static)
        title.update(f"Loading \u201c{playlist_name}\u201d\u2026")

        table = self.query_one("#library-tracks", DataTable)
        table.clear()

        tracks = await self.controller.get_playlist_tracks(playlist_name)
        self._current_playlist = playlist_name
        self._all_tracks = tracks
        self._filter = ""

        # Reset the search field now that we have a new track list.
        search = self.query_one("#library-search", SearchInput)
        search.value = ""
        search.display = False

        self._refresh_table()
        table.focus()

    def _visible_tracks(self) -> list[dict]:
        if not self._filter:
            return self._all_tracks
        f = self._filter
        return [
            t for t in self._all_tracks
            if f in t["name"].lower()
            or f in t["artist"].lower()
            or f in t["album"].lower()
        ]

    def _refresh_table(self) -> None:
        table = self.query_one("#library-tracks", DataTable)
        table.clear()

        visible = self._visible_tracks()
        for t in visible:
            table.add_row(
                str(t["index"]),
                t["name"],
                t["artist"],
                t["album"],
            )

        title = self.query_one("#library-tracks-title", Static)
        if self._filter:
            title.update(
                f"\u201c{self._current_playlist}\u201d "
                f"\u2014 {len(visible)}/{len(self._all_tracks)} "
                f"(filter: {self._filter!r})"
            )
        else:
            title.update(
                f"\u201c{self._current_playlist}\u201d "
                f"\u2014 {len(self._all_tracks)} tracks"
            )

    # ------ Search wiring ------

    def action_focus_search(self) -> None:
        search = self.query_one("#library-search", SearchInput)
        search.display = True
        search.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._filter = event.value.strip().lower()
        self._refresh_table()

    # ------ Row selection ------

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row = event.cursor_row
        visible = self._visible_tracks()
        if row < 0 or row >= len(visible):
            return
        track = visible[row]
        asyncio.create_task(
            self._play_and_close(self._current_playlist, track["index"])
        )

    async def _play_and_close(self, playlist_name: str, index: int) -> None:
        await self.controller.play_playlist_track(playlist_name, index)
        self.dismiss()

    def action_dismiss_modal(self) -> None:
        self.dismiss()
