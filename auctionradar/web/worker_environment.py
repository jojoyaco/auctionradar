"""Explicit workspace boundaries for detached search/evaluation subprocesses."""

from __future__ import annotations

import os
from pathlib import Path


def for_workspace(cfg, *, environ=None, role="search") -> dict[str, str]:
    """Snapshot effective paths, never inherit the household's output overrides.

    The parent request has already selected/authorised the workspace. No path in
    this mapping comes from the request body. This helper does not open a file,
    change os.environ, or mutate the application's configuration object.
    """
    env = dict(os.environ if environ is None else environ)
    env["AUCTIONRADAR_DATA_DIR"] = str(cfg.DATA_DIR)
    for attribute, variable in (
        ("CATALOG_PATH", "AUCTIONRADAR_CATALOG_PATH"),
        ("PROFILE_PATH", "AUCTIONRADAR_PROFILE_PATH"),
        ("FEEDBACK_PATH", "AUCTIONRADAR_FEEDBACK_PATH"),
        ("SEEDS_PATH", "AUCTIONRADAR_SEEDS_PATH"),
    ):
        env[variable] = str(getattr(cfg, attribute))
    if getattr(cfg, "WORKSPACE_MODE", "legacy") == "private":
        root = Path(cfg.DATA_DIR)
        for name in (
            "AUCTIONRADAR_PROFILE_OVERLAY",
            "AUCTIONRADAR_RUNNER_DRY_ITEMS",
            "AUCTIONRADAR_RUNNER_DRY_STATUS_COUNTS",
            "AUCTIONRADAR_RUNNER_DRY_SELECTION_COUNTS",
            "AUCTIONRADAR_RUNNER_DRY_ID",
            "CREDENTIALS_DIRECTORY",
        ):
            env.pop(name, None)
        env.update(
            AUCTIONRADAR_WORKSPACE_MODE="private",
            AUCTIONRADAR_REGISTRATION_ENABLED="false",
            AUCTIONRADAR_HISTORY_DIR=str(root / "runs"),
            AUCTIONRADAR_OUTPUT_DIR=str(root / "runs"),
            AUCTIONRADAR_BOE_EXPORT_DIR=str(root / "private-media" / "boe-authenticated"),
        )
        identity = getattr(cfg, "IDENTITY_DB_PATH", root / "identity" / "identity.sqlite3")
        env["AUCTIONRADAR_IDENTITY_DB_PATH"] = str(identity)
        env.setdefault("AUCTIONRADAR_JOB_LOCK_DIR", str(Path(identity).parent / "job-slots"))
        if role == "evaluation":
            env.pop("AUCTIONRADAR_CREDENTIALS_KEY_FILE", None)
            env.pop("AUCTIONRADAR_CREDENTIAL_KEY_FILE", None)
        # An inherited source list must not activate an unconfigured connector.
        env.pop("AUCTIONRADAR_RUNNER_SOURCES", None)
    return env


def for_public_cli(*, environ=None) -> dict[str, str]:
    """The public scraper needs no session, identity, portal or vault secrets."""
    original = os.environ if environ is None else environ
    allowed = {
        "PATH",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "TMPDIR",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONUNBUFFERED",
        "AUCTIONRADAR_WORKSPACE_MODE",
    }
    return {key: value for key, value in original.items() if key in allowed}
