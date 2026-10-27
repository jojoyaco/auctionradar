import tempfile
import unittest
from pathlib import Path

from auctionradar.web.job_limits import JobCapacityError, acquire
from auctionradar.web.worker_environment import for_public_cli


class JobLimitTests(unittest.TestCase):
    def test_shared_slots_bound_parallel_searches_and_are_released(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {
                "AUCTIONRADAR_WORKSPACE_MODE": "private",
                "AUCTIONRADAR_JOB_LOCK_DIR": str(Path(directory) / "slots"),
            }
            first, second = acquire("search", environ=env), acquire("search", environ=env)
            try:
                with self.assertRaises(JobCapacityError):
                    acquire("search", environ=env)
                with acquire("evaluation", environ=env):
                    with self.assertRaises(JobCapacityError):
                        acquire("evaluation", environ=env)
                first.close()
                with acquire("search", environ=env):
                    pass
            finally:
                first.close()
                second.close()

    def test_insecure_or_symlink_lock_directory_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "linked").symlink_to(root, target_is_directory=True)
            with self.assertRaises(JobCapacityError):
                acquire(
                    "search",
                    environ={
                        "AUCTIONRADAR_WORKSPACE_MODE": "private",
                        "AUCTIONRADAR_JOB_LOCK_DIR": str(root / "linked"),
                    },
                )
            insecure = root / "insecure"
            insecure.mkdir(mode=0o777)
            insecure.chmod(0o777)
            with self.assertRaises(JobCapacityError):
                acquire(
                    "search",
                    environ={
                        "AUCTIONRADAR_WORKSPACE_MODE": "private",
                        "AUCTIONRADAR_JOB_LOCK_DIR": str(insecure),
                    },
                )

    def test_public_cli_has_no_credential_session_or_proxy_environment(self):
        env = for_public_cli(
            environ={
                "PATH": "/usr/bin",
                "AUCTIONRADAR_WORKSPACE_MODE": "private",
                "AUCTIONRADAR_SECRET_FILE": "/etc/session",
                "AUCTIONRADAR_CREDENTIALS_KEY_FILE": "/etc/vault",
                "AUCTIONRADAR_AI_API_KEY": "fixture-only",
                "HTTP_PROXY": "http://127.0.0.1",
                "CREDENTIALS_DIRECTORY": "/run/credentials",
            }
        )
        self.assertEqual(env, {"PATH": "/usr/bin", "AUCTIONRADAR_WORKSPACE_MODE": "private"})


if __name__ == "__main__":
    unittest.main()
