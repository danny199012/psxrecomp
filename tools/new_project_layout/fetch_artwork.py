#!/usr/bin/env python3
"""Fetch PS1 artwork from libretro-thumbnails (PNG + optional launcher TGA).

libretro-thumbnails publishes a per-system tree with several *named* sets. This
helper fetches any of them for one title and writes a manifest so a launcher or
README can point at whichever asset it wants:

  boxart  Named_Boxarts  the retail cover (recomp-ui LAUNCHER_BOXART)
  title   Named_Titles   the in-game title screen
  snap    Named_Snaps    a representative gameplay screenshot
  logo    Named_Logos    the transparent game logo

Sources (tried in order, per kind):
  https://thumbnails.libretro.com/Sony - PlayStation/<Named_*>/<name>.png
  https://raw.githubusercontent.com/libretro-thumbnails/Sony_-_PlayStation/master/<Named_*>/<name>.png

For each matched asset the PNG is written (for the README) and, when requested,
a 32-bit uncompressed TGA (bottom-up BGRA) for recomp-ui. A JSON manifest
(`ARTWORK_MANIFEST.json`) records the matched libretro name, source URL and
pixel size of everything that landed, plus attribution.

Requires network. Prefer Pillow when installed; otherwise decodes common
8-bit RGBA/RGB PNGs with the stdlib. `fetch_boxart.py` is a thin, backwards
compatible wrapper over this module (boxart kind only).
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

THUMB_HOST = "https://thumbnails.libretro.com/Sony%20-%20PlayStation/"
GITHUB_RAW = (
    "https://raw.githubusercontent.com/libretro-thumbnails/"
    "Sony_-_PlayStation/master/"
)
ATTRIBUTION = "https://github.com/libretro-thumbnails/libretro-thumbnails"
USER_AGENT = "psxrecomp-fetch_artwork/1.0"

# The libretro thumbnail set for each artwork kind we support.
KIND_DIRS = {
    "boxart": "Named_Boxarts",
    "title": "Named_Titles",
    "snap": "Named_Snaps",
    "logo": "Named_Logos",
}

# Friendly aliases accepted on the command line / in the API.
KIND_ALIASES = {
    "box": "boxart",
    "cover": "boxart",
    "boxarts": "boxart",
    "named_boxarts": "boxart",
    "titles": "title",
    "title_screen": "title",
    "titlescreen": "title",
    "named_titles": "title",
    "snaps": "snap",
    "screenshot": "snap",
    "shot": "snap",
    "named_snaps": "snap",
    "logos": "logo",
    "named_logos": "logo",
}

# Characters libretro-thumbnails replaces with '_' in file names.
INVALID = '&*/:`<>?\\|"'

MANIFEST_NAME = "ARTWORK_MANIFEST.json"


def normalize_kind(kind: str) -> str:
    """Map a user-supplied kind (or alias) to a canonical KIND_DIRS key."""
    key = (kind or "").strip().lower()
    key = KIND_ALIASES.get(key, key)
    if key not in KIND_DIRS:
        raise ValueError(
            f"unknown artwork kind {kind!r}; expected one of "
            f"{', '.join(sorted(KIND_DIRS))}"
        )
    return key


def thumbnail_bases(kind: str) -> tuple[str, str]:
    """The two candidate base URLs for a canonical artwork kind."""
    sub = KIND_DIRS[normalize_kind(kind)]
    return THUMB_HOST + sub + "/", GITHUB_RAW + sub + "/"


def sanitize_libretro_name(name: str) -> str:
    out = name.strip()
    if out.lower().endswith(".png"):
        out = out[:-4]
    for ch in INVALID:
        out = out.replace(ch, "_")
    return out


def candidate_names(*hints: str) -> list[str]:
    """Build Redump / libretro-style name candidates."""
    seen: list[str] = []

    def add(s: str) -> None:
        s = s.strip()
        if not s:
            return
        # Drop path / extension
        s = Path(s).name
        if s.lower().endswith(".cue"):
            s = s[:-4]
        s = sanitize_libretro_name(s)
        if s and s not in seen:
            seen.append(s)

    for h in hints:
        if not h:
            continue
        add(h)
        stem = Path(h).stem if h.endswith(".cue") else h
        stem = stem.strip()
        # Strip region then re-add the common regions (USA first: the most
        # frequent Redump / libretro pairing).
        stripped = re.sub(
            r"\s*\((USA|Europe|Japan|World|En,Fr,De,Es,It)\)\s*$",
            "",
            stem,
            flags=re.I,
        ).strip()
        add(stripped)
        add(f"{stripped} (USA)")
        add(f"{stripped} (Europe)")
        add(f"{stripped} (Japan)")
        add(stem)

    return seen


def url_for(base: str, name: str) -> str:
    filename = sanitize_libretro_name(name) + ".png"
    return base + urllib.parse.quote(filename, safe="()-_!.'")


def http_get(url: str, timeout: float = 60.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if "png" not in ctype and not data.startswith(b"\x89PNG"):
            raise RuntimeError(f"not a PNG ({ctype}): {url}")
        return data




def try_fetch_kind(kind: str, names: list[str]) -> tuple[bytes, str, str]:
    """Return (png_bytes, url, matched_name) for the first candidate that hits."""
    if not names:
        raise ValueError("no candidate names supplied")
    last_err: Exception | None = None
    for name in names:
        for base in thumbnail_bases(kind):
            url = url_for(base, name)
            try:
                return http_get(url), url, name
            except (
                urllib.error.HTTPError,
                urllib.error.URLError,
                TimeoutError,
                RuntimeError,
            ) as e:
                last_err = e
                continue
    raise FileNotFoundError(
        f"{kind} not found for candidates {names[:8]}… ({last_err})"
    )


def png_to_rgba(png: bytes) -> tuple[int, int, bytes]:
    """Return (w, h, RGBA bytes). Pillow preferred; stdlib RGBA/RGB PNG fallback."""
    try:
        from PIL import Image  # type: ignore
        import io

        im = Image.open(io.BytesIO(png)).convert("RGBA")
        return im.width, im.height, im.tobytes()
    except ImportError:
        pass

    return _decode_png_rgba_stdlib(png)


def _decode_png_rgba_stdlib(png: bytes) -> tuple[int, int, bytes]:
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        raise SystemExit("invalid PNG signature (install Pillow for more formats)")
    pos = 8
    width = height = 0
    bit_depth = 8
    color_type = -1
    idat = bytearray()
    while pos + 8 <= len(png):
        length = struct.unpack(">I", png[pos : pos + 4])[0]
        ctype = png[pos + 4 : pos + 8]
        data = png[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if ctype == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(">IIBB", data[:10])
        elif ctype == b"IDAT":
            idat.extend(data)
        elif ctype == b"IEND":
            break
    if width <= 0 or height <= 0:
        raise SystemExit("PNG missing IHDR")
    if bit_depth != 8 or color_type not in (2, 6):
        raise SystemExit(
            f"PNG color_type={color_type} bit_depth={bit_depth} unsupported; "
            "install Pillow (pip install pillow)"
        )
    raw = zlib.decompress(bytes(idat))
    bpp = 4 if color_type == 6 else 3
    stride = width * bpp
    rows: list[bytearray] = []
    i = 0
    prev = bytearray(stride)
    for _ in range(height):
        filt = raw[i]
        i += 1
        row = bytearray(raw[i : i + stride])
        i += stride
        if filt == 0:
            pass
        elif filt == 1:  # Sub
            for x in range(stride):
                left = row[x - bpp] if x >= bpp else 0
                row[x] = (row[x] + left) & 0xFF
        elif filt == 2:  # Up
            for x in range(stride):
                row[x] = (row[x] + prev[x]) & 0xFF
        elif filt == 3:  # Average
            for x in range(stride):
                left = row[x - bpp] if x >= bpp else 0
                row[x] = (row[x] + ((left + prev[x]) // 2)) & 0xFF
        elif filt == 4:  # Paeth
            for x in range(stride):
                a = row[x - bpp] if x >= bpp else 0
                b = prev[x]
                c = prev[x - bpp] if x >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                row[x] = (row[x] + pr) & 0xFF
        else:
            raise SystemExit(f"unsupported PNG filter {filt}")
        rows.append(row)
        prev = row
    rgba = bytearray(width * height * 4)
    o = 0
    for row in rows:
        if bpp == 4:
            rgba[o : o + stride] = row
            o += stride
        else:
            for x in range(0, stride, 3):
                rgba[o : o + 3] = row[x : x + 3]
                rgba[o + 3] = 255
                o += 4
    return width, height, bytes(rgba)


def write_tga_bgra(path: Path, width: int, height: int, rgba: bytes) -> None:
    """Uncompressed 32-bit TGA, bottom-up, BGRA (recomp-ui friendly)."""
    if len(rgba) != width * height * 4:
        raise SystemExit("RGBA size mismatch")
    header = bytearray(18)
    header[2] = 2  # uncompressed true-color
    struct.pack_into("<H", header, 12, width)
    struct.pack_into("<H", header, 14, height)
    header[16] = 32
    header[17] = 0x08  # 8-bit alpha attribute; origin bottom-left (bottom-up)
    bgra = bytearray(width * height * 4)
    for y in range(height):
        src_y = height - 1 - y
        for x in range(width):
            si = (src_y * width + x) * 4
            di = (y * width + x) * 4
            r, g, b, a = rgba[si : si + 4]
            bgra[di : di + 4] = bytes((b, g, r, a))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(header) + bytes(bgra))


def fetch_asset(
    kind: str,
    out_dir: Path,
    *,
    cue_stem: str = "",
    display_name: str = "",
    extra_names: list[str] | None = None,
    write_tga: bool = False,
    timeout: float = 60.0,
) -> dict:
    """Fetch one artwork kind into `out_dir`.

    Returns a manifest entry; raises FileNotFoundError / RuntimeError when the
    asset cannot be fetched or decoded.
    """
    kind = normalize_kind(kind)
    names = candidate_names(cue_stem, display_name, *(extra_names or []))
    if not names:
        raise ValueError("pass cue_stem and/or display_name")
    png, url, matched = try_fetch_kind(kind, names)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    png_path = out_dir / f"{kind}.png"
    png_path.write_bytes(png)
    entry: dict = {
        "kind": kind,
        "libretro_set": KIND_DIRS[kind],
        "matched": matched,
        "url": url,
        "png": png_path.name,
        "bytes": len(png),
    }
    try:
        w, h, rgba = png_to_rgba(png)
    except SystemExit as exc:  # decode failure: keep the PNG, report why
        raise RuntimeError(str(exc) or "PNG decode failed") from exc
    entry["width"], entry["height"] = w, h
    if write_tga:
        tga_path = out_dir / f"{kind}.tga"
        write_tga_bgra(tga_path, w, h, rgba)
        entry["tga"] = tga_path.name
    return entry


def fetch_artwork(
    out_dir: Path,
    kinds: list[str],
    *,
    cue_stem: str = "",
    display_name: str = "",
    extra_names: list[str] | None = None,
    tga_kinds: tuple[str, ...] = ("boxart",),
    manifest_path: Path | None = None,
    timeout: float = 60.0,
) -> dict:
    """Fetch several artwork kinds; never raises for a single missing asset.

    Every requested kind is attempted. A kind that is not in the libretro set
    (or whose download fails) is recorded in `manifest["missing"]` rather than
    aborting the others, because a title often has a cover but no logo.
    """
    out_dir = Path(out_dir)
    canon: list[str] = []
    for k in kinds:
        k = normalize_kind(k)
        if k not in canon:
            canon.append(k)
    tga_set = {normalize_kind(k) for k in tga_kinds}

    manifest: dict = {
        "source": "libretro-thumbnails",
        "attribution": ATTRIBUTION,
        "generated_by": "psxrecomp fetch_artwork",
        "assets": {},
        "missing": [],
    }
    for kind in canon:
        try:
            entry = fetch_asset(
                kind,
                out_dir,
                cue_stem=cue_stem,
                display_name=display_name,
                extra_names=extra_names,
                write_tga=kind in tga_set,
                timeout=timeout,
            )
        except (FileNotFoundError, ValueError, RuntimeError) as exc:
            manifest["missing"].append({"kind": kind, "reason": str(exc)})
            continue
        manifest["assets"][kind] = entry

    manifest["ok"] = sorted(manifest["assets"])
    if manifest_path is None:
        manifest_path = out_dir / MANIFEST_NAME
    manifest_path = Path(manifest_path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def write_source_txt(path: Path, entries: list[dict]) -> None:
    """Legacy attribution file: one block per matched asset + attribution."""
    lines = ["Artwork sourced from libretro-thumbnails.", ATTRIBUTION]
    for entry in entries:
        lines.append(
            f"[{entry['kind']}] matched name: {entry['matched']}\n"
            f"    URL: {entry['url']}"
        )
    lines.append("PNG is for the README; TGA (boxart) is for recomp-ui.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _parse_kinds(args: argparse.Namespace) -> list[str]:
    if args.all:
        return list(KIND_DIRS)
    kinds = list(args.kind or [])
    if not kinds:
        kinds = ["boxart"]
    return [normalize_kind(k) for k in kinds]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--out-dir",
        default="launcher_assets/img",
        help="directory for PNG/TGA/manifest (default: launcher_assets/img)",
    )
    ap.add_argument(
        "--kind",
        action="append",
        default=[],
        help="artwork kind: boxart|title|snap|logo (repeatable; default boxart)",
    )
    ap.add_argument(
        "--all",
        action="store_true",
        help="fetch every supported kind (boxart, title, snap, logo)",
    )
    ap.add_argument("--cue-stem", default="", help="cue filename or stem hint")
    ap.add_argument("--display-name", default="", help="game display name hint")
    ap.add_argument(
        "--name", action="append", default=[], help="extra libretro name candidate"
    )
    ap.add_argument(
        "--tga",
        action="append",
        default=[],
        help="kind that also gets a launcher TGA (default: boxart)",
    )
    ap.add_argument(
        "--no-tga", action="store_true", help="write PNGs only, no launcher TGA"
    )
    ap.add_argument(
        "--manifest",
        default="",
        help=f"manifest path (default: <out-dir>/{MANIFEST_NAME})",
    )
    ap.add_argument(
        "--source-out",
        default="",
        help="attribution file (default: <out-dir>/BOXART_SOURCE.txt)",
    )
    ap.add_argument(
        "--quiet", action="store_true", help="suppress progress lines on stderr"
    )
    args = ap.parse_args(argv)

    kinds = _parse_kinds(args)
    if args.no_tga:
        tga_kinds: tuple[str, ...] = ()
    elif args.tga:
        tga_kinds = tuple(normalize_kind(k) for k in args.tga)
    else:
        tga_kinds = ("boxart",)

    out_dir = Path(args.out_dir)
    manifest_path = Path(args.manifest) if args.manifest else out_dir / MANIFEST_NAME

    def log(msg: str) -> None:
        if not args.quiet:
            print(msg, file=sys.stderr)

    log(f"  searching libretro artwork ({', '.join(kinds)})…")
    try:
        manifest = fetch_artwork(
            out_dir,
            kinds,
            cue_stem=args.cue_stem,
            display_name=args.display_name,
            extra_names=list(args.name),
            tga_kinds=tga_kinds,
            manifest_path=manifest_path,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if not manifest["assets"]:
        print("artwork fetch failed: no assets matched", file=sys.stderr)
        for miss in manifest["missing"]:
            print(f"  {miss['kind']}: {miss['reason']}", file=sys.stderr)
        return 1

    for kind, entry in sorted(manifest["assets"].items()):
        extra = f" + {entry['tga']}" if "tga" in entry else ""
        log(
            f"  {kind}: {entry['png']}{extra} "
            f"({entry['width']}x{entry['height']}) <- {entry['matched']}"
        )
    for miss in manifest["missing"]:
        log(f"  {miss['kind']}: not found")
    log(f"  wrote {manifest_path}")

    src_path = (
        Path(args.source_out) if args.source_out else out_dir / "BOXART_SOURCE.txt"
    )
    write_source_txt(src_path, list(manifest["assets"].values()))
    log(f"  wrote {src_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
