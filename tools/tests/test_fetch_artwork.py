"""fetch_artwork.py / fetch_boxart.py: multi-kind libretro artwork fetching.

Everything here runs offline: `http_get` is stubbed, and PNG fixtures are built
with zlib + crc32 so the stdlib decoder path is exercised without Pillow.
"""
import json
import struct
import sys
import tempfile
import unittest
import urllib.error
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLKIT = ROOT / "tools" / "new_project_layout"
sys.path.insert(0, str(TOOLKIT))

import fetch_artwork as fa  # noqa: E402
import fetch_boxart as fb  # noqa: E402


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def make_png_rgb(width: int, height: int, rows: list[bytes]) -> bytes:
    """Minimal 8-bit RGB PNG (color_type 2), no interlacing, filter 0 rows."""
    assert len(rows) == height
    assert all(len(r) == width * 3 for r in rows)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + r for r in rows)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(raw))
        + _chunk(b"IEND", b"")
    )


PNG_2X2 = make_png_rgb(
    2,
    2,
    [
        bytes((255, 0, 0, 0, 255, 0)),  # row 0: red, green
        bytes((0, 0, 255, 255, 255, 255)),  # row 1: blue, white
    ],
)


class StubHttp:
    """Context manager replacing fa.http_get with a canned responder."""

    def __init__(self, respond):
        self._respond = respond
        self.calls: list[str] = []

    def __enter__(self):
        self._orig = fa.http_get

        def fake(url, timeout=60.0):
            self.calls.append(url)
            return self._respond(url)

        fa.http_get = fake
        return self

    def __exit__(self, *exc):
        fa.http_get = self._orig
        return False



class KindTests(unittest.TestCase):
    def test_canonical_kinds_map_to_libretro_sets(self):
        self.assertEqual(fa.KIND_DIRS["boxart"], "Named_Boxarts")
        self.assertEqual(fa.KIND_DIRS["title"], "Named_Titles")
        self.assertEqual(fa.KIND_DIRS["snap"], "Named_Snaps")
        self.assertEqual(fa.KIND_DIRS["logo"], "Named_Logos")

    def test_aliases_normalize(self):
        for alias, want in (
            ("box", "boxart"),
            ("Cover", "boxart"),
            ("titlescreen", "title"),
            ("screenshot", "snap"),
            ("LOGO", "logo"),
        ):
            self.assertEqual(fa.normalize_kind(alias), want)

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            fa.normalize_kind("cartridge")

    def test_thumbnail_bases_per_kind(self):
        host, raw = fa.thumbnail_bases("title")
        self.assertTrue(host.endswith("Named_Titles/"))
        self.assertTrue(raw.endswith("Named_Titles/"))
        self.assertIn("thumbnails.libretro.com", host)
        self.assertIn("raw.githubusercontent.com", raw)


class NameTests(unittest.TestCase):
    def test_sanitize_replaces_libretro_invalid_chars(self):
        self.assertEqual(fa.sanitize_libretro_name("A:B?C"), "A_B_C")
        self.assertEqual(fa.sanitize_libretro_name("Cover.png"), "Cover")

    def test_candidate_names_prefers_exact_then_region_variants(self):
        names = fa.candidate_names("Tomba! (USA)")
        self.assertEqual(names[0], "Tomba! (USA)")
        self.assertIn("Tomba!", names)
        self.assertIn("Tomba! (Europe)", names)
        # No duplicates.
        self.assertEqual(len(names), len(set(names)))

    def test_candidate_names_drops_cue_extension_and_path(self):
        names = fa.candidate_names("/dumps/Game.cue")
        self.assertIn("Game", names)
        self.assertTrue(all("/" not in n for n in names))

    def test_url_for_encodes_but_keeps_safe_chars(self):
        url = fa.url_for("https://x/", "Tomba! (USA)")
        self.assertTrue(url.endswith("Tomba!%20(USA).png"), url)


