"""Stand-alone CLI utilities (``python -m auctionradar.web.manage``).

Bootstrap administrators and ingest historical runs from the local console,
without requiring a browser or an existing administrator session.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from auctionradar.web import (
    ingest,  # type: ignore
    models,  # type: ignore
)
from auctionradar.web.config import build_config  # noqa: F401
from auctionradar.web.security import hash_password


def _read_password_stdin() -> str:
    """Read a password without echoing it; automation uses stdin, never arguments."""
    if sys.stdin.isatty():
        from getpass import getpass

        return getpass("Password: ")
    return sys.stdin.readline().rstrip("\r\n")


def _create_user(args: argparse.Namespace) -> int:
    config = build_config()
    from auctionradar.web import workspaces

    workspaces.initialize(config)
    username = (args.username or "").strip()
    if not username:
        print("ERROR: username required", file=sys.stderr)
        return 2
    password = _read_password_stdin()
    if not password or not 12 <= len(password) <= 1024:
        print("ERROR: password must be between 12 and 1024 characters", file=sys.stderr)
        return 2
    if models.get_user_by_username(username) is not None:
        print(f"ERROR: user {username!r} already exists", file=sys.stderr)
        return 3
    digest, salt = hash_password(password)
    is_admin = bool(args.admin)
    user_id = models.create_user(username, digest, salt, is_admin=is_admin)
    print(f"User created: id={user_id} username={username!r} admin={is_admin}")
    return 0


def _import_run(args: argparse.Namespace) -> int:
    config = build_config()
    from auctionradar.web import workspaces

    workspaces.initialize(config)
    history_dir = Path(args.path)
    if not history_dir.exists():
        print(f"ERROR: path not found: {history_dir}", file=sys.stderr)
        return 2
    summary = ingest.ingest_run(history_dir, run_id=args.run_id)
    print(json.dumps(summary, ensure_ascii=False, default=str, indent=2))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="AuctionRadar administration utilities")
    sub = parser.add_subparsers(dest="command")

    create = sub.add_parser("create-user", help="create a user (admin or invited)")
    create.add_argument("--username", required=True)
    create.add_argument(
        "--password-stdin", action="store_true", help="read password from stdin (no echo)"
    )
    create.add_argument("--admin", action="store_true", help="grant administrative privileges")
    create.set_defaults(func=_create_user)

    importer = sub.add_parser(
        "import-run", help="ingest a CLI run artefact into the webapp database"
    )
    importer.add_argument("path", help="directory containing <run_id>.json (or latest.json)")
    importer.add_argument("--run-id", default=None)
    importer.set_defaults(func=_import_run)

    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 1
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
