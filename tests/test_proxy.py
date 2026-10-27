import unittest

from werkzeug.test import Client
from werkzeug.wrappers import Request, Response

from auctionradar.web.proxy import LoopbackProxyScheme


class LoopbackProxyTests(unittest.TestCase):
    def setUp(self):
        @Request.application
        def application(request):
            return Response(
                f"{request.scheme}|{request.remote_addr}|{request.host}|{request.script_root}"
            )

        self.client = Client(LoopbackProxyScheme(application))

    def get(self, address, proto):
        return self.client.get(
            "/",
            headers={
                "X-Forwarded-Proto": proto,
                "X-Forwarded-For": "attacker-supplied",
                "X-Forwarded-Host": "attacker.invalid",
                "X-Forwarded-Port": "999",
                "X-Forwarded-Prefix": "/attacker",
            },
            environ_overrides={"REMOTE_ADDR": address},
        ).text

    def test_local_proxy_changes_only_scheme(self):
        self.assertEqual(self.get("127.0.0.1", "https"), "https|127.0.0.1|localhost|")
        self.assertEqual(self.get("::1", "https"), "https|::1|localhost|")

    def test_remote_client_cannot_forge_scheme(self):
        self.assertEqual(self.get("198.51.100.1", "https"), "http|198.51.100.1|localhost|")

    def test_header_chain_is_not_trusted(self):
        self.assertEqual(self.get("127.0.0.1", "https,http"), "http|127.0.0.1|localhost|")


if __name__ == "__main__":
    unittest.main()