class PngTests(unittest.TestCase):
    def test_stdlib_decoder_rgb_to_rgba(self):
        w, h, rgba = fa._decode_png_rgba_stdlib(PNG_2X2)
        self.assertEqual((w, h), (2, 2))
        self.assertEqual(len(rgba), 2 * 2 * 4)
        self.assertEqual(tuple(rgba[0:4]), (255, 0, 0, 255))  # red
        self.assertEqual(tuple(rgba[4:8]), (0, 255, 0, 255))  # green
        self.assertEqual(tuple(rgba[8:12]), (0, 0, 255, 255))  # blue

    def test_tga_is_bottom_up_bgra_with_32bpp_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.tga"
            _, _, rgba = fa._decode_png_rgba_stdlib(PNG_2X2)
            fa.write_tga_bgra(path, 2, 2, rgba)
            blob = path.read_bytes()
        self.assertEqual(len(blob), 18 + 2 * 2 * 4)
        self.assertEqual(blob[2], 2)  # uncompressed true-color
        self.assertEqual(struct.unpack("<H", blob[12:14])[0], 2)  # width
        self.assertEqual(struct.unpack("<H", blob[14:16])[0], 2)  # height
        self.assertEqual(blob[16], 32)  # bits per pixel
        self.assertEqual(blob[17], 0x08)  # alpha bits, bottom-up origin
        first = blob[18:22]  # first stored pixel = image BOTTOM-left = blue
        self.assertEqual(tuple(first), (255, 0, 0, 255))  # BGRA of blue


def _not_found(url):
    raise urllib.error.HTTPError(url, 404, "Not Found", None, None)


class FetchTests(unittest.TestCase):
    def test_fetch_artwork_writes_png_tga_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with StubHttp(lambda url: PNG_2X2):
                manifest = fa.fetch_artwork(
                    out, ["boxart", "title"], cue_stem="Tomba!", tga_kinds=("boxart",)
                )
            self.assertEqual(manifest["ok"], ["boxart", "title"])
            self.assertTrue((out / "boxart.png").is_file())
            self.assertTrue((out / "boxart.tga").is_file())
            self.assertTrue((out / "title.png").is_file())
            # Only the requested kind gets a TGA.
            self.assertFalse((out / "title.tga").exists())
            self.assertEqual(manifest["assets"]["boxart"]["width"], 2)
            self.assertEqual(manifest["assets"]["boxart"]["tga"], "boxart.tga")
            self.assertEqual(
                manifest["assets"]["title"]["libretro_set"], "Named_Titles"
            )
            saved = json.loads((out / fa.MANIFEST_NAME).read_text())
            self.assertEqual(saved["source"], "libretro-thumbnails")
            self.assertEqual(saved["ok"], ["boxart", "title"])

    def test_missing_kind_is_recorded_not_fatal(self):
        def respond(url):
            if "Named_Boxarts" in url:
                return PNG_2X2
            return _not_found(url)

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with StubHttp(respond):
                manifest = fa.fetch_artwork(out, ["boxart", "logo"], cue_stem="Game")
            self.assertEqual(manifest["ok"], ["boxart"])
            self.assertEqual([m["kind"] for m in manifest["missing"]], ["logo"])
            self.assertTrue((out / "boxart.png").is_file())
            self.assertFalse((out / "logo.png").exists())

    def test_fetch_asset_requires_a_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                fa.fetch_asset("boxart", Path(tmp))

    def test_fetch_boxart_wrapper_keeps_legacy_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tga = Path(tmp) / "img" / "boxart.tga"
            with StubHttp(lambda url: PNG_2X2):
                got_tga, got_png, url = fb.fetch_to_paths(
                    tga, cue_stem="Tomba!", display_name="Tomba!"
                )
            self.assertEqual(got_tga, tga)
            self.assertTrue(got_png.is_file())
            self.assertTrue(tga.is_file())
            self.assertIn("Named_Boxarts", url)
            src = tga.parent / "BOXART_SOURCE.txt"
            self.assertTrue(src.is_file())
            self.assertIn("libretro-thumbnails", src.read_text())

    def test_cli_all_writes_every_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            with StubHttp(lambda url: PNG_2X2):
                rc = fa.main(
                    ["--out-dir", tmp, "--all", "--cue-stem", "Tomba!", "--quiet"]
                )
            self.assertEqual(rc, 0)
            for kind in fa.KIND_DIRS:
                self.assertTrue((Path(tmp) / f"{kind}.png").is_file(), kind)
            manifest = json.loads((Path(tmp) / fa.MANIFEST_NAME).read_text())
            self.assertEqual(manifest["ok"], sorted(fa.KIND_DIRS))

    def test_cli_returns_1_when_nothing_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            with StubHttp(_not_found):
                rc = fa.main(["--out-dir", tmp, "--cue-stem", "Nope", "--quiet"])
            self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()

