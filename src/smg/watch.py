"""File watcher that auto-rescans changed files."""
from __future__ import annotations

import fnmatch
import threading
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from smg.diff import diff_graphs
from smg.langs import get_extractor, load_extractors
from smg.scan import DEFAULT_EXCLUDES, _strip_extension, scan_paths
from smg.storage import load_graph, save_graph


class _ScanHandler(FileSystemEventHandler):
    """Collects changed files and triggers debounced rescans."""

    def __init__(self, root: Path, debounce: float = 0.5) -> None:
        self.root = root
        self.debounce = debounce
        self._pending: set[Path] = set()
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._callback: callable | None = None

    def on_callback(self, callback: callable) -> None:
        self._callback = callback

    def _is_supported(self, path: str) -> bool:
        p = Path(path)
        for part in p.parts:
            if any(fnmatch.fnmatch(part, pat) for pat in DEFAULT_EXCLUDES):
                return False
            if part.startswith("."):
                return False
        return _strip_extension(p.name) is not None

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if self._is_supported(event.src_path):
            self._schedule(Path(event.src_path))

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if self._is_supported(event.src_path):
            self._schedule(Path(event.src_path))

    def on_deleted(self, event: FileSystemEvent) -> None:
        pass

    def _schedule(self, path: Path) -> None:
        with self._lock:
            self._pending.add(path)
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce, self._flush)
            self._timer.daemon = True
            self._timer.start()

    def _flush(self) -> None:
        with self._lock:
            files = list(self._pending)
            self._pending.clear()
            self._timer = None
        if files and self._callback:
            self._callback(files)


def watch_and_scan(
    root: Path,
    paths: list[Path],
    on_scan: callable | None = None,
    debounce: float = 0.5,
) -> None:
    """Watch paths for changes and rescan modified files.

    Blocks until interrupted (Ctrl+C). Calls on_scan(diff, files) after each
    rescan cycle, where diff is a GraphDiff showing the net structural change.
    """
    load_extractors()

    handler = _ScanHandler(root, debounce=debounce)

    def do_rescan(files: list[Path]) -> None:
        old_graph = load_graph(root)
        graph = load_graph(root)
        stats = scan_paths(graph, root, files, clean=True)
        save_graph(graph, root)
        result = diff_graphs(old_graph, graph)
        if on_scan:
            on_scan(result, stats, files)

    handler.on_callback(do_rescan)

    observer = Observer()
    for path in paths:
        observer.schedule(handler, str(path), recursive=True)

    observer.start()
    try:
        while observer.is_alive():
            observer.join(timeout=1)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()
