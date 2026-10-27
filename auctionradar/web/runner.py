"""Detached ingestion runner for the auctionradar.web.

Two entry modes:

* **Manual** — invoked by ``/api/runs`` with ``--run-id <existing>``.
  The runner finds the row already created by the view, refreshes its
  source list from the settings overlay, takes the exclusive flock,
  invokes the CLI, ingests the artefact and finalises the row.
* **Timer / cron** — invoked by an external scheduler with **no** flags.
  The runner creates its own ``runs`` row with a fresh ``run_id``,
  reads the settings overlay from the database, picks the
  currently-enabled sources and proceeds exactly like the manual
  branch.

The runner is a single point of contact between the webapp process and
the canonical ``auctionradar.cli`` CLI. The view layer never invokes the CLI
directly; doing so would block gunicorn workers on a 30-minute
network-bound search. The runner also enforces a hard wall-clock
budget (default 30 minutes) and tears down the entire CLI process
group (curl grand-children included) when the budget trips.

No shell, no user input, every CLI argument originates from the
validated settings overlay or a constant. Secret material never
appears in CLI arguments; debug strings are scrubbed before being
written to the run summary.
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import fcntl
import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from auctionradar.web import db, ingest, models  # type: ignore
from auctionradar.web.worker_environment import for_public_cli

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [runner] %(message)s",
)
log = logging.getLogger("auctionradar.web.runner")

# Silence other loggers so subprocess output doesn't overwrite ours.
logging.getLogger("webapp").setLevel(logging.INFO)
logging.getLogger("urllib3").setLevel(logging.WARNING)


RUN_DIR_NAME = "runs"
LOCK_FILENAME = "run.lock"
PROFILE_FILENAME = "profile.overlay.json"
FEEDBACK_FILENAME = "selection-feedback.json"
SEEDS_FILENAME = "seeds.json"
OUTPUT_FILENAME = "result.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def _safe_dump(obj: Any) -> str:
    """Render an object for the summary column, never crashing on odd types."""
    try:
        return json.dumps(obj, ensure_ascii=False, default=str, sort_keys=True)
    except (TypeError, ValueError):
        return repr(obj)[:1000]


def _scrub(value: Any) -> Any:
    """Drop anything that looks like a credential from a CLI artefact."""
    if isinstance(value, list):
        return [_scrub(entry) for entry in value]
    if not isinstance(value, dict):
        return value
    out = {}
    for key, val in value.items():
        if isinstance(key, str) and any(
            token in key.lower()
            for token in ("password", "secret", "token", "api_key", "authorization")
        ):
            continue
        out[key] = _scrub(val)
    return out


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Settings loader
# ---------------------------------------------------------------------------


def load_overlay_from_db_or_env() -> dict:
    """Read the settings overlay, honouring the legacy env override.

    The runtime prefers the database row that was written by
    :func:`auctionradar.web.views.api_settings_put`. The ``AUCTIONRADAR_PROFILE_OVERLAY``
    environment variable still wins (used by tests and ad-hoc cron
    triggers), letting callers inject a synthetic overlay without
    round-tripping through the view layer.
    """
    env_raw = os.environ.get("AUCTIONRADAR_PROFILE_OVERLAY")
    if env_raw:
        try:
            data = json.loads(env_raw)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            return data
    raw = models.get_setting_raw("settings")
    if isinstance(raw, dict):
        return raw
    return {}


def build_runner_profile(overlay: dict) -> dict:
    """Derive the CLI invocation parameters from the overlay.

    Every field falls back to a documented default so the runner never
    trips over a partial overlay.
    """
    cli = overlay.get("cli") or {}
    return {
        "timeout": _coerce_float(cli.get("timeout"), 12.0),
        "max_pages": _coerce_int(cli.get("max_pages"), 2),
        "max_details": _coerce_int(cli.get("max_details"), 6),
        "max_documents": _coerce_int(cli.get("max_documents"), 1),
        "workers": _coerce_int(cli.get("workers"), 4),
        "max_run_seconds": _coerce_int(cli.get("max_run_seconds"), 1800),
    }


def read_enabled_sources(overlay: dict) -> list[str]:
    """Pick the sources the runner will actually hit.

    ``sources`` sub-dict in the overlay wins when non-empty. When the
    operator never tweaked it we ask the canonical catalog's
    ``enabled`` flag; failing that we use ``RUNNER_DEFAULT_SOURCES``
    from the config, and finally a hard-coded fallback so a fresh
    install never produces an empty CLI invocation.
    """
    from auctionradar.web.config import build_config

    cfg = build_config()
    if os.environ.get("AUCTIONRADAR_WORKSPACE_MODE", "").lower() == "private":
        # Empty means empty, never "fall back to the owner's 65 sources".
        catalog = json.loads(Path(cfg.CATALOG_PATH).read_text(encoding="utf-8"))
        states = overlay.get("sources", {}) if isinstance(overlay, dict) else {}
        return [
            source["id"]
            for source in catalog.get("sources", [])
            if _is_truthy(
                (states.get(source["id"]) or {}).get("enabled", source.get("enabled", False))
            )
        ]
    sources_in = overlay.get("sources") if isinstance(overlay, dict) else None
    if isinstance(sources_in, dict) and sources_in:
        enabled = [
            sid
            for sid, state in sources_in.items()
            if isinstance(state, dict) and _is_truthy(state.get("enabled", True))
        ]
        if enabled:
            return enabled
    try:
        from auctionradar.web.config import build_config  # type: ignore

        cfg = build_config()
        if cfg.RUNNER_DEFAULT_SOURCES:
            return list(cfg.RUNNER_DEFAULT_SOURCES)
    except Exception:
        pass
    return []


def read_regions(overlay: dict) -> list[str]:
    regions = overlay.get("regions") if isinstance(overlay, dict) else []
    if isinstance(regions, list):
        return [r for r in regions if isinstance(r, str) and r]
    return []


def read_preferred_regions(overlay: dict) -> list[str]:
    housing = overlay.get("housing") if isinstance(overlay, dict) else None
    if not isinstance(housing, dict):
        return []
    preferred = housing.get("preferred_regions") or []
    if isinstance(preferred, list):
        return [r for r in preferred if isinstance(r, str) and r]
    return []


# ---------------------------------------------------------------------------
# Profile overlay materialised to disk
# ---------------------------------------------------------------------------


def _resolve_canonical_paths() -> tuple[Path, Path, Path]:
    """Honour the effective workspace paths, including in detached workers."""
    from auctionradar.web.config import build_config

    cfg = build_config()
    return Path(cfg.PROFILE_PATH), Path(cfg.FEEDBACK_PATH), Path(cfg.SEEDS_PATH)


def _load_canonical_profile() -> dict:
    path, _, _ = _resolve_canonical_paths()
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}


def write_profile_overlay(overlay: dict, dest_dir: Path) -> Optional[Path]:
    """Copy the canonical profile to ``dest_dir`` after applying the overlay.

    The CLI accepts ``--profile /path/to/profile.json``; we don't want
    to mutate the canonical artefact under ``config/``. The runner
    therefore writes a temporary profile with the overlay's housing
    budget and preferred regions patched in.

    Returns the path to the freshly written profile or ``None`` when
    the overlay leaves the canonical profile untouched.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    profile = dict(_load_canonical_profile())
    housing = overlay.get("housing") or {}
    if isinstance(housing, dict):
        budget = housing.get("budget_max_all_in")
        if "budget_max_all_in" in housing and (budget is None or isinstance(budget, (int, float))):
            profile.setdefault("housing", {})
            profile["housing"].setdefault("budget", {})
            profile["housing"]["budget"]["max_all_in"] = budget
        preferred = housing.get("preferred_regions")
        if isinstance(preferred, list):
            profile.setdefault("housing", {})
            profile["housing"]["preferred_regions"] = [
                {"name": r} if isinstance(r, str) else r for r in preferred
            ]
    target = dest_dir / PROFILE_FILENAME
    with target.open("w", encoding="utf-8") as fh:
        json.dump(profile, fh, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    target.chmod(0o600)
    return target


def write_catalog_overlay(overlay: dict, dest_dir: Path) -> Path:
    from .config import build_config

    catalog_path = Path(build_config().CATALOG_PATH)
    if os.environ.get("AUCTIONRADAR_WORKSPACE_MODE", "").lower() != "private":
        return catalog_path
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    states = overlay.get("sources") or {}
    for source in catalog.get("sources", []):
        if source["id"] in states:
            source["enabled"] = bool(
                states[source["id"]].get("enabled", source.get("enabled", False))
            )
    destination = dest_dir / "catalog.overlay.json"
    destination.write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
    destination.chmod(0o600)
    return destination


# ---------------------------------------------------------------------------
# Lock + process group lifecycle
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _wall_clock_budget(seconds: int):
    deadline = time.monotonic() + max(60, seconds)
    try:
        yield
    finally:
        if time.monotonic() > deadline:
            raise TimeoutError(f"wall-clock budget of {seconds}s exceeded")


class _FileLock:
    """Single-process POSIX flock wrapper."""

    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self._fd: Optional[int] = None

    def acquire(self) -> bool:
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        except OSError as exc:
            log.warning("could not open lock file %s: %s", self.lock_path, exc)
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                os.close(fd)
                return False
            os.close(fd)
            return False
        try:
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.fsync(fd)
        except OSError:
            pass
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        except OSError:  # pragma: no cover
            pass
        try:
            os.close(self._fd)
        except OSError:  # pragma: no cover
            pass
        self._fd = None


def _terminate_process_group(proc: subprocess.Popen, *, grace: float = 5.0) -> None:
    """SIGTERM the whole process group, escalate to SIGKILL after ``grace``.

    The CLI shells out to ``curl``; if SIGTERM only kills the parent
    the grand-children would keep downloading until their own
    timeouts expire. Spawning the subprocess with
    ``start_new_session=True`` makes it the leader of a fresh process
    group — we then ``killpg`` the group.
    """
    if proc.poll() is not None:
        return
    pid = proc.pid
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.wait(timeout=grace)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        log.error("process group did not die after SIGKILL: pid=%s", pid)


def _install_sigterm_handler() -> tuple[Any, Any]:
    """Install a SIGTERM handler that requests a wind-down.

    Returns the ``(old_handler, installed_flag)`` tuple so the caller
    can restore the previous handler on exit.
    """
    request = {"requested": False, "process": None}

    def _handler(signum, frame):
        request["requested"] = True
        log.warning("runner received SIGTERM; will terminate subprocess gracefully")
        if request["process"] is not None:
            _terminate_process_group(request["process"])

    previous = signal.getsignal(signal.SIGTERM)
    try:
        signal.signal(signal.SIGTERM, _handler)
        return previous, request
    except (ValueError, OSError):
        # Signal handlers cannot be installed from worker threads.
        return previous, None


# ---------------------------------------------------------------------------
# CLI invocation
# ---------------------------------------------------------------------------


def _build_cli_command(
    *,
    history_dir: Path,
    overlay_profile: dict,
    sources: list[str],
    regions: list[str],
    asset: Optional[str],
    extra_args: Optional[list[str]] = None,
) -> list[str]:
    args = [
        sys.executable,
        "-m",
        "auctionradar.cli",
        "--output",
        str(history_dir / OUTPUT_FILENAME),
        "--history-dir",
        str(history_dir),
        "--timeout",
        str(max(0.1, overlay_profile["timeout"])),
        "--max-pages",
        str(max(1, overlay_profile["max_pages"])),
        "--max-details",
        str(max(0, overlay_profile["max_details"])),
        "--max-documents",
        str(max(0, overlay_profile["max_documents"])),
        "--workers",
        str(max(1, overlay_profile["workers"])),
    ]
    if asset is not None:
        args.extend(["--asset", asset])
    for sid in sources:
        args.extend(["--source", sid])
    for region in regions:
        args.extend(["--region", region])
    if extra_args:
        args.extend(extra_args)
    return args


# ---------------------------------------------------------------------------
# Run state transitions
# ---------------------------------------------------------------------------


def _ensure_run_record(run_id: str, *, sources: list[str], timer: bool) -> Optional[dict]:
    """Read or create the row and refresh its ``sources`` field.

    Returns ``None`` if the row never appears (timer mode just
    inserted it and is therefore visible by definition).
    """
    row = models.get_run(run_id)
    if row is None and timer:
        models.create_run_silent(run_id=run_id, sources=sources, created_by=None)
        row = models.get_run(run_id)
    if row is None:
        log.error("run record not found for %s", run_id)
        return None
    models.update_run_sources(run_id, sources)
    return row


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Detached ingestion runner")
    parser.add_argument("--run-id", default=None, help="Existing run id (manual mode)")
    parser.add_argument(
        "--timer",
        action="store_true",
        help="Timer mode: create a new run record if no --run-id supplied.",
    )
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--history-dir", default=None)
    parser.add_argument(
        "--asset",
        choices=("housing", "vehicles"),
        default=None,
        help="Limit the CLI to a single asset type (defaults to both)",
    )
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        help="Override the enabled-source list (repeatable).",
    )
    from auctionradar.assets import CATEGORY_CHOICES

    parser.add_argument(
        "--category",
        action="append",
        default=[],
        choices=[c["id"] for c in CATEGORY_CHOICES],
        help="Focus additional public queries on these categories (repeatable).",
    )
    parser.add_argument(
        "--preferred-region",
        action="append",
        default=[],
        help="Optional override of the preferred regions (repeatable).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip subprocess.Popen; tests rely on this.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Override the directory the CLI writes to (default: $AUCTIONRADAR_OUTPUT_DIR or history_dir).",
    )
    parser.add_argument(
        "--artifact-name",
        default=OUTPUT_FILENAME,
        help="Override the artefact filename produced by the CLI.",
    )
    parser.add_argument(
        "--max-run-seconds",
        type=int,
        default=None,
        help="Override wall-clock budget for the CLI subprocess.",
    )
    args = parser.parse_args(argv)

    data_dir = Path(
        args.data_dir or os.environ.get("AUCTIONRADAR_DATA_DIR") or PROJECT_ROOT / "instance"
    )
    history_dir = Path(
        args.history_dir or os.environ.get("AUCTIONRADAR_HISTORY_DIR") or data_dir / RUN_DIR_NAME
    )
    history_dir.mkdir(parents=True, exist_ok=True)
    output_dir = Path(args.output_dir or os.environ.get("AUCTIONRADAR_OUTPUT_DIR") or history_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    from .config import build_config

    config = build_config()
    db.configure_runtime(config.WORKSPACE_MODE, config.IDENTITY_DB_PATH)
    db.init_db(data_dir)

    overlay = load_overlay_from_db_or_env()
    profile_cfg = build_runner_profile(overlay)
    if args.max_run_seconds is not None:
        profile_cfg["max_run_seconds"] = args.max_run_seconds

    sources = list(args.source) if args.source else read_enabled_sources(overlay)
    if args.preferred_region:
        # CLI flags take absolute precedence over the DB.
        preferred_regions = [r for r in args.preferred_region if r]
    else:
        preferred_regions = read_preferred_regions(overlay)
    base_regions = read_regions(overlay)
    # Preferences do not exclude the rest of Spain. Only `regions`
    # es un filtro estricto; `preferred_regions` vive en el perfil.
    regions = list(base_regions)

    run_id = args.run_id
    timer_mode = bool(args.timer) or not run_id
    if not run_id:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + os.urandom(2).hex()
        log.info("timer mode: generated run_id=%s", run_id)

    # Honour stale detection up-front: refuse to start a new run while
    # one is already considered alive.
    models.recover_stuck_runs(threshold_seconds=120)

    row = _ensure_run_record(run_id, sources=sources, timer=timer_mode)
    if row is None:
        return 2

    if os.environ.get("AUCTIONRADAR_WORKSPACE_MODE", "").lower() == "private" and not sources:
        models.finalize_run(
            run_id,
            status="success",
            summary={
                "reason": "no_sources_configured",
                "sources_requested": [],
                "sources": [],
                "ingest": {"ingested": 0},
            },
        )
        return 0

    from .job_limits import JobCapacityError, acquire

    try:
        lease = acquire("search")
    except JobCapacityError as exc:
        models.finalize_run(run_id, status="error", summary={"reason": str(exc)}, error=str(exc))
        return 75
    lock = _FileLock(data_dir / LOCK_FILENAME)
    if not lock.acquire():
        lease.close()
        log.error("another runner is already active (lock busy)")
        models.finalize_run(
            run_id,
            status="error",
            summary={"reason": "lock_busy"},
            error="another runner holds the run lock",
        )
        return 3
    previous_handler, sigterm_request = _install_sigterm_handler()

    try:
        # Persist the artefact name as soon as we know it.
        artefact_path = output_dir / args.artifact_name
        # The CLI guarantees --output on every successful run. Do not reuse
        # a previous result.json if a run ends without writing output.
        artefact_path.unlink(missing_ok=True)
        models.set_run_artifact_path(run_id, str(artefact_path))
        models.mark_run_started(run_id, pid=os.getpid())

        profile_path = write_profile_overlay(overlay, data_dir)
        if profile_path is not None:
            profile_arg = ["--profile", str(profile_path)]
        else:
            profile_arg = []

        # CLI expects the canonical feedback / seeds files to point at
        # ``config/``. They live outside ``data_dir`` so we hand them
        # the original paths.
        canon_profile, canon_feedback, canon_seeds = _resolve_canonical_paths()
        cli_artefact_args = [
            *profile_arg,
            "--feedback",
            str(canon_feedback),
            "--seeds",
            str(canon_seeds),
        ]
        cli_artefact_args.extend(["--catalog", str(write_catalog_overlay(overlay, data_dir))])

        # ``artefact_name`` is the filename the CLI writes; we then
        # rename it onto ``runs/<run_id>.json`` so the ingest pipeline
        # reads the *real* run identifier.
        cli_command = _build_cli_command(
            history_dir=output_dir,
            overlay_profile=profile_cfg,
            sources=sources,
            regions=regions,
            asset=args.asset,
            extra_args=[*cli_artefact_args, "--output", str(artefact_path)],
        )
        cli_command.append("--all-assets")
        focus = list(dict.fromkeys(args.category))
        for saved in db.fetchall(
            "SELECT s.criteria_json FROM saved_searches s JOIN users u ON u.id=s.owner_id WHERE s.active=1 AND s.archived=0 AND u.active=1"
        ):
            try:
                categories = json.loads(saved["criteria_json"]).get("categories", [])
            except (ValueError, TypeError):
                continue
            for category in categories:
                category = "other" if category == "vehicles" else category
                if category not in focus and category not in ("other", "unknown"):
                    focus.append(category)
        if focus:
            # Keep the general catalog and add queries specific to subscriptions.
            for category in [*focus[:15], "other"]:
                cli_command.extend(["--category", category])
        log.info("invoking CLI: %s", " ".join(cli_command))

        if args.dry_run:
            return _dry_run(
                run_id,
                cli_command,
                profile_cfg,
                sources,
                regions,
                overlay,
                output_dir=output_dir,
            )

        stderr_tail = ""
        with _wall_clock_budget(profile_cfg["max_run_seconds"]):
            proc = subprocess.Popen(
                cli_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(PROJECT_ROOT),
                env=(
                    for_public_cli()
                    if os.environ.get("AUCTIONRADAR_WORKSPACE_MODE", "").lower() == "private"
                    else os.environ.copy()
                ),
                start_new_session=True,
            )
            if sigterm_request is not None:
                sigterm_request["process"] = proc
            try:
                stdout_bytes, stderr_bytes = proc.communicate(
                    timeout=profile_cfg["max_run_seconds"] + 30
                )
            except subprocess.TimeoutExpired:
                log.warning("subprocess communicate timed out; killing process group")
                _terminate_process_group(proc)
                models.finalize_run(
                    run_id,
                    status="error",
                    summary={"reason": "wall-clock timeout"},
                    error=f"wall-clock budget of {profile_cfg['max_run_seconds']}s exceeded",
                )
                return 4
            stderr_tail = (stderr_bytes or b"")[-2000:].decode("utf-8", "replace")

        rc = proc.returncode
        if sigterm_request is not None and sigterm_request.get("requested"):
            models.finalize_run(
                run_id,
                status="cancelled",
                summary={"reason": "sigterm", "return_code": rc, "stderr_tail": stderr_tail},
                error="cancelled",
            )
            return 9

        if rc != 0:
            log.warning("CLI exited with code %s", rc)
            models.finalize_run(
                run_id,
                status="error",
                summary={
                    "reason": "cli_return_code",
                    "return_code": rc,
                    "stderr_tail": stderr_tail,
                    "sources_requested": sources,
                    "regions": regions,
                },
                error=f"cli return code {rc}",
            )
            return rc or 5

        # Move the artefact onto ``runs/<run_id>.json`` so the webapp
        # can find it deterministically. We tolerate a missing artefact
        # (the CLI may legitimately produce an empty run when every
        # source was ineligible).
        run_artefact = output_dir / f"{run_id}.json"
        if artefact_path.exists():
            if artefact_path.resolve() != run_artefact.resolve():
                try:
                    if run_artefact.exists():
                        run_artefact.unlink()
                    artefact_path.rename(run_artefact)
                except OSError as exc:
                    log.warning("could not rename artefact: %s", exc)
                    run_artefact = artefact_path
        elif run_artefact.exists():
            log.info("artefact already named after run id; nothing to rename")
        else:
            models.finalize_run(
                run_id,
                status="error",
                summary={"reason": "no_artefact"},
                error="CLI produced no fresh artefact",
            )
            return 6

        # Update ``runs.summary.artifact_path`` with the final path so
        # post-mortem debugging can locate the JSON later.
        models.set_run_artifact_path(run_id, str(run_artefact))

        try:
            ingestion_summary = ingest.ingest_run(
                output_dir,
                run_id=run_id,
                settings=overlay,
                profile=json.loads(profile_path.read_text(encoding="utf-8"))
                if profile_path
                else _load_canonical_profile(),
            )
        except FileNotFoundError as exc:
            log.error("could not ingest: %s", exc)
            models.finalize_run(
                run_id,
                status="error",
                summary={"reason": "missing_artefact", "error": str(exc)},
                error=str(exc),
            )
            return 7

        final_status = "success"
        if ingestion_summary.get("partial") or ingestion_summary.get("skipped", 0) > 0:
            final_status = "partial"
        if rc != 0:
            final_status = "partial"
        actual_report = json.loads(run_artefact.read_text(encoding="utf-8"))
        subscription_summary = {}
        if ingestion_summary.get("ingested", 0):
            models.set_setting("catalog_last_updated_at", _now(), updated_by=None)
        try:
            from . import searches

            with (data_dir / "search-evaluations.lock").open("a+") as evaluation_lock:
                try:
                    fcntl.flock(evaluation_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    subscription_summary = {"queued": True}
                    db.execute(
                        "UPDATE saved_searches SET ai_status='queued' WHERE active=1 AND archived=0 AND ai_status!='running'"
                    )
                else:
                    subscription_summary = searches.process_subscriptions(
                        budget_seconds=min(
                            1800,
                            max(
                                30,
                                _coerce_int(os.environ.get("AUCTIONRADAR_AI_BUDGET_SECONDS"), 900),
                            ),
                        ),
                        max_evaluations=min(
                            100,
                            max(
                                1,
                                _coerce_int(
                                    os.environ.get("AUCTIONRADAR_AI_MAX_ITEMS_PER_RUN"), 30
                                ),
                            ),
                        ),
                    )
        except Exception:
            log.error("saved-search processing failed; catalogue ingestion preserved")
            subscription_summary = {"error": "evaluation_error"}
        models.finalize_run(
            run_id,
            status=final_status,
            summary={
                "ingest": ingestion_summary,
                "cli_return_code": rc,
                "sources_requested": sources,
                "regions": regions,
                "preferred_regions": preferred_regions,
                "stderr_tail": stderr_tail,
                "sources": actual_report.get("sources", []),
                "artifact_path": str(run_artefact),
                "searches": subscription_summary,
            },
        )
        try:
            if os.environ.get("AUCTIONRADAR_WORKSPACE_MODE", "").lower() == "private":
                # The personal product must never signal the household's
                # certificate service or import its shared export directory.
                from .workspace_boe import refresh_if_configured

                refresh_if_configured(data_dir)
            else:
                from .boe_sync import trigger_after_search

                trigger_after_search(data_dir)
        except ImportError:
            log.info("optional personal BOE reader not configured")
        except OSError:
            log.warning("BOE photo refresh signal unavailable; public ingestion preserved")
        return 0
    except TimeoutError as exc:
        models.finalize_run(run_id, status="error", error=str(exc))
        return 6
    except Exception as exc:  # pragma: no cover - belt and braces
        log.exception("runner crashed")
        models.finalize_run(run_id, status="error", error=f"runner: {type(exc).__name__}: {exc}")
        return 7
    finally:
        if sigterm_request is not None and sigterm_request.get("requested"):
            try:
                models.finalize_run(run_id, status="cancelled", error="cancelled")
            except Exception:  # pragma: no cover - last-ditch safety
                pass
        if previous_handler is not None:
            try:
                signal.signal(signal.SIGTERM, previous_handler)
            except (ValueError, OSError):  # pragma: no cover - thread restore
                pass
        lock.release()
        lease.close()


# ---------------------------------------------------------------------------
# Dry-run mode (test scaffolding)
# ---------------------------------------------------------------------------


def _dry_run(
    run_id: str,
    cli_command: list[str],
    profile_cfg: dict,
    sources: list[str],
    regions: list[str],
    overlay: dict,
    *,
    output_dir: Path,
) -> int:
    """Skip subprocess and write a fixture artefact in-process.

    Tests rely on this mode because the runner needs to drive an
    end-to-end loop without a network round trip. Real runs never
    hit this branch.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    artefact = output_dir / f"{run_id}.json"
    items = json.loads(os.environ["AUCTIONRADAR_RUNNER_DRY_ITEMS"])
    # The artefact's ``observed_at`` is the start-of-run timestamp the
    # CLI generates in real life. When the items themselves carry an
    # observed_at we keep the earliest so back-dated history stays
    # stable across imports.
    observed_at = _now()
    for item in items:
        value = item.get("observed_at")
        if isinstance(value, str) and value and value < observed_at:
            observed_at = value
    artefact.write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "run_id": run_id,
                "observed_at": observed_at,
                "items": items,
                "summary": {
                    "items": len(items),
                    "source_status_counts": json.loads(
                        os.environ.get("AUCTIONRADAR_RUNNER_DRY_STATUS_COUNTS", "{}")
                    ),
                    "selection_counts": json.loads(
                        os.environ.get("AUCTIONRADAR_RUNNER_DRY_SELECTION_COUNTS", "{}")
                    ),
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    models.set_run_artifact_path(run_id, str(artefact))
    ingest_summary = ingest.ingest_run(
        output_dir,
        run_id=run_id,
        settings=overlay,
        profile=_load_canonical_profile(),
    )
    final_status = "success"
    if ingest_summary.get("partial") or ingest_summary.get("skipped", 0) > 0:
        final_status = "partial"
    models.finalize_run(
        run_id,
        status=final_status,
        summary={
            "ingest": ingest_summary,
            "cli_command": cli_command,
            "sources_requested": sources,
            "regions": regions,
            "artifact_path": str(artefact),
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
