#!/usr/bin/env bash
# Untrack files that are now covered by .gitignore.
# Removes them from the git index without deleting them locally.

set -euo pipefail

FILES=$(git ls-files -i --cached --exclude-standard)

if [ -z "$FILES" ]; then
  echo "No tracked files match .gitignore — nothing to do."
  exit 0
fi

echo "Files to be untracked:"
echo "$FILES"
echo

git rm --cached $FILES

echo
echo "Done. Commit the result to finish removing these files from history."
