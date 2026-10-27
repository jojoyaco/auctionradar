"""Opt-in scheme forwarding for the bundled loopback-only nginx deployment."""

from werkzeug.middleware.proxy_fix import ProxyFix


class LoopbackProxyScheme:
    """Trust one overwritten scheme header, only from the local reverse proxy.

    No forwarded hostname, port, prefix or client IP is trusted by this
    middleware. Gunicorn must remain bound to loopback and nginx must overwrite
    X-Forwarded-Proto. A direct remote caller cannot mark HTTP as HTTPS.
    """

    def __init__(self, app):
        self.app = app
        self.proxied = ProxyFix(app, x_for=0, x_proto=1, x_host=0, x_port=0, x_prefix=0)

    def __call__(self, environ, start_response):
        if environ.get("REMOTE_ADDR") in {"127.0.0.1", "::1"} and environ.get(
            "HTTP_X_FORWARDED_PROTO"
        ) in {"http", "https"}:
            return self.proxied(environ, start_response)
        return self.app(environ, start_response)
