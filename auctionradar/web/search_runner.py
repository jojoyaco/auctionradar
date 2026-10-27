"""Asynchronous saved-search evaluation without fetching source portals again."""

import argparse
import fcntl
import os
from pathlib import Path

from . import db, searches
from .config import build_config
from .job_limits import JobCapacityError, acquire


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--search-id", type=int)
    args = parser.parse_args(argv)
    cfg = build_config()
    db.configure_runtime(cfg.WORKSPACE_MODE, cfg.IDENTITY_DB_PATH)
    db.init_db(cfg.DATA_DIR)
    path = Path(cfg.DATA_DIR) / "search-evaluations.lock"
    try:
        lease = acquire("evaluation")
    except JobCapacityError:
        db.execute(
            "UPDATE saved_searches SET ai_status='pending',eval_pid=NULL,last_error='job_capacity_busy' WHERE ai_status IN ('queued','running')"
        )
        return 75
    with lease, path.open("a+") as lock:
        # Wait for the existing evaluator so requests are not lost in the final
        # window of its queue loop. The API prevents another process for that ID.
        fcntl.flock(lock, fcntl.LOCK_EX)
        searches.process_subscriptions(
            args.search_id,
            budget_seconds=min(
                1800, max(30, int(os.environ.get("AUCTIONRADAR_AI_BUDGET_SECONDS", "900")))
            ),
            max_evaluations=min(
                100, max(1, int(os.environ.get("AUCTIONRADAR_AI_MAX_ITEMS_PER_RUN", "30")))
            ),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
