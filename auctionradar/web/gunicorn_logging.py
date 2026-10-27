"""Redact share capabilities even in Gunicorn's request-error diagnostics."""

from gunicorn.glogging import Logger

from .security import PrivateLinkLogFilter


class PrivateLogger(Logger):
    def setup(self, cfg):
        super().setup(cfg)
        self.error_log.addFilter(PrivateLinkLogFilter())
        self.access_log.addFilter(PrivateLinkLogFilter())
