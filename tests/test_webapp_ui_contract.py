"""Static contract: bilingual pages, theme initialization, and preserved CSP."""

import html
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class UiContractTests(unittest.TestCase):
    def test_all_template_translation_keys_exist(self):
        source = (ROOT / "auctionradar/web/static/ui.js").read_text()
        keys = {
            json.loads('"' + s + '"')
            for s in re.findall(r'^\s*"((?:\\.|[^"\\])*)"\s*:', source, re.M)
        }
        required = set()
        for template in (ROOT / "auctionradar/web/templates").glob("*.html"):
            text = template.read_text()
            required.update(html.unescape(key) for key in re.findall(r'data-i18n="([^"]+)"', text))
            for value in re.findall(r'data-i18n-attr="([^"]+)"', text):
                for pair in html.unescape(value).split(";"):
                    if ":" in pair:
                        required.add(pair.split(":", 1)[1].strip())
        self.assertEqual(sorted(required - keys), [])

    def test_each_page_initializes_preferences_before_styles(self):
        for name in ("app", "login", "share"):
            with self.subTest(page=name):
                text = (ROOT / f"auctionradar/web/templates/{name}.html").read_text()
                self.assertLess(text.index("/static/ui.js"), text.index("/static/app.css"))
                self.assertIn("data-language-select", text)
                self.assertIn("data-theme-toggle", text)
                self.assertNotRegex(text, r"<script(?![^>]*\bsrc=)[^>]*>\s*\S")
                self.assertNotRegex(text, r"\s(?:style|on\w+)\s*=")

    def test_gallery_and_shortcut_controls_are_keyboard_buttons(self):
        for name in ("app", "share"):
            text = (ROOT / f"auctionradar/web/templates/{name}.html").read_text()
            self.assertRegex(text, r"<button[^>]+data-lightbox-prev")
            self.assertRegex(text, r"<button[^>]+data-lightbox-next")
            self.assertIn("data-lightbox-counter", text)
        self.assertIn(
            "data-shortcuts-dialog", (ROOT / "auctionradar/web/templates/app.html").read_text()
        )
