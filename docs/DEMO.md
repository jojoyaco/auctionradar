# Demo assets

[Back to the README](../README.md#see-it-in-action)

## Current capture

| Asset | Format | Purpose |
| --- | --- | --- |
| [Walkthrough](media/auctionradar-demo.mp4) | H.264 MP4, 1440 × 900, 25 fps, 20.16 s, 1.86 MB, no audio | Full-resolution walkthrough. |
| [README animation](media/auctionradar-demo.gif) | GIF, 960 × 600, 8.33 fps, 20.13 s, 161 frames, 3.20 MB, looping | Compact inline preview; below the 5 MiB limit. |
| [Light screenshot](media/auctionradar-overview.png) | PNG, 1440 × 900 | Static alternative to the animation. |
| [Dark screenshot](media/auctionradar-dark.png) | PNG, 1440 × 900 | Same synthetic grid in dark theme. |

### Scope and known limitations

This is the **real, unchanged AuctionRadar UI** in a fresh private workspace.
The five fictional records are ingested through the application's ingestion
code; review decisions and notes use the real application API and are read back
after a reload. The capture shows the listing grid and detail gallery, switches
between English and Spanish UI text and light and dark themes, and saves a
fictional review.

All listing titles, locations, prices, and other facts are deterministic
synthetic fixtures. Each title is marked `DEMO`; every image is an original,
procedurally generated same-origin SVG illustration visibly labelled as a
synthetic illustration, not a photograph. The UI and APIs are not mocked or
restyled for the capture. The test-only fixture and routes live under `tests/`
and are excluded from the product package.

The recorder clears inherited `AUCTIONRADAR_*` and `CREDENTIALS_DIRECTORY`
variables, uses a fresh temporary data directory and random disposable account
password, and binds only to `127.0.0.1`. Browser routing blocks every non-loopback
request. The fixture refuses search-run and saved-search mutations; it has no
configured source catalog, AI provider, or portal access. Registration and the
authenticated capture remain within the disposable local workspace. No
production data or credentials, portal/provider traffic, third-party images, or
AI inference are used.

## Reproduce

From the repository root, install the hash-pinned application dependencies and
the pinned browser library into a disposable work directory. Supply local
Chromium and FFmpeg executables:

```sh
DEMO_WORK=/tmp/opencode/auctionradar-demo
python3 -m venv "$DEMO_WORK/venv"
"$DEMO_WORK/venv/bin/pip" install --require-hashes -r requirements.txt
npm install --prefix "$DEMO_WORK/node" --ignore-scripts --no-audit --no-fund --no-save playwright-core@1.64.1
export PLAYWRIGHT_BROWSERS_PATH="$DEMO_WORK/browser-tools"
node "$DEMO_WORK/node/node_modules/playwright-core/cli.js" install --no-remove ffmpeg
NODE_PATH="$DEMO_WORK/node/node_modules" node tests/record_demo.cjs \
  --python "$DEMO_WORK/venv/bin/python" \
  --chrome /path/to/chrome \
  --ffmpeg /path/to/ffmpeg \
  --work-root "$DEMO_WORK/work" \
  --evidence-dir "$DEMO_WORK/evidence"
```

Playwright's small recording helper is separate from the full FFmpeg executable
passed with `--ffmpeg`; install both before recording. Chromium is supplied
explicitly, so the command above does not download a browser.

The script writes the four current media assets to `docs/media/`, uses a fresh
temporary recording and application directory under `--work-root`, and writes
redacted verification metadata plus theme screenshots and sample frames under
`--evidence-dir` (by default, `--work-root/evidence`). It removes the temporary
recording and application state after its owned preview/browser processes exit;
if shutdown cannot be verified, it leaves that directory for inspection. Use
`--media-dir` to validate a new recording outside the checkout without replacing
the committed assets. It checks real API persistence, zero browser page errors,
zero non-loopback requests, image/video dimensions, H.264 output, and the GIF
size ceiling. It does not run a source search or claim portal coverage.

Playwright closes the browser context before its recording is converted. FFmpeg
uses `palettegen`/`paletteuse` for the GIF and H.264 for the MP4. See the
[Playwright video documentation](https://playwright.dev/docs/videos),
[browser context API](https://playwright.dev/docs/api/class-browser#browser-new-context),
[FFmpeg palette generation](https://ffmpeg.org/ffmpeg-filters.html#palettegen),
and [palette application](https://ffmpeg.org/ffmpeg-filters.html#paletteuse).

## Historical capture and rights boundary

An earlier capture, recorded on 2026-10-27 from source revision `8854033`, used
public BOE and Escogecasa results and listing photographs. It showed partial,
bounded source results and a different manual-review walkthrough; those facts
describe only that historical capture, not the synthetic assets above.

Replacing the current four files does **not** delete or rewrite older media in
Git history, other refs, CI artifacts, backups, or external copies. Rights to
historical portal material remain unresolved; this synthetic replacement is not
legal clearance or a complete publication-history audit. Review prior refs and
copies separately before making publication claims. See the
[publication checklist](RELEASING.md#before-making-the-repository-public) and
[third-party notices](../THIRD_PARTY_NOTICES.md).
