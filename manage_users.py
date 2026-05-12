"""
User management CLI for the web UI.

Usage:
    python manage_users.py add <username>
    python manage_users.py remove <username>
    python manage_users.py list

Via Docker:
    docker exec -it rag-api python manage_users.py add alice
"""

import getpass
import sys

import bcrypt

from web.user_store import delete_user, init_db, list_users, upsert_user


def cmd_add(username: str) -> None:
    password = getpass.getpass(f"Password for {username!r}: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords do not match.", file=sys.stderr)
        sys.exit(1)
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    upsert_user(username, password_hash)
    print(f"User {username!r} saved.")


def cmd_remove(username: str) -> None:
    delete_user(username)
    print(f"User {username!r} removed (no-op if they did not exist).")


def cmd_list() -> None:
    users = list_users()
    if not users:
        print("No users.")
    else:
        for u in users:
            print(u)


def main() -> None:
    init_db()
    args = sys.argv[1:]
    if not args:
        print("Usage: manage_users.py <add|remove|list> [username]", file=sys.stderr)
        sys.exit(1)
    cmd, *rest = args
    if cmd == "add":
        if not rest:
            print("Usage: manage_users.py add <username>", file=sys.stderr)
            sys.exit(1)
        cmd_add(rest[0])
    elif cmd == "remove":
        if not rest:
            print("Usage: manage_users.py remove <username>", file=sys.stderr)
            sys.exit(1)
        cmd_remove(rest[0])
    elif cmd == "list":
        cmd_list()
    else:
        print(f"Unknown command: {cmd!r}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
