# WallPaperCapital

Build a wallpaper library of the world's capital cities from Wikimedia Commons, and
put one on your Windows lock screen at every logon.

Two commands:

- **`wpcapital download`** — fetches one photograph per capital for the 193 UN member
  states plus the Holy See and the State of Palestine: 195 states, 203 capitals (some
  countries have several, like South Africa or Bolivia).
- **`wpcapital lockscreen`** — picks one at random, stamps the capital and country in
  the bottom-right corner, and sets it as the lock screen.

Country and capital names come from Wikidata, images from Wikimedia Commons. Images
are converted to 2560×1440 JPEG in `data/wallpaper/`. The desktop background is never
touched.

Labels are in French — that is the display language the library was built with, and
the filenames follow it (`le_caire_egy.jpg`). English names appear on a second line
when they differ.

## Install

Requires [uv](https://docs.astral.sh/uv/) and Python 3.10+.

```bash
uv sync
```

Run every command from the project root: the default paths are relative to it.

## Download the wallpapers

```bash
uv run wpcapital download --limit 3     # try it on three capitals
uv run wpcapital download               # all of them, about 30 minutes
```

A full run is deliberately slow — there is a pause between API calls. It is
resumable: capitals already downloaded are skipped unless you pass `--force`.

Useful flags:

| Flag | Effect |
| --- | --- |
| `--capital "Abou Dabi"` | One capital; the English name works too |
| `--country-code ZAF` | Every capital of one country |
| `--force` | Re-download and re-evaluate existing files |
| `--quality-only` | Only accept images the Commons community has distinguished |
| `--keep-original-format` | Keep the source file instead of converting to 16:9 JPEG |

`--help` lists the rest (`--output-dir`, `--width`, `--pause`, …).

## Set the lock screen

```bash
uv run wpcapital lockscreen --capital Budapest --dry-run   # preview, changes nothing
uv run wpcapital lockscreen                                # random draw, applied
uv run wpcapital lockscreen --restore                      # put the old one back
```

To run it at every logon:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\Install-StartupTask.ps1
```

This registers a scheduled task that fires 30 seconds after logon and runs without a
console. Add `-Unregister` to remove it. No administrator rights are needed: the
WinRT API it uses acts on the current user only.

Runtime state lives in `%LOCALAPPDATA%\WallPaperCapital\` — the tagged image, a log,
and a history of the last 40 draws (the last 20 are excluded from the next draw, so
the same city does not come back two days running).

Two things worth knowing:

- If **Windows Spotlight** is on it will overwrite the image. The command says so;
  `--disable-spotlight` turns it off for your user.
- The default margins keep the tag 7 % from the right edge and 6 % from the bottom.
  Windows crops the 16:9 image to fill the screen, and a 16:10 display loses about
  5 % of the width on each side — a tag flush with the edge would be cut off. On a
  narrower screen, raise `--margin-x`.

## How an image gets chosen

Searching Commons for a city name does not work: the full-text index has no idea
where a photo was taken. Searching "Algiers" used to return a prize-winning shot of
the harbour of Sète.

So candidates come from what Commons genuinely ties to a place, in decreasing order
of trust:

1. **View categories** — `Views of X`, `Cityscapes of X`, `X at night`… An image in
   one of those shows the city by construction.
2. **The Wikidata reference image** for the city (property P18).
3. **The city's own Commons category.**
4. **Coordinate search**, within 7 km of the centre.
5. **Full-text search**, last resort only, for capitals too rarely photographed to be
   categorised at all.

Survivors are then ranked on: where they came from, community distinctions (featured,
quality, valued, picture of the year — capped, because they stack), how many Wikimedia
pages reuse the image, whether the city is named in the title, and the framing. What
counts is not the file's width but the width surviving the centred 16:9 crop: a
5061×1200 panorama leaves a 2133 px band, where a 7585×2217 keeps 3941.

Rejected outright: anything that is not a photograph (maps, engravings, book scans),
interiors, ceremonies and official visits, war scenes, vehicles, orbital views, and
photographs older than 1995 — they no longer show today's city.

The chosen source is recorded in the manifest as `source_kind`.

### Overriding a choice

No heuristic rescues a capital that Commons photographs badly. `data/overrides.json`
forces a specific file:

```json
{
  "Bichkek": "Bishkek City's business center.jpg",
  "Basseterre": "File:Une belle vue de Basseterre.jpg"
}
```

The key accepts the French name, the English name, or the ISO alpha-3 country code;
the `File:` prefix is optional. A file that cannot be found is reported and normal
selection resumes.

## Files produced

- `data/capitals_wikidata.json` — cached list of countries and capitals, with
  coordinates and Commons category.
- `data/wallpaper/manifest.json` — country, capital, source, author and licence for
  every image.

## Licensing

**Downloading an image does not grant every right to use it.** Check
`data/wallpaper/manifest.json` before any public or commercial use and respect the
licence recorded for each image. The manifest keeps the source page and attribution
details so that check is easy.

The code in this repository is MIT licensed (see [LICENSE](LICENSE)); the images are
not, and each carries its own terms.

## Development

```bash
uv run ruff check .   # lint
uv run mypy           # type-check
uv run pytest         # tests
```

Layout:

```
src/wallpaper_capital/
  cli.py          both subcommands
  wikidata.py     the capitals list, cached
  commons.py      Wikimedia Commons API client
  selection.py    building the candidate pool
  scoring.py      the ranking rules
  download.py     fetching and recording
  imaging.py      cropping and encoding
  lockscreen/     rendering the tag and talking to WinRT
```
