# ------ Imports ------
import asyncio

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
    """Input that intercepts Escape to clear and return focus to the table."""

    BINDINGS = [
        ("escape", "cancel_search", "Cancel"),
    ]

    def action_cancel_search(self) -> None:
        self.value = ""
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

    Playlists on the left, tracks on the right. Press `/` to filter,
    `d` twice to remove a track from the current playlist, Enter to play.
    """

    BINDINGS = [
        ("escape", "dismiss_modal", "Close"),
        ("q", "dismiss_modal", "Close"),
        ("slash", "focus_search", "Search"),
        ("d", "delete_track", "Remove"),
    ]

    def __init__(self, controller) -> None:
        super().__init__()
        self.controller = controller
        self._current_playlist: str = ""
        self._all_tracks: list[dict] = []
        self._filter: str = ""
        # Two-press delete: first press arms, second press removes.
        self._pending_delete_row: int | None = None

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
        self.query_one("#library-search").display = False
        asyncio.create_task(self._load_playlists())

    # ------ Action guards ------

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        # While typing in the search box, `d` and `q` are just letters,
        # not commands. Suppress them so the user can search for "denzel"
        # or "queen" without triggering actions.
        if isinstance(self.focused, SearchInput):
            if action in ("dismiss_modal", "delete_track"):
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
        self._pending_delete_row = None

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

    # ------ Delete action ------

    def action_delete_track(self) -> None:
        table = self.query_one("#library-tracks", DataTable)
        row = table.cursor_row
        if row < 0:
            return

        visible = self._visible_tracks()
        if row >= len(visible):
            return

        if self._pending_delete_row == row:
            # Second press — commit.
            self._pending_delete_row = None
            asyncio.create_task(self._do_delete(row))
        else:
            # First press — arm, wait for confirmation.
            self._pending_delete_row = row
            name = visible[row]["name"]
            self.notify(
                f"Press d again to remove \u201c{name}\u201d",
                severity="warning",
                timeout=3,
            )
            self.set_timer(3.0, self._disarm_delete)

    def _disarm_delete(self) -> None:
        self._pending_delete_row = None

    async def _do_delete(self, row: int) -> None:
        visible = self._visible_tracks()
        if row >= len(visible):
            return
        track = visible[row]
        playlist = self._current_playlist

        self.notify(f"Removing \u201c{track['name']}\u201d\u2026", timeout=2)
        ok = await self.controller.remove_playlist_track(playlist, track["index"])
        if not ok:
            self.notify("Could not remove track", severity="error", timeout=3)
            return

        self.notify(f"Removed from \u201c{playlist}\u201d", timeout=2)
        # Reload — Music.app re-indexes the playlist after a delete, so
        # our cached indices are stale.
        await self._load_tracks(playlist)

    # ------ Close ------

    def action_dismiss_modal(self) -> None:
        self.dismiss()


# ------ Queue modal ------

class QueueScreen(ModalScreen):
    """
    Shows the tracks after the current one in the playing playlist.

    Note: this is playlist order, not true playback order. In shuffle
    mode the actual order will differ — the header reflects that.
    """

    BINDINGS = [
        ("escape", "dismiss_modal", "Close"),
        ("q", "dismiss_modal", "Close"),
        ("r", "refresh_queue", "Refresh"),
    ]

    def __init__(self, controller) -> None:
        super().__init__()
        self.controller = controller
        self._playlist: str = ""
        self._tracks: list[dict] = []
        self._shuffled: bool = False

    def compose(self) -> ComposeResult:
        with Vertical(id="queue-layout"):
            yield Static("Up Next", id="queue-title")
            yield Static("Loading\u2026", id="queue-subtitle")
            yield DataTable(id="queue-tracks", cursor_type="row")

    def on_mount(self) -> None:
        table = self.query_one("#queue-tracks", DataTable)
        table.add_columns("#", "Title", "Artist", "Album")
        asyncio.create_task(self._load_queue())

    async def _load_queue(self) -> None:
        subtitle = self.query_one("#queue-subtitle", Static)
        table = self.query_one("#queue-tracks", DataTable)

        subtitle.update("Loading\u2026")
        table.clear()

        # Shuffle state is cheap to query — read it in parallel with the
        # slower queue fetch.
        shuffled = await self.controller.shuffle_enabled()

        data = await self.controller.get_queue()
        self._playlist = data["playlist"] or ""
        self._tracks = data["tracks"]
        self._shuffled = shuffled

        if not self._playlist:
            subtitle.update(
                "No queue available \u2014 nothing playing or no "
                "playlist context"
            )
            return

        for t in self._tracks:
            table.add_row(
                str(t["index"]),
                t["name"],
                t["artist"],
                t["album"],
            )

        suffix = " \u00b7 shuffled (order may differ)" if self._shuffled else ""
        subtitle.update(
            f"From \u201c{self._playlist}\u201d \u2014 "
            f"{len(self._tracks)} upcoming{suffix}"
        )
        table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row = event.cursor_row
        if row < 0 or row >= len(self._tracks):
            return
        track = self._tracks[row]
        asyncio.create_task(self._jump_to(track["index"]))

    async def _jump_to(self, index: int) -> None:
        ok = await self.controller.play_playlist_track(self._playlist, index)
        if not ok:
            self.notify("Could not jump to track", severity="error", timeout=3)
            return
        # Reload — the current track has changed, so "upcoming" is now
        # different.
        await self._load_queue()

    def action_refresh_queue(self) -> None:
        asyncio.create_task(self._load_queue())

    def action_dismiss_modal(self) -> None:
        self.dismiss()
