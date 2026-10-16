"""probe_disc.py / game.toml.in: the scaffolded game.toml carries the video
enhancement defaults and the graphics presets.

Why this test exists. probe_disc.py rewrites game.toml WHOLESALE, so the
[quality.*] tables and the [video] enhancement keys a project actually ships
come from render_game_toml(), not from templates/game.toml.in. When the probe
wrote only `renderer` and `aspect_ratio`, every scaffolded game ran with PS1
dithering at its hardware default and had no presets at all -- no hardware
detection, no dithering knob, nothing a player could change. This pins both
renderers of that block.

Everything is validated with tomllib (real TOML), and every preset key is
checked against the framework's supported set: a key outside it is a
game.toml LOAD ERROR, so a typo here would break every new project.
"""
import sys
import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLKIT = ROOT / "tools" / "new_project_layout"
sys.path.insert(0, str(TOOLKIT))

import probe_disc as pd  # noqa: E402

# Mirrors recompiler/src/config_loader.cpp kQualityPlayerKeys +
# kQualityPresetOnlyKeys. A key outside the union is a load error.
PLAYER_KEYS = {
    "texture_filtering", "fmv_filter", "antialiasing", "dynamic_resolution",
    "dynamic_resolution_min", "frame_generation", "render_thread",
    "present_thread", "geometry_correction", "perspective_texturing",
    "frame_interpolation", "frame_interpolation_fps",
}
PRESET_ONLY_KEYS = {
    "supersample", "texture_lod", "anisotropic_filtering",
    "fmv_chroma_smoothing", "bloom", "dithering", "pgxp_depth_buffer",
    "pgxp_color_correction", "pgxp_seam",
}
SUPPORTED = PLAYER_KEYS | PRESET_ONLY_KEYS

# [video] keys that are rejected inside a [quality.*] table.
REJECTED_IN_PRESET = {"internal_resolution", "antialiasing_mode", "renderer",
                      "scanlines", "supersampling", "aspect_ratio", "vsync"}

PRESET_NAMES = ["low", "medium", "high", "ultra"]
DITHER_MODES = {"on", "scaled", "fine", "off"}


def sample_probe() -> "pd.DiscProbe":
    return pd.DiscProbe(
        cue_path="/dumps/Game (USA).cue",
        cue_name="Game (USA).cue",
        bin_name="Game (USA) (Track 1).bin",
        track_count=1,
        data_track_size=123456,
        data_track_md5="0" * 32,
        data_track_sha1="1" * 40,
        data_track_crc32="deadbeef",
        boot_exe="SLUS_005.62",
        serial="SLUS-00562",
        display_name="Game",
        load_address="0x80010000",
        entry_pc="0x80010000",
        text_size="0x1000",
        stack_base="0x801FFFF0",
    )


def rendered_toml() -> dict:
    text = pd.render_game_toml(
        sample_probe(), disc_rel="disc/Game (USA).cue", out_dir="disc", players=2
    )
    return tomllib.loads(text)


class ProbeGameTomlTests(unittest.TestCase):
    def setUp(self):
        self.doc = rendered_toml()

    def test_rendered_toml_is_valid_toml(self):
        # tomllib.loads() already raised if not; assert the shape we depend on.
        self.assertIn("video", self.doc)
        self.assertIn("quality", self.doc)

    def test_video_keeps_renderer_and_aspect(self):
        self.assertEqual(self.doc["video"]["renderer"], "opengl")
        self.assertEqual(self.doc["video"]["aspect_ratio"], "4:3")

    def test_video_enables_the_quality_defaults(self):
        video = self.doc["video"]
        self.assertEqual(video["dithering"], "fine")
        self.assertEqual(video["supersample"], 1.5)
        self.assertIs(video["dynamic_resolution"], True)
        self.assertEqual(video["dynamic_resolution_min"], "display")
        self.assertEqual(video["texture_filtering"], "bilinear")
        self.assertEqual(video["texture_lod"], "mipmap")
        self.assertEqual(video["anisotropic_filtering"], 4)
        self.assertEqual(video["antialiasing_mode"], "fxaa")
        self.assertIs(video["fmv_chroma_smoothing"], True)

    def test_all_four_presets_are_offered(self):
        self.assertEqual(sorted(self.doc["quality"]), sorted(PRESET_NAMES))

    def test_ultra_is_empty_so_it_is_exactly_video(self):
        self.assertEqual(self.doc["quality"]["ultra"], {})

    def test_preset_ladder_dials_back_monotonically(self):
        q = self.doc["quality"]
        # supersample must not rise as the tier drops.
        self.assertEqual(q["ultra"].get("supersample", 1.5), 1.5)
        self.assertEqual(q["high"]["supersample"], 1.25)
        self.assertEqual(q["medium"]["supersample"], 1.0)
        self.assertEqual(q["low"]["supersample"], 1.0)
        # the low tier gives up the elective texture work.
        self.assertEqual(q["low"]["texture_filtering"], "nearest")
        self.assertEqual(q["low"]["texture_lod"], "off")
        self.assertEqual(q["low"]["anisotropic_filtering"], 1)
        self.assertIs(q["low"]["fmv_chroma_smoothing"], False)
        self.assertEqual(q["low"]["dynamic_resolution_min"], "native")

    def test_every_preset_key_is_in_the_supported_set(self):
        for name, body in self.doc["quality"].items():
            for key in body:
                self.assertIn(key, SUPPORTED,
                              f"[quality.{name}] key {key!r} is not supported "
                              "by the framework (load error)")
                self.assertNotIn(key, REJECTED_IN_PRESET,
                                 f"[quality.{name}] key {key!r} is rejected "
                                 "inside a preset")

    def test_enhancement_values_parse_at_their_declared_types(self):
        video = self.doc["video"]
        self.assertIsInstance(video["dithering"], str)
        self.assertIsInstance(video["supersample"], float)
        self.assertIsInstance(video["anisotropic_filtering"], int)
        self.assertIsInstance(video["fmv_chroma_smoothing"], bool)
        self.assertIn(video["dithering"], DITHER_MODES)

    def test_dithering_in_a_preset_uses_a_supported_mode(self):
        for body in self.doc["quality"].values():
            if "dithering" in body:
                self.assertIn(body["dithering"], DITHER_MODES)


