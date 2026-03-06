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
import threading
import time
import yaml
from pathlib import Path
from queue import Queue
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from ingest.index_documents import delete_document, index_file
from settings import CONFIG_PATH
from indexer.fingerprint_store import (
    init_db,
    get_hash,
    upsert_hash,
    delete_hash,
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
        self._queue = Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def submit(self, path: str) -> None:
        self._queue.put(path)

    def _run(self) -> None:
        while True:
            path = self._queue.get()
            try:
                p = Path(path)
                if not p.exists():
                    continue

                file_hash = sha256_file(p)
                prev_hash = get_hash(path)
                if prev_hash == file_hash:
                    continue
                print(f"Indexing {path}")
                index_file(p)
                upsert_hash(path, file_hash)

            except Exception as e:
                print(f"Error indexing {path}: {e}")
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

    def enqueue(self, path: str) -> None:
        if not self.should_ignore(path) and self.valid_ext(path):
            self.worker.submit(path)

    def on_created(self, event) -> None:
        if not event.is_directory:
            self.enqueue(event.src_path)

    def on_modified(self, event) -> None:
        if not event.is_directory:
            self.enqueue(event.src_path)

    def on_deleted(self, event) -> None:
        if not event.is_directory and not self.should_ignore(event.src_path) and self.valid_ext(event.src_path):
            delete_document(event.src_path)
            delete_hash(event.src_path)


def _iter_watch_paths(watch_paths: list):
    for entry in watch_paths:
        path = Path(entry["path"]).expanduser()
        if not path.exists():
            print(f"Skipping missing path: {path}")
            continue
        yield entry, path


def initial_scan(watch_paths: list, handler: WatchHandler) -> None:
    print("Starting initial scan")
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

    initial_scan(config["watch_paths"], handler)

    observer = Observer()
    for entry, path in _iter_watch_paths(config["watch_paths"]):
        recursive = entry.get("recursive", True)
        print(f"Watching {path}")
        observer.schedule(handler, str(path), recursive=recursive)

    observer.start()

    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        observer.stop()

    observer.join()


if __name__ == "__main__":
    main()
