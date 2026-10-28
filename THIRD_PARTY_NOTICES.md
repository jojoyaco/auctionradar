# Third-party notices

The AuctionRadar-specific noncommercial license applies only to original
project material. It does **not** replace or restrict the independent licenses
of third-party libraries, system programs, portal content or data.

No third-party Python package source or binary is vendored here. The installer
downloads the exact dependencies pinned in `requirements.txt`; installed wheels
retain their own copyright and license files. Preserve those files when
redistributing dependencies or an installed system. The table below records
the license metadata observed for the locked runtime dependencies.

| Component | Version | Independent license | Upstream |
|---|---|---|---|
| Flask | 3.1.3 | BSD-3-Clause | https://github.com/pallets/flask |
| Flask-WTF | 1.3.0 | BSD-3-Clause | https://github.com/pallets-eco/flask-wtf |
| Gunicorn | 26.2.0 | MIT | https://github.com/benoitc/gunicorn |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | https://github.com/pyca/cryptography |
| Blinker | 1.9.0 | MIT | https://github.com/pallets-eco/blinker |
| cffi | 2.1.1 | MIT-0 | https://github.com/python-cffi/cffi |
| Click | 8.5.0 | BSD-3-Clause | https://github.com/pallets/click |
| itsdangerous | 2.2.0 | BSD-3-Clause | https://github.com/pallets/itsdangerous |
| Jinja2 | 3.1.6 | BSD-3-Clause | https://github.com/pallets/jinja |
| MarkupSafe | 3.0.4 | BSD-3-Clause | https://github.com/pallets/markupsafe |
| pycparser | 3.11 | BSD-3-Clause | https://github.com/eliben/pycparser |
| typing_extensions | 4.16.0 | PSF-2.0 | https://github.com/python/typing_extensions |
| Werkzeug | 3.1.9 | BSD-3-Clause | https://github.com/pallets/werkzeug |
| WTForms | 3.2.2 | BSD-3-Clause | https://github.com/pallets-eco/wtforms |

cryptography distributions also contain notices for their own dependencies;
retain the wheel's complete license directory rather than relying on this
summary alone. System packages such as Python, curl, OpenSSL, nginx and Poppler
are installed separately and retain the notices supplied by their distributor.

Advertisements, images, documents, auction terms, trademarks and source-portal
data remain subject to their original rights and access conditions. This
project grants no ownership or redistribution permission over those materials.

Development-only browser tooling is not bundled in the product archive. Review
the licenses of any browser or Playwright installation you distribute separately.
