import os
import time
import hashlib
import yaml
import threading
from queue import Queue
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from ingest.index_documents import index_file, delete_document


CONFIG_PATH = Path("config/watcher_config.yaml")

file_queue = Queue()
indexed_hashes = {}


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def sha256_file(path):

    h = hashlib.sha256()

    with open(path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)

    return h.hexdigest()


class WatchHandler(FileSystemEventHandler):

    def __init__(self, config):

        self.allowed_ext = set(config["allowed_extensions"])
        self.ignore = config["ignore_patterns"]

    def should_ignore(self, path):

        for pattern in self.ignore:
            if pattern in path:
                return True

        return False

    def valid_ext(self, path):

        return Path(path).suffix.lower() in self.allowed_ext

    def enqueue(self, path):

        if self.should_ignore(path):
            return

        if not self.valid_ext(path):
            return

        file_queue.put(path)

    def on_created(self, event):

        if not event.is_directory:
            self.enqueue(event.src_path)

    def on_modified(self, event):

        if not event.is_directory:
            self.enqueue(event.src_path)

    def on_deleted(self, event):

        if event.is_directory:
            return

        print(f"Deleting vectors for {event.src_path}")
        delete_document(event.src_path)


def worker():

    while True:

        path = file_queue.get()

        try:

            if not os.path.exists(path):
                file_queue.task_done()
                continue

            file_hash = sha256_file(path)

            if path in indexed_hashes and indexed_hashes[path] == file_hash:
                file_queue.task_done()
                continue

            print(f"Indexing {path}")

            index_file(Path(path))

            indexed_hashes[path] = file_hash

        except Exception as e:

            print(f"Error indexing {path}: {e}")

        file_queue.task_done()


def initial_scan(paths, handler):

    print("Starting initial scan")

    for root in paths:

        root = os.path.expanduser(root)

        if not os.path.exists(root):
            print(f"Skipping missing path: {root}")
            continue

        for dirpath, _, filenames in os.walk(root):

            for f in filenames:

                handler.enqueue(os.path.join(dirpath, f))


def main():

    config = load_config()

    paths = [p["path"] for p in config["watch_paths"]]

    handler = WatchHandler(config)

    worker_thread = threading.Thread(target=worker, daemon=True)
    worker_thread.start()

    initial_scan(paths, handler)

    observer = Observer()

    for p in config["watch_paths"]:

        path = os.path.expanduser(p["path"])

        if not os.path.exists(path):
            print(f"Skipping missing path: {path}")
            continue

        recursive = p.get("recursive", True)

        print(f"Watching {path}")

        observer.schedule(handler, path, recursive=recursive)

    observer.start()

    try:

        while True:
            time.sleep(5)

    except KeyboardInterrupt:

        observer.stop()

    observer.join()


if __name__ == "__main__":
    main()