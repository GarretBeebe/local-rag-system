"""
Filesystem watcher for continuous background document indexing.

On startup, performs an initial scan of all configured watch paths, then
keeps watching for file system events. File changes are deduplicated by
SHA-256 hash before being passed to the ingest pipeline.

Watch paths, allowed extensions, and ignore patterns are configured in
config/watcher_config.container.yaml (set via CONFIG_PATH env var).
"""

import ctypes
import gc
import hashlib
import logging
import sys
import threading
import time
from collections.abc import Generator
from contextlib import suppress
from pathlib import Path
from queue import Queue
from typing import Any

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers.polling import PollingObserver

from common.config import load_yaml_config
from common.index_state import bump_index_version
from common.index_state import init_db as init_index_state
from common.paths import (
    is_indexable_path,
    matches_ignore_pattern,
    normalize_extensions,
    normalize_path,
)
from common.types import IndexDecision
from indexer.fingerprint_store import get_hash, init_db, upsert_hash
from ingest.cleanup_stale import cleanup_stale
from ingest.index_documents import index_file, remove_indexed_document
from settings import ALLOWED_EXTENSIONS, CONFIG_PATH, WATCHER_POLL_INTERVAL_SECONDS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def load_config() -> dict[str, Any]:
    try:
        return load_yaml_config(CONFIG_PATH)
    except FileNotFoundError:
        raise RuntimeError(f"Config file not found: {CONFIG_PATH}") from None
    except ValueError as e:
        raise RuntimeError(str(e)) from e
    except Exception as e:
        raise RuntimeError(f"Failed to parse config file {CONFIG_PATH}: {e}") from e


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def _index_if_changed(path: str) -> IndexDecision:
    """Check whether a file needs indexing and index it if so."""
    p = Path(path)
    if not p.exists():
        return IndexDecision.MISSING
    try:
        file_hash = sha256_file(p)
        prev_hash = get_hash(path)
        if prev_hash == file_hash:
            return IndexDecision.UNCHANGED
        logger.info("Indexing %s", path)
        outcome = index_file(p)
        if outcome == IndexDecision.INDEXED:
            upsert_hash(path, file_hash)
            bump_index_version()
            return IndexDecision.INDEXED
        if outcome == IndexDecision.SKIPPED:
            if prev_hash is not None:
                logger.info("Removing stale vectors for %s (now unindexable)", path)
                remove_indexed_document(path)
                bump_index_version()
            else:
                logger.info("Skipped %s (never indexed)", path)
            return IndexDecision.SKIPPED
        logger.warning("Failed to index %s — fingerprint not updated", path)
        return IndexDecision.FAILED
    except Exception as e:
        logger.error("Error indexing %s: %s", path, e)
        return IndexDecision.FAILED


class IndexWorker:

    def __init__(self):
        self._queue: Queue[str | None] = Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def submit(self, path: str) -> None:
        self._queue.put(path)

    def stop(self) -> None:
        """Signal the worker to stop after processing any queued paths."""
        # Wait for all currently queued tasks to finish, then send a sentinel.
        self._queue.join()
        self._queue.put(None)
        self._thread.join()

    def _run(self) -> None:
        while True:
            path = self._queue.get()
            if path is None:
                self._queue.task_done()
                break
            try:
                _index_if_changed(path)
            finally:
                self._queue.task_done()


