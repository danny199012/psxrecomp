#!/usr/bin/env python3
"""Fetch PS1 Named_Boxarts from libretro-thumbnails (PNG + launcher TGA).

Backwards-compatible wrapper over `fetch_artwork.py`: this is the boxart-only
entry point that existing scaffold/migrate call sites (setup_project.sh/.ps1,
project_studio) import. New code should prefer `fetch_artwork.py`, which also
fetches title screens, snaps and logos and writes a JSON manifest.

Sources (tried in order):
  https://thumbnails.libretro.com/Sony - PlayStation/Named_Boxarts/<name>.png
  https://raw.githubusercontent.com/libretro-thumbnails/Sony_-_PlayStation/master/Named_Boxarts/<name>.png

Writes the original PNG (README) and a 32-bit uncompressed TGA (bottom-up BGRA)
for recomp-ui LAUNCHER_BOXART. Also writes launcher_assets/img/BOXART_SOURCE.txt
with URL + attribution.

Requires network. Prefer Pillow when installed; otherwise decodes common
8-bit RGBA/RGB PNGs with the stdlib.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import fetch_artwork as _art  # same directory: normal script / sys.path use
except ImportError:  # imported by path without the dir on sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import fetch_artwork as _art  # type: ignore

# Re-exported for callers that import these names from fetch_boxart.
LIBRETRO_HOST = _art.THUMB_HOST + _art.KIND_DIRS["boxart"] + "/"
GITHUB_RAW = _art.GITHUB_RAW + _art.KIND_DIRS["boxart"] + "/"
INVALID = _art.INVALID
sanitize_libretro_name = _art.sanitize_libretro_name
candidate_names = _art.candidate_names
url_for = _art.url_for
http_get = _art.http_get
png_to_rgba = _art.png_to_rgba
write_tga_bgra = _art.write_tga_bgra


def try_fetch(names: list[str]) -> tuple[bytes, str, str]:
    """Boxart-only fetch: (png_bytes, url, matched_name)."""
    return _art.try_fetch_kind("boxart", names)


def fetch_to_paths(
    tga_path: Path,
    *,
    cue_stem: str = "",
    display_name: str = "",
    extra_names: list[str] | None = None,
    png_path: Path | None = None,
) -> tuple[Path, Path, str]:
    """Fetch libretro Named_Boxarts PNG, write PNG + TGA + BOXART_SOURCE.txt."""
    names = _art.candidate_names(cue_stem, display_name, *(extra_names or []))
    if not names:
        raise ValueError("pass cue_stem and/or display_name")
    png, url, matched = _art.try_fetch_kind("boxart", names)
    tga_path = Path(tga_path)
    png_path = Path(png_path) if png_path else tga_path.with_suffix(".png")
    png_path.parent.mkdir(parents=True, exist_ok=True)
    png_path.write_bytes(png)
    try:
        w, h, rgba = _art.png_to_rgba(png)
        _art.write_tga_bgra(tga_path, w, h, rgba)
    except SystemExit as exc:
        raise RuntimeError(str(exc) or "PNG decode failed") from exc
    src_path = tga_path.parent / "BOXART_SOURCE.txt"
    _art.write_source_txt(
        src_path,
        [{"kind": "boxart", "matched": matched, "url": url}],
    )
    return tga_path, png_path, url


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--out",
        default="launcher_assets/img/boxart.tga",
        help="output TGA path (default: launcher_assets/img/boxart.tga)",
    )
    ap.add_argument("--cue-stem", default="", help="cue filename or stem hint")
    ap.add_argument("--display-name", default="", help="game display name hint")
    ap.add_argument(
        "--name", action="append", default=[], help="extra libretro name candidate"
    )
    ap.add_argument(
        "--source-out",
        default="",
        help="attribution file (default: next to TGA as BOXART_SOURCE.txt)",
    )
    ap.add_argument(
        "--png-out",
        default="",
        help="output PNG path (default: same stem as --out with .png)",
    )
    args = ap.parse_args(argv)

    names = _art.candidate_names(args.cue_stem, args.display_name, *args.name)
    if not names:
        print("error: pass --cue-stem and/or --display-name", file=sys.stderr)
        return 2

    print(f"  searching libretro boxart ({len(names)} candidates)…", file=sys.stderr)
    out = Path(args.out)
    png_out = Path(args.png_out) if args.png_out else out.with_suffix(".png")
    try:
        tga_path, png_path, url = fetch_to_paths(
            out,
            cue_stem=args.cue_stem,
            display_name=args.display_name,
            extra_names=list(args.name),
            png_path=png_out,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(str(exc) or "boxart fetch failed", file=sys.stderr)
        return 1
    print(f"  wrote {png_path}", file=sys.stderr)
    print(f"  wrote {tga_path}", file=sys.stderr)
    print(f"    {url}", file=sys.stderr)
    src_path = (
        Path(args.source_out) if args.source_out else out.parent / "BOXART_SOURCE.txt"
    )
    if args.source_out:
        src_path.write_text(
            (tga_path.parent / "BOXART_SOURCE.txt").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    print(f"  wrote {src_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
