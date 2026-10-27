"""Host-wide bounded job admission across all private workspaces/workers."""

import fcntl
import os
import stat
from pathlib import Path


class JobCapacityError(RuntimeError):
    pass


class JobLease:
    def __init__(self, descriptor=None):
        self.descriptor = descriptor

    def close(self):
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def acquire(kind, *, environ=None):
    env = os.environ if environ is None else environ
    if env.get("AUCTIONRADAR_WORKSPACE_MODE", "").lower() != "private":
        return JobLease()
    if kind not in {"search", "evaluation"}:
        raise ValueError("invalid_job_kind")
    configured = env.get("AUCTIONRADAR_JOB_LOCK_DIR")
    if not configured:
        from .config import build_config

        configured = str(Path(build_config(env).IDENTITY_DB_PATH).parent / "job-slots")
    root = Path(configured)
    count = int(
        env.get(
            "AUCTIONRADAR_MAX_SEARCH_JOBS"
            if kind == "search"
            else "AUCTIONRADAR_MAX_EVALUATION_JOBS",
            "2" if kind == "search" else "1",
        )
    )
    if not 1 <= count <= 8:
        raise JobCapacityError("invalid_job_capacity")
    try:
        if root.is_symlink():
            raise JobCapacityError("job_admission_unavailable")
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        info = root.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise JobCapacityError("job_admission_unavailable")
        for slot in range(count):
            descriptor = os.open(
                root / f"{kind}-{slot}.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
            )
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o077
            ):
                os.close(descriptor)
                raise JobCapacityError("job_admission_unavailable")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(descriptor)
                continue
            return JobLease(descriptor)
    except OSError as exc:
        raise JobCapacityError("job_admission_unavailable") from exc
    raise JobCapacityError("job_capacity_busy")
