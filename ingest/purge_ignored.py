"""
Delete indexed documents that no longer pass the watcher config filters.

This removes both Qdrant vectors and fingerprint DB rows for paths that are
currently tracked but would now be ignored by the watcher.

Usage:
    python -m ingest.purge_ignored --config /app/config/watcher_config.container.yaml
    python -m ingest.purge_ignored --config /app/config/watcher_config.container.yaml --apply
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import yaml

from common.paths import is_indexable_path, normalize_path
from indexer.fingerprint_store import delete_hash, init_db, list_all_paths
from ingest.index_documents import delete_document
from settings import ALLOWED_EXTENSIONS, CONFIG_PATH

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def load_config(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f) or {}


def _iter_accessible_roots(config: dict) -> list[Path]:
    roots = []
    for entry in config.get("watch_paths", []):
        root = Path(entry["path"]).expanduser()
        if root.exists():
            roots.append(Path(normalize_path(root)))
        else:
            logger.warning("Skipping missing watch root: %s", root)
    return roots


def _is_under_roots(path: Path, roots: list[Path]) -> bool:
    return any(path.is_relative_to(root) for root in roots)


def find_ignored_paths(config: dict) -> list[str]:
    allowed_ext = set(config.get("allowed_extensions", ALLOWED_EXTENSIONS))
    ignore_patterns = config.get("ignore_patterns", [])
    accessible_roots = _iter_accessible_roots(config)
    ignored = []

    for filepath in list_all_paths():
        path = Path(filepath)
        if accessible_roots and not _is_under_roots(path, accessible_roots):
            continue
        if not is_indexable_path(path, allowed_ext, ignore_patterns):
            ignored.append(filepath)

    return sorted(ignored)


def purge_ignored(config_path: Path, *, apply: bool) -> int:
    init_db()
    config = load_config(config_path)
    ignored = find_ignored_paths(config)

    action = "Deleting" if apply else "Would delete"
    for filepath in ignored:
        logger.info("%s indexed ignored path: %s", action, filepath)
        if apply:
            delete_document(filepath)
            delete_hash(filepath)

    logger.info(
        "%s complete — %d indexed ignored path(s) %s",
        "Purge" if apply else "Dry run",
        len(ignored),
        "removed" if apply else "matched",
    )
    return len(ignored)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
        help="Watcher config path to use for allowed extensions and ignore patterns.",
    )
    parser.add_argument("--apply", action="store_true", help="Delete matched entries.")
    args = parser.parse_args()
    purge_ignored(args.config, apply=args.apply)


if __name__ == "__main__":
    main()
