# Launcher and services roadmap

Where the player-facing launcher requests (aspect ratios, DX11/DX12/Vulkan
selection, automatic artwork, RetroAchievements) actually land in this
framework: what already exists, what is safe to add, and what must not be
faked.

Status legend: **DONE** (in this branch) · **READY** (small, safe) ·
**CAMPAIGN** (multi-session) · **DO-NOT-STUB**.

## Where the launcher lives

- The GUI launcher is **recomp-ui**, a *separate repo* each game project pulls
  in as a submodule (`PSX_RECOMP_UI`, `runtime/runtime.cmake:352`). It is not in
  this tree. `recomp_launcher.h` is its C ABI, consumed here under the
  `RECOMP_LAUNCHER` define.
- This repo owns the **runtime contract**: the player's `settings.toml`
  (`UserSettings`, `recompiler/src/config_loader.h:1520+`), the seed/readback
  loops in `runtime/src/main.cpp` (~line 18.5k), and every renderer or feature
  behind them.
- The standalone BIOS runtime (`PSXRecomp.exe`) has its own minimal file
  picker; it has no ImGui.

So a launcher *row* is a recomp-ui change, while the *behavior* behind it is a
change here. Every item below is split into those two halves.

---

## 1. Aspect-ratio / display-mode options — READY (mod-owned, not a generic row)

Already implemented in the framework:

- Arbitrary fixed aspect: `psx_mod_set_fixed_display_aspect(num, den)`
  (`runtime/include/mod_plugins.h:466`) and resize-driven
  `psx_mod_set_adaptive_display_aspect(max_num, max_den)` (`:475`).
- Runtime plumbing: `gte_set_display_aspect` (GTE X-squash, `runtime/src/gte.cpp:1031`),
  `gl_renderer_set_display_aspect` / `vk_renderer_set_display_aspect`,
  `gpu_ws_configure`, and `psx_gl_fit_wide_aspect` (narrow a wide aspect to what
  the GL context can allocate at the current internal scale).
- `settings.toml` already carries `aspect_num` / `aspect_den`
  (`config_loader.h:1615`), and the launcher round-trips them
  (`main.cpp:18636`).

**Why there is no generic "Aspect ratio" launcher row — and should not be one.**
Widescreen is deliberately *mod-owned* on PSX. `runtime/src/main.cpp:17257` pins
`constexpr bool ws_offered = false;`, and `:18198` clamps any persisted aspect
back to 4:3 unless a **trusted activation plugin** engages it. A generic row
would reintroduce exactly the unvalidated 16:9 state that policy removed
(`recompiler/src/config_loader.h:1416` documents the same reasoning for
`[widescreen] offer`).

Safe next step (no new renderer work): expose aspect ratios as a **mod option**
in a title's built-in package, so the choice is per-title, validated, and
hardcore-legal:

```toml
[[feature]]
id = "display"
label = "Display"

[[feature.option]]
id = "aspect"
label = "Aspect ratio"
type = "choice"
choices = [{ id = "4:3" }, { id = "16:9" }, { id = "21:9" }, { id = "fit" }]
```

The activation callback calls `psx_mod_set_fixed_display_aspect(...)` or
`psx_mod_set_adaptive_display_aspect(...)` — see `docs/MOD_PACKAGES.md`.
"Fit to window" already exists as adaptive with `(0, 0)`.

---

## 2. DX11 / DX12 / Vulkan selection — CAMPAIGN (and DX11 is the wrong target)

Today the renderer IDs are `VIDEO_RENDERER_SOFTWARE|OPENGL|VULKAN`
(`recompiler/src/config_loader.h:42-45`), with `[video] renderer` and
`[video] offer_vulkan` gating launcher visibility (`:619`). Vulkan is
hand-written (`runtime/src/gpu_vk_renderer.c`) and explicitly incomplete — its
`vk_renderer_set_display_aspect` was a no-op stub until recently, and the
native-wide compositor is unimplemented (`docs/WIDESCREEN.md`).

Writing two more hand-rolled backends (DX11, DX12) is the worst option: it
triples the renderer surface for features that today exist only in GL.
`CLAUDE.md` RULE -1 also forbids landing the *schema* for a backend that does
not exist ("no stubs"), so a `renderer = "d3d12"` value must not appear before
the backend is real.

The faithful route is **SDL_GPU**, shipped by SDL3 3.4 — the version this tree
already requires (`runtime/runtime.cmake:245`). One backend covers:

- `"direct3d12"` → DX12
- `"vulkan"` → Vulkan
- `"metal"` → Metal (macOS)

Selection is `SDL_CreateGPUDevice(shader_formats, debug_mode, name)`; enumerate
with `SDL_GetNumGPUDrivers` / `SDL_GetGPUDriver`, and detect support with
`SDL_GPUSupportsShaderFormats`. Shaders are precompiled per backend
(DXIL / SPIR-V / MSL) via `SDL_shadercross`.

> **DX11 is the wrong target.** SDL_GPU's documented drivers are `vulkan`,
> `direct3d12`, and `metal`. There is **no `direct3d11` SDL_GPU driver**, so a
> literal DX11 path would mean a fourth hand-written backend or an
> `SDL_Renderer` D3D11 fallback. Recommend DX12 + Vulkan + Metal through
> SDL_GPU and dropping DX11 from the request.

Phased plan:

- **P1** — `gpu_sdlgpu_renderer.c` implementing the `gr_*` renderer contract
  (`runtime/include/gpu_render.h`) for the *present* path only, so it can be
  A/B'd against GL before it is trusted for rasterization.