class WatchHandler(FileSystemEventHandler):

    def __init__(
        self,
        config: dict[str, Any],
        worker: IndexWorker,
        required_mount_roots: list[Path],
    ):
        self.allowed_ext = normalize_extensions(
            config.get("allowed_extensions", ALLOWED_EXTENSIONS)
        )
        self.ignore = config.get("ignore_patterns", [])
        self.worker = worker
        self.required_mount_roots = required_mount_roots

    def _broken_mount_for(self, file_path: Path) -> Path | None:
        """Return the required mount root that is empty (broken bind mount), or None."""
        for root in self.required_mount_roots:
            if file_path.is_relative_to(root) and _is_empty_dir(root):
                return root
        return None

    def enqueue(self, path: str) -> None:
        if is_indexable_path(path, self.allowed_ext, self.ignore):
            self.worker.submit(normalize_path(path))

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self.enqueue(event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        self.on_created(event)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if event.is_directory or not is_indexable_path(
            event.src_path, self.allowed_ext, self.ignore
        ):
            return
        broken_root = self._broken_mount_for(Path(event.src_path))
        if broken_root is not None:
            logger.warning(
                "Skipping delete for %s — root %s is empty, likely a broken bind mount. "
                "Fix the mount and run: docker compose restart watcher",
                event.src_path, broken_root,
            )
            return
        normalized_path = normalize_path(event.src_path)
        try:
            remove_indexed_document(normalized_path)
            bump_index_version()
        except Exception as e:
            logger.error("Failed to remove deleted file %s: %s", normalized_path, e)


def _iter_watch_paths(
    watch_paths: list[dict[str, Any]],
) -> Generator[tuple[dict[str, Any], Path], None, None]:
    for entry in watch_paths:
        raw_path = Path(entry["path"]).expanduser()
        if not raw_path.exists():
            logger.warning("Skipping missing path: %s", raw_path)
            continue
        yield entry, Path(normalize_path(raw_path))


def _is_empty_dir(path: Path) -> bool:
    """Return True if path is an empty directory, False if non-empty or unreadable."""
    try:
        return not any(path.iterdir())
    except OSError:
        return False


def _iter_schedulable_dirs(root: Path, exclude_dirs: list[str]) -> Generator[Path, None, None]:
    """Yield root and all non-excluded subdirectories for non-recursive scheduling."""
    yield root
    for child in sorted(root.iterdir()):
        if child.is_dir() and not matches_ignore_pattern(child, exclude_dirs):
            yield from _iter_schedulable_dirs(child, exclude_dirs)


def validate_required_mounts(required_mounts: list[dict[str, Any]]) -> list[Path]:
    """Validate bind mount roots at startup. Raises RuntimeError if any are missing or empty."""
    if not required_mounts:
        logger.warning("No required_mounts configured — skipping mount validation")
        return []
    roots = []
    for entry in required_mounts:
        root = Path(normalize_path(entry["path"]))
        if not root.exists():
            raise RuntimeError(
                f"Required mount {root} does not exist — restart will be attempted"
            )
        if entry.get("require_non_empty", False) and not any(root.iterdir()):
            raise RuntimeError(
                f"Required mount {root} is empty — restart will be attempted"
            )
        logger.info("Mount %s OK", root)
        roots.append(root)
    return roots


def initial_scan(watch_path_pairs: list[tuple[dict, Path]], handler: WatchHandler) -> None:
    logger.info("Starting initial scan")
    for entry, root in watch_path_pairs:
        exclude_dirs = entry.get("exclude_dirs", [])
        recursive = entry.get("recursive", True)
        if recursive and exclude_dirs:
            for directory in _iter_schedulable_dirs(root, exclude_dirs):
                for f in directory.glob("*"):
                    if f.is_file():
                        handler.enqueue(str(f))
        else:
            pattern = "**/*" if recursive else "*"
            for f in root.glob(pattern):
                if f.is_file():
                    handler.enqueue(str(f))


def main() -> None:
    try:
        config = load_config()
        required_mount_roots = validate_required_mounts(config.get("required_mounts", []))
    except RuntimeError as e:
        logger.error("%s", e)
        sys.exit(1)

    init_db()
    init_index_state()
    worker = IndexWorker()

    watch_path_pairs = list(_iter_watch_paths(config["watch_paths"]))
    accessible_roots = [root for _, root in watch_path_pairs]

    handler = WatchHandler(config, worker, required_mount_roots)
    cleanup_stale(accessible_roots)
    initial_scan(watch_path_pairs, handler)
    gc.collect()
    if sys.platform == "linux":
        with suppress(OSError):
            ctypes.cdll.LoadLibrary("libc.so.6").malloc_trim(0)

    observer = PollingObserver(timeout=WATCHER_POLL_INTERVAL_SECONDS)
    for entry, path in watch_path_pairs:
        exclude_dirs = entry.get("exclude_dirs", [])
        recursive = entry.get("recursive", True)
        if exclude_dirs and recursive:
            scheduled_dirs = list(_iter_schedulable_dirs(path, exclude_dirs))
            for watch_dir in scheduled_dirs:
                observer.schedule(handler, str(watch_dir), recursive=False)
            logger.info("Watching %s (%d directories scheduled)", path, len(scheduled_dirs))
        else:
            observer.schedule(handler, str(path), recursive=recursive)
            logger.info("Watching %s", path)

    observer.start()

    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        observer.stop()

    observer.join()
    worker.stop()


if __name__ == "__main__":
    main()
