"""No-network regression tests for user-controlled source destinations."""

import socket
import subprocess
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import auctionradar.connectors.catalog as adapters
import auctionradar.network as network


def answer(address):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    return (family, socket.SOCK_STREAM, 6, "", (address, 443))


class PublicNetworkTests(unittest.TestCase):
    def setUp(self):
        self.version_patch = patch.object(network, "_check_curl", return_value=None)
        self.version_patch.start()
        self.addCleanup(self.version_patch.stop)

    def test_rejects_credentials_ambiguous_and_non_https_urls(self):
        for url in (
            "http://example.org/",
            "https://user:pass@example.org/",
            "file:///etc/passwd",
            "https://example.org:8443/",
            "https://localhost/",
            "https://a.home.arpa/",
            "https://example.org/?api_key=hidden",
            "https://example.org/?access_token=hidden",
            "https://example.org/?token=hidden",
            "https://example.org/\\@127.0.0.1",
            "https://exam\tple.org/",
            "https://[fe80::1%25eth0]/",
        ):
            with self.subTest(url=url), self.assertRaises(network.PublicNetworkError):
                network.validate_url(url)

    def test_rejects_private_and_transition_addresses(self):
        for address in (
            "127.0.0.1",
            "10.0.0.1",
            "169.254.169.254",
            "192.168.50.100",
            "100.64.0.1",
            "0.0.0.0",
            "224.0.0.1",
            "::1",
            "fc00::1",
            "fe80::1",
            "::ffff:127.0.0.1",
            "64:ff9b::7f00:1",
            "2002:7f00:1::1",
            "2001:db8::1",
        ):
            with (
                self.subTest(address=address),
                patch.object(network.socket, "getaddrinfo", return_value=[answer(address)]),
            ):
                with self.assertRaises(network.PublicNetworkError):
                    network.resolve_public("https://source.example/")

    def test_mixed_public_private_dns_answers_fail_closed(self):
        with patch.object(
            network.socket,
            "getaddrinfo",
            return_value=[answer("93.184.216.34"), answer("10.0.0.2")],
        ):
            with self.assertRaises(network.PublicNetworkError):
                network.resolve_public("https://source.example/")

    @staticmethod
    def response(
        command, *, status=200, redirect="", address="93.184.216.34", body=b"safe", **kwargs
    ):
        Path(command[command.index("--output") + 1]).write_bytes(body)
        return subprocess.CompletedProcess(
            command, 0, f"{status}\ntext/html\n{redirect}\n{address}".encode(), b""
        )

    def fetch(self, **kwargs):
        return network.fetch_public(
            "https://source.example/listings", timeout=2, max_body=1024, user_agent="test", **kwargs
        )

    def test_pins_dns_and_disables_proxy_automatic_redirects_and_curlrc(self):
        with (
            patch.object(
                network.socket, "getaddrinfo", return_value=[answer("93.184.216.34")]
            ) as dns,
            patch.object(network.subprocess, "run", side_effect=self.response) as transport,
        ):
            result = self.fetch()
        self.assertIsNone(result["error"])
        self.assertEqual(result["body_bytes"], b"safe")
        command = transport.call_args.args[0]
        self.assertEqual(command[:2], ["curl", "-q"])
        self.assertNotIn("--location", command)
        self.assertEqual(command[command.index("--noproxy") + 1], "*")
        self.assertEqual(command[command.index("--proxy") + 1], "")
        self.assertEqual(
            command[command.index("--resolve") + 1], "source.example:443:93.184.216.34"
        )
        self.assertEqual(dns.call_count, 1)

    def test_redirect_to_metadata_never_makes_second_request(self):
        def dns(host, *args, **kwargs):
            return [answer("169.254.169.254" if host == "169.254.169.254" else "93.184.216.34")]

        def redirect(command, **kwargs):
            return self.response(
                command, status=302, redirect="https://169.254.169.254/latest/meta-data", **kwargs
            )

        with (
            patch.object(network.socket, "getaddrinfo", side_effect=dns),
            patch.object(network.subprocess, "run", side_effect=redirect) as transport,
        ):
            result = self.fetch()
        self.assertEqual(result["error"], "non_public_destination")
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(result["body_bytes"], b"")

    def test_rebinding_same_host_redirect_is_resolved_again(self):
        with (
            patch.object(
                network.socket,
                "getaddrinfo",
                side_effect=[[answer("93.184.216.34")], [answer("127.0.0.1")]],
            ),
            patch.object(
                network.subprocess,
                "run",
                side_effect=lambda command, **kw: self.response(
                    command, status=302, redirect="https://source.example/other", **kw
                ),
            ) as transport,
        ):
            result = self.fetch()
        self.assertEqual(result["error"], "non_public_destination")
        self.assertEqual(transport.call_count, 1)

    def test_cross_origin_post_not_replayed(self):
        with (
            patch.object(network.socket, "getaddrinfo", return_value=[answer("93.184.216.34")]),
            patch.object(
                network.subprocess,
                "run",
                side_effect=lambda command, **kw: self.response(
                    command, status=307, redirect="https://other.example/", **kw
                ),
            ) as transport,
        ):
            result = self.fetch(request_body=b'{"id":1}')
        self.assertEqual(result["error"], "cross_origin_post_redirect")
        self.assertEqual(transport.call_count, 1)

    def test_peer_mismatch_discards_body(self):
        with (
            patch.object(network.socket, "getaddrinfo", return_value=[answer("93.184.216.34")]),
            patch.object(
                network.subprocess,
                "run",
                side_effect=lambda command, **kw: self.response(command, address="127.0.0.1", **kw),
            ),
        ):
            result = self.fetch()
        self.assertEqual(result["error"], "public_peer_mismatch")
        self.assertEqual(result["body_bytes"], b"")

    def test_oversized_chunked_body_is_discarded(self):
        with (
            patch.object(network.socket, "getaddrinfo", return_value=[answer("93.184.216.34")]),
            patch.object(
                network.subprocess,
                "run",
                side_effect=lambda command, **kw: self.response(command, body=b"x" * 1025, **kw),
            ),
        ):
            result = self.fetch()
        self.assertEqual(result["error"], "public_response_too_large")
        self.assertTrue(result["truncated"])
        self.assertEqual(result["body_bytes"], b"")

    def test_adapter_private_transport_rejects_original_secret_url(self):
        with patch.object(network.subprocess, "run") as transport:
            result = adapters.fetch(
                "https://source.example/?api_key=sensitive", {"public_network_only": True}
            )
        self.assertEqual(result["error"], "invalid_public_source_url")
        transport.assert_not_called()

    def test_bare_token_is_also_redacted_from_legacy_evidence(self):
        self.assertNotIn(
            "token=", adapters.public_url("https://example.org/feed?token=fixture&id=1")
        )

    def test_stalled_dns_respects_request_deadline(self):
        release = threading.Event()
        finished = threading.Event()

        def slow_dns(*args, **kwargs):
            release.wait(timeout=2)
            finished.set()
            return [answer("93.184.216.34")]

        started = time.monotonic()
        try:
            with (
                patch.object(network.socket, "getaddrinfo", side_effect=slow_dns),
                patch.object(network.subprocess, "run") as transport,
            ):
                result = network.fetch_public(
                    "https://source.example/", timeout=0.05, max_body=1024, user_agent="test"
                )
                self.assertEqual(result["error"], "public_dns_timeout")
                self.assertLess(time.monotonic() - started, 0.5)
                transport.assert_not_called()
        finally:
            release.set()
            self.assertTrue(finished.wait(timeout=2))

    def test_old_curl_is_rejected_before_download(self):
        self.version_patch.stop()
        network._check_curl.cache_clear()
        self.addCleanup(network._check_curl.cache_clear)
        response = subprocess.CompletedProcess(["curl"], 0, b"curl 8.3.0 (test)\n", b"")
        with patch.object(network.subprocess, "run", return_value=response) as transport:
            result = self.fetch()
        self.assertEqual(result["error"], "curl_8_4_or_newer_required")
        self.assertEqual(transport.call_count, 1)

    def test_adapter_extracts_safe_result_with_shared_parser(self):
        with (
            patch.object(network.socket, "getaddrinfo", return_value=[answer("93.184.216.34")]),
            patch.object(network.subprocess, "run", side_effect=self.response),
        ):
            result = adapters.fetch("https://source.example/", {"public_network_only": True})
        self.assertIsNone(result["error"])
        self.assertEqual(result["body"], "safe")
        self.assertEqual(result["http_status"], 200)

    def test_named_connector_preserves_private_source_identity(self):
        source = {"id": "my_auctions", "adapter_id": "boe", "url": "https://subastas.boe.es/"}
        found = {"items": [{"id": "boe-fixture", "source_id": "boe", "source_ids": ["boe"]}]}
        with patch.object(adapters, "discover", return_value=found) as discover:
            result = adapters.discover_configured(
                source, {"seeds": {"my_auctions": ["https://example.org/fixture"]}}
            )
        self.assertEqual(discover.call_args.args[0]["id"], "boe")
        self.assertTrue(discover.call_args.args[1]["public_network_only"])
        self.assertEqual(discover.call_args.args[1]["seeds"], ["https://example.org/fixture"])
        self.assertEqual(
            result["items"][0],
            {
                "id": "my_auctions-fixture",
                "source_id": "my_auctions",
                "source_ids": ["my_auctions"],
            },
        )

    def test_generic_source_name_does_not_silently_choose_named_connector(self):
        source = {"id": "boe", "adapter_id": "generic_html", "url": "https://example.org/"}
        with patch.object(adapters, "discover", return_value={"items": []}) as discover:
            adapters.discover_configured(source, {})
        self.assertEqual(discover.call_args.args[0]["id"], "generic_html")

    def test_unknown_connector_cannot_execute_user_code(self):
        with self.assertRaises(ValueError):
            adapters.discover_configured({"id": "mine", "adapter_id": "run_untrusted_code"}, {})


if __name__ == "__main__":
    unittest.main()