- **P2** — move rasterization and VRAM upload onto the same backend
  (`gpu_vram_dirty`, `gpu_hd_textures`).
- **P3** — `[video] renderer = "d3d12" | "vulkan" | "metal"` plus
  `offer_d3d12` / `offer_vulkan` following the existing pattern, and a
  recomp-ui `[video] renderer` row listing only drivers the build supports.
- **P4** — retire the bespoke Vulkan backend once SDL_GPU/Vulkan passes the
  same qualification (`docs/ENHANCEMENTS.md` R2/R3).

Acceptance is the existing renderer qualification (`docs/ENHANCEMENTS.md` R3,
`tools/gpu_parity.py`) on the same routes — not "it compiles".

---

## 3. Automatic artwork — DONE (this branch) + next steps

Shipped here: `tools/new_project_layout/fetch_artwork.py`

- fetches the libretro `Named_Boxarts` / `Named_Titles` / `Named_Snaps` /
  `Named_Logos` sets for a title, writing PNG (plus a launcher TGA for the
  boxart) and `ARTWORK_MANIFEST.json` recording the matched name, source URL,
  and pixel size of everything that landed;
- a kind absent from the set is recorded in the manifest's `missing` list
  instead of aborting the others (a title often has a cover but no logo);
- `fetch_boxart.py` is now a thin backwards-compatible wrapper over it, so the
  scaffold/migrate call sites and their `BOXART_SOURCE.txt` output are
  unchanged;
- 16 offline unit tests: `tools/tests/test_fetch_artwork.py`.

Run it with:

```sh
python3 tools/new_project_layout/fetch_artwork.py --all \
  --cue-stem "Tomba!" --display-name "Tomba!" --out-dir launcher_assets/img
```

Next, each small and independent:

- **DONE here** — `--all-artwork` / `--no-all-artwork` on
  `setup_project.sh` and `-AllArtwork` / `-NoAllArtwork` on
  `setup_project.ps1`, beside `--fetch-boxart`; the extra PNGs and the manifest
  are staged into the initial commit;
- auto-grab on disc insert: the launcher already has the disc identity
  (`runtime/src/disc_identity.cpp`), so it can reuse this tool's logic — keep it
  opt-in and cached next to `settings.toml`;
- a second provider (ScreenScraper / IGDB) behind a `--provider` flag; libretro
  needs no API key, which is why it stays the default;
- use `title` / `snap` on the launcher's game tile and `logo` on the library
  header.

---

## 4. RetroAchievements — CAMPAIGN (vendored, real integration only)

Nothing exists today: there is no RetroAchievements string anywhere in the
tree. The faithful integration is **rcheevos** (MIT, C —
github.com/RetroAchievements/rcheevos), the same library DuckStation and
RetroArch use:

- `rc_client_t` is the preferred API. It owns login, game identification, the
  active achievement list, and event callbacks; the *client* supplies the
  **memory-read**, **HTTP**, and **UI** callbacks — rcheevos makes no network
  calls itself.
- `rhash` (`rc_hash_generate`, PlayStation console id) produces the RA game
  hash from the disc image. This tree already hashes discs
  (`runtime/src/disc_identity.cpp`), so the same buffer feeds it.
- Hardcore mode must be honoured: no savestates, rewind, or turbo while
  hardcore is on. That intersects features this runtime already owns
  (`runtime/src/psx_rewind.c`, load acceleration), so it is a real gate, not a
  label.

Plan:

- **P1** — vendor rcheevos as a pinned archive (the `third_party/deps.manifest`
  pattern used by libchdr) plus its MIT notice in `runtime/licenses/`.
- **P2** — `rc_client` host adapter: memory callback over the guest RAM
  accessors, HTTP callback over the existing netplay auth HTTP path
  (`runtime/src/main.cpp:13413`, where `psx_netplay_auth` owns the HTTP
  worker), and UI callbacks into the in-game menu (`psx_menu_host.h`) plus
  `host_osd` toasts.
- **P3** — `[retroachievements]` in `settings.toml` (username, token, enabled,
  hardcore) and a recomp-ui login row; never ship credentials in a repo.
- **P4** — gate rewind/savestate/turbo behind hardcore, progress
  notifications, leaderboard submissions.

---

## 5. More ideas (backlog, cheapest first)

- **Controller auto-mapping** from SDL's gamepad database
  (`SDL_GetGamepadMapping`), removing first-run manual binding for known pads.
- **CRT / shader presets**: the GL post chain already has scanline, FXAA and
  SMAA hooks (`runtime/src/gl_postfx.inc`); expose a "Display filter" preset
  list rather than a strength slider per effect.
- **Save-state thumbnails**: the runtime already captures the present surface
  (`runtime/include/overlay_capture.h`); use it for the launcher's state list.
- **One library manifest**: merge the scaffold's `catalog_identity.json` and
  `ARTWORK_MANIFEST.json` into a single `library.json` the launcher reads.
- **Cheats**: the libretro cheat DB (`.cht`) is a plain text format that maps
  onto the existing patch machinery.
- **Discord Rich Presence** (opt-in), alongside the existing OSD.

---

## Verification and tracking

- Runtime changes must keep the 4:3 / default-renderer guarantees covered by
  `runtime/tests/test_default_renderer_guards.py`.
- Renderer work must pass the `docs/ENHANCEMENTS.md` R3 sweep before any
  launcher exposure — the `offer_*` flags exist precisely so a half-validated
  backend cannot reach players.
- Track the campaigns in the central Beads database under the enhancement
  campaign. This document is the design; Beads owns the status.

