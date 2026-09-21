# ------ Imports ------
import asyncio
import concurrent.futures
import queue
import threading
from typing import Any, Callable


# ------ Worker thread ------

class MusicWorker:
    """
    Serializes AppleScript calls that hit Music.app.

    AppleScript is synchronous and can block for seconds on library
    queries. Rather than spawning a thread per call (which grows
    unbounded under rapid navigation), we run one worker thread that
    processes requests in FIFO order. Callers await the result through
    a Future, so from their perspective this is just an async function.

    Long-running calls also don't starve the default asyncio thread
    pool, which stays free for the fast now-playing poll and playback
    controls.
    """

    def __init__(self) -> None:
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(
            target=self._run,
            name="music-worker",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        while True:
            func, args, future = self._queue.get()
            if func is None:  # shutdown sentinel
                break
            try:
                result = func(*args)
                future.set_result(result)
            except Exception as exc:
                future.set_exception(exc)

    async def run(self, func: Callable, *args: Any) -> Any:
        """
        Submit `func(*args)` to the worker thread and await its result.

        Safe to call from any async context. If the caller is cancelled,
        the request stays in the queue (AppleScript cannot be interrupted
        mid-call), but the Future is discarded so the result goes nowhere.
        """
        future: concurrent.futures.Future = concurrent.futures.Future()
        self._queue.put((func, args, future))
        return await asyncio.wrap_future(future)

    def shutdown(self) -> None:
        """Stop the worker thread. Called when the app is quitting."""
        self._queue.put((None, (), None))
