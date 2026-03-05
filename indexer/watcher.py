"""
Filesystem watcher for continuous background document indexing.

On startup, performs an initial scan of all configured watch paths, then
keeps watching for file system events. File changes are deduplicated by
SHA-256 hash before being passed to the ingest pipeline.

Watch paths, allowed extensions, and ignore patterns are configured in
config/watcher_config.yaml. Run from the project root:
  python indexer/watcher.py
"""

import time
import hashlib
import yaml
import threading
from queue import Queue
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from ingest.index_documents import index_file, delete_document


CONFIG_PATH = Path(__file__).parent.parent / "config" / "watcher_config.yaml"


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
        self._hashes: dict = {}
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
                if self._hashes.get(path) == file_hash:
                    continue

                print(f"Indexing {path}")
                index_file(p)
                self._hashes[path] = file_hash

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
        return any(pattern in path for pattern in self.ignore)

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
        if not event.is_directory:
            delete_document(event.src_path)


def initial_scan(watch_paths: list, handler: WatchHandler) -> None:
    print("Starting initial scan")
    for entry in watch_paths:
        root = Path(entry["path"]).expanduser()
        if not root.exists():
            print(f"Skipping missing path: {root}")
            continue
        pattern = "**/*" if entry.get("recursive", True) else "*"
        for f in root.glob(pattern):
            if f.is_file():
                handler.enqueue(str(f))


def main() -> None:
    config = load_config()
    worker = IndexWorker()
    handler = WatchHandler(config, worker)

    initial_scan(config["watch_paths"], handler)

    observer = Observer()
    for entry in config["watch_paths"]:
        path = Path(entry["path"]).expanduser()
        if not path.exists():
            print(f"Skipping missing path: {path}")
            continue
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
