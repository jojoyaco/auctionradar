# Demo assets

[Back to the README](../README.md#see-it-in-action)

## Files

| Asset | Format | Purpose |
| --- | --- | --- |
| [Walkthrough](media/auctionradar-demo.mp4) | H.264 MP4, 1440 × 900, 25 fps, about 26 seconds, no audio | Full-resolution video and GIF conversion source. |
| [README animation](media/auctionradar-demo.gif) | GIF, 960 × 600, 8 fps, about 4.2 MiB | Inline, looping GitHub README demo. |
| [Light screenshot](media/auctionradar-overview.png) | PNG, 1440 × 900 | Static alternative to the animation. |
| [Dark screenshot](media/auctionradar-dark.png) | PNG, 1440 × 900 | Dark-theme reference. |

## What was recorded

The application was run from the initial product release (`8854033`) on
October 27, 2026, in a disposable private workspace bound to loopback. Chromium
was driven with Playwright; API responses and listing data were not mocked,
and no application code or page styling was changed for the recording.

Public BOE and Escogecasa sources were added through **Sources and access**.
A real search was completed with the application runner before recording:
six BOE items and six Escogecasa items were ingested. Both sources reported
partial, bounded coverage. This is not an exhaustive inventory.

The video starts with **With photos** enabled, so the opening grid contains
Escogecasa results. It then shows:

1. Filtering to housing and searching for Castellón.
2. Advancing a listing photograph and opening its real gallery and details.
3. Entering a demonstration note, saving it, and marking the item as OK.
4. Clearing the text search and switching to dark mode and back.

Both review writes returned HTTP 200. The saved note and OK status were read
back after a page reload. The final recorded workflow had no JavaScript page
errors. The MP4 and GIF were decoded and representative frames inspected.

No production accounts, credentials, certificates, or private production notes
were used. The visible `demo-researcher` identity and note belong only to the
disposable capture account. No public share token was created. Listing titles,
prices, and photographs are source-published material, not generated examples;
they can change and remain subject to their owners' terms. See
[third-party notices](../THIRD_PARTY_NOTICES.md).

### Scope and known limitations

This capture demonstrates the manual review workflow, not AI inference,
end-to-end search queueing, authenticated BOE photographs, bidding, or a
production deployment. The prompt-based AI review described in the README is
an optional application feature; no AI provider was used in this recording.

During preparation, a UI-queued worker exited before completing its run; a
successful public search was instead completed by invoking the application
runner with explicit workspace paths. The recording does not imply that the
queued-worker issue was repaired.

Changing the language interactively also produced a pre-existing
`Maximum call stack size exceeded` error: `onLanguageChange` in
`auctionradar/web/static/app.js` redispatches the event it handles. The final
recording stays in English and does not demonstrate language switching.
Neither issue was changed as part of this documentation/media task.

## Regenerate the GIF

Run from the repository root with a full FFmpeg installation providing
`palettegen` and `paletteuse`:

```sh
ffmpeg -y -i docs/media/auctionradar-demo.mp4 \
  -filter_complex 'fps=8,scale=960:-2:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=none:diff_mode=rectangle' \
  -loop 0 docs/media/auctionradar-demo.gif
```

The complete walkthrough is retained at normal speed. Resolution, frame rate,
and palette size are reduced to keep the README asset small. The MP4 is the
higher-quality alternative. The README links a static PNG for readers who
prefer no animation.

To record a new walkthrough, use a fresh local instance and disposable account,
configure public sources, complete a real search, and check source limitations.
Record a 1440 × 900 viewport with Playwright's `recordVideo` option; close the
browser context before saving the video. Keep login and workspace setup outside
the final cut. Review every frame for sensitive information before committing.

References: [Playwright video recording](https://playwright.dev/docs/videos),
[FFmpeg palette generation](https://ffmpeg.org/ffmpeg-filters.html#palettegen),
and [palette application](https://ffmpeg.org/ffmpeg-filters.html#paletteuse).
