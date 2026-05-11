"""
Filesystem watcher for continuous background document indexing.

On startup, performs an initial scan of all configured watch paths, then
keeps watching for file system events. File changes are deduplicated by
SHA-256 hash before being passed to the ingest pipeline.

Watch paths, allowed extensions, and ignore patterns are configured in
config/watcher_config.yaml. Run from the project root:
  python indexer/watcher.py
"""

import hashlib
import logging
import threading
import time
from pathlib import Path
from queue import Queue

import yaml
from watchdog.events import FileSystemEventHandler
from watchdog.observers.polling import PollingObserver

from indexer.fingerprint_store import delete_hash, get_hash, init_db, upsert_hash
from ingest.cleanup_stale import cleanup_stale
from ingest.index_documents import delete_document, index_file
from settings import CONFIG_PATH

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


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
                p = Path(path)
                if not p.exists():
                    continue

                file_hash = sha256_file(p)
                prev_hash = get_hash(path)
                if prev_hash == file_hash:
                    continue
                logging.info("Indexing %s", path)
                index_file(p)
                upsert_hash(path, file_hash)

            except Exception as e:
                logging.error("Error indexing %s: %s", path, e)
            finally:
                self._queue.task_done()


class WatchHandler(FileSystemEventHandler):

    def __init__(self, config, worker: IndexWorker):
        self.allowed_ext = set(config["allowed_extensions"])
        self.ignore = config["ignore_patterns"]
        self.worker = worker

    def should_ignore(self, path: str) -> bool:
        p = Path(path)
        for pattern in self.ignore:
            if pattern in p.parts:
                return True
        return False

    def valid_ext(self, path: str) -> bool:
        return Path(path).suffix.lower() in self.allowed_ext

    def _should_enqueue_file(self, path: str) -> bool:
        """Return True if the file at path should be processed by the indexer."""
        return not self.should_ignore(path) and self.valid_ext(path)

    def enqueue(self, path: str) -> None:
        if self._should_enqueue_file(path):
            self.worker.submit(path)

    def on_created(self, event) -> None:
        if not event.is_directory:
            self.enqueue(event.src_path)

    def on_modified(self, event) -> None:
        if not event.is_directory:
            self.enqueue(event.src_path)

    def on_deleted(self, event) -> None:
        if not event.is_directory and self._should_enqueue_file(event.src_path):
            delete_document(event.src_path)
            delete_hash(event.src_path)


def _iter_watch_paths(watch_paths: list):
    for entry in watch_paths:
        path = Path(entry["path"]).expanduser()
        if not path.exists():
            logging.warning("Skipping missing path: %s", path)
            continue
        yield entry, path


def initial_scan(watch_paths: list, handler: WatchHandler) -> None:
    logging.info("Starting initial scan")
    for entry, root in _iter_watch_paths(watch_paths):
        pattern = "**/*" if entry.get("recursive", True) else "*"
        for f in root.glob(pattern):
            if f.is_file():
                handler.enqueue(str(f))


def main() -> None:
    init_db()
    config = load_config()
    worker = IndexWorker()
    handler = WatchHandler(config, worker)

    accessible_roots = [path for _, path in _iter_watch_paths(config["watch_paths"])]
    cleanup_stale(accessible_roots)
    initial_scan(config["watch_paths"], handler)

    observer = PollingObserver()
    for entry, path in _iter_watch_paths(config["watch_paths"]):
        recursive = entry.get("recursive", True)
        logging.info("Watching %s", path)
        observer.schedule(handler, str(path), recursive=recursive)

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