class TemplateGameTomlTests(unittest.TestCase):
    """The no-disc path writes templates/game.toml.in instead."""

    def rendered(self) -> dict:
        text = (TOOLKIT / "templates" / "game.toml.in").read_text(encoding="utf-8")
        text = text.replace("@GAME_NAME@", "Game").replace("@PLAYERS@", "2")
        text = text.replace("@BOOT_EXE@", "SLUS_005.62")
        return tomllib.loads(text)

    def test_template_parses_and_matches_the_probe_block(self):
        doc = self.rendered()
        probe = rendered_toml()
        # The two sources of truth must agree on the enhancement block.
        self.assertEqual(doc["video"]["dithering"], probe["video"]["dithering"])
        self.assertEqual(doc["video"]["supersample"], probe["video"]["supersample"])
        self.assertEqual(doc["quality"], probe["quality"])

    def test_template_preset_keys_are_supported(self):
        for name, body in self.rendered()["quality"].items():
            for key in body:
                self.assertIn(key, SUPPORTED, f"[quality.{name}] key {key!r}")


class MainTomlEmitTests(unittest.TestCase):
    """psxrecomp-toml (recompiler/src/main_toml.cpp) is the third scaffold path.

    Its emitted TOML is reconstructed from the source's own `toml += "..."` and
    `toml += R"TOML(...)TOML"` statements so all three writers are compared as
    real TOML rather than by substring.
    """

    def emitted(self) -> dict:
        src = (ROOT / "recompiler" / "src" / "main_toml.cpp").read_text(
            encoding="utf-8"
        )
        parts = [
            m.group(1).encode("utf-8").decode("unicode_escape")
            for m in re.finditer(r'toml \+= "((?:[^"\\]|\\.)*)";', src)
        ]
        raw = re.search(r'toml \+= R"TOML\((.*?)\)TOML";', src, re.S)
        self.assertIsNotNone(raw, "main_toml.cpp no longer emits the enhancement "
                                  "block as a raw string")
        parts.append(raw.group(1))
        return tomllib.loads("\n".join(parts))

    def test_emitted_toml_keeps_the_guarded_default_renderer_lines(self):
        # runtime/tests/test_default_renderer_guards.py checks these substrings.
        src = (ROOT / "recompiler" / "src" / "main_toml.cpp").read_text(
            encoding="utf-8"
        )
        self.assertIn('toml += "[video]\\n";', src)
        self.assertIn('toml += "renderer = \\"opengl\\"\\n";', src)

    def test_all_three_scaffold_paths_agree(self):
        probe = rendered_toml()
        template = TemplateGameTomlTests().rendered()
        emitted = self.emitted()
        for doc, who in ((template, "game.toml.in"), (emitted, "main_toml.cpp")):
            self.assertEqual(doc["quality"], probe["quality"],
                             f"{who} presets drifted from probe_disc.py")
            for key in ("dithering", "supersample", "dynamic_resolution",
                        "dynamic_resolution_min", "texture_filtering",
                        "texture_lod", "anisotropic_filtering",
                        "antialiasing_mode", "fmv_chroma_smoothing"):
                self.assertEqual(doc["video"][key], probe["video"][key],
                                 f"{who} [video] {key} drifted from probe_disc.py")

    def test_emitted_preset_keys_are_supported(self):
        for name, body in self.emitted()["quality"].items():
            for key in body:
                self.assertIn(key, SUPPORTED, f"[quality.{name}] key {key!r}")


if __name__ == "__main__":
    unittest.main()

