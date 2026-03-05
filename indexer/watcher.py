import os
import time
import hashlib
import yaml
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from ingest.index_documents import index_file, delete_document


CONFIG_PATH = Path("config/watcher_config.yaml")


def load_config():
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)


def sha256_file(path):
    h = hashlib.sha256()

    with open(path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)

    return h.hexdigest()


class DocumentWatcher(FileSystemEventHandler):

    def __init__(self, config):
        self.allowed_ext = set(config["allowed_extensions"])
        self.ignore_patterns = config["ignore_patterns"]
        self.file_hashes = {}

    def should_ignore(self, path):

        for pattern in self.ignore_patterns:
            if pattern in path:
                return True

        return False

    def valid_extension(self, path):

        return Path(path).suffix.lower() in self.allowed_ext

    def process_file(self, path):

        if not os.path.exists(path):
            return

        if self.should_ignore(path):
            return

        if not self.valid_extension(path):
            return

        try:

            file_hash = sha256_file(path)

            if path in self.file_hashes and self.file_hashes[path] == file_hash:
                return

            print(f"Indexing updated file: {path}")

            index_file(path)

            self.file_hashes[path] = file_hash

        except Exception as e:
            print(f"Error indexing {path}: {e}")

    def on_created(self, event):

        if event.is_directory:
            return

        self.process_file(event.src_path)

    def on_modified(self, event):

        if event.is_directory:
            return

        self.process_file(event.src_path)

    def on_deleted(self, event):

        if event.is_directory:
            return

        print(f"Removing document: {event.src_path}")

        delete_document(event.src_path)


def initial_scan(paths, handler):

    print("Running initial document scan")

    for root in paths:

        root = os.path.expanduser(root)

        for dirpath, _, filenames in os.walk(root):

            for f in filenames:

                full_path = os.path.join(dirpath, f)

                handler.process_file(full_path)


def main():

    config = load_config()

    paths = [p["path"] for p in config["watch_paths"]]

    handler = DocumentWatcher(config)

    initial_scan(paths, handler)

    observer = Observer()

    for p in config["watch_paths"]:

        path = os.path.expanduser(p["path"])

        recursive = p.get("recursive", True)

        print(f"Watching: {path}")

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
