"""Public image destinations are reviewed, not arbitrary listing-controlled URLs."""

from auctionradar.web import models, urls
from tests.test_webapp_auth import _WebappFixture


class PublicShareImageTests(_WebappFixture):
    def setUp(self):
        super().setUp()
        self.make_user(username="image-owner")
        self.login(username="image-owner")
        self.urls = [
            "https://ngb-repositorio.urbaniza.com/inmuebles/photo.jpg",
            "http://localhost/static/synthetic.png",
            "http://127.0.0.1:19999/recipient-service",
            "https://192.168.1.1/private.jpg",
            "https://attacker.example/redirect-to-private",
            "https://ngb-repositorio.urbaniza.com.attacker.example/spoof.jpg",
            "http://ngb-repositorio.urbaniza.com/insecure.jpg",
            "https://ngb-repositorio.urbaniza.com:8443/other-service.jpg",
        ]
        item = {
            "asset_type": "housing",
            "source_id": "synthetic",
            "url": "https://example.org/listing/audit-images",
            "title": "Synthetic image destinations",
            "photos": [{"url": url} for url in self.urls],
        }
        models.upsert_item(
            item_id="image-policy",
            asset_type="housing",
            source_id="synthetic",
            source_ids=["synthetic"],
            first_seen="2026-10-27T00:00:00+00:00",
            last_seen="2026-10-27T00:00:00+00:00",
            data=item,
            listing_url=item["url"],
            raw_fingerprint="synthetic-images",
        )

    def _share(self):
        response = self.api_call("POST", "/api/items/image-policy/share", json={})
        self.assertEqual(response.status_code, 200)
        return response.get_json()["opaque"]

    def test_public_payload_excludes_unreviewed_private_and_redirect_origins(self):
        token = self._share()
        response = self.app.test_client().get("/api/public/" + token)
        self.assertEqual(response.status_code, 200)
        photos = response.get_json()["item"]["data"]["photos"]
        self.assertEqual([photo["url"] for photo in photos], self.urls[:2])

    def test_share_csp_limits_images_even_after_an_allowed_origin_redirects(self):
        token = self._share()
        response = self.app.test_client().get("/share/" + token)
        self.assertEqual(response.status_code, 200)
        csp = response.headers["Content-Security-Policy"]
        image_policy = next(part.strip() for part in csp.split(";") if "img-src" in part)
        self.assertEqual(
            image_policy.split(),
            [
                "img-src",
                "'self'",
                "https://ngb-repositorio.urbaniza.com",
                "https://subastas.boe.es",
            ],
        )
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")

    def test_owner_api_preserves_custom_source_images(self):
        response = self.api_call("GET", "/api/items/image-policy")
        self.assertEqual(response.status_code, 200)
        photos = response.get_json()["item"]["photos"]
        self.assertEqual([photo["url"] for photo in photos], self.urls)

    def test_malformed_application_origin_fails_closed(self):
        for origin in ("https://example.org:invalid", "//example.org", ""):
            with self.subTest(origin=origin):
                self.assertFalse(
                    urls.is_safe_public_image_url(self.urls[0], application_origin=origin)
                )
