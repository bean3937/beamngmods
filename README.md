# BeamNG Engine Swap Catalog (version 2.0)

Builds one mod, `engine_swap_catalog.zip`, that adds an **Engine catalog** option to the Engine slot of every
BeamNG.drive car (vanilla and modded). Inside it, a **Catalog engine** slot lists every vanilla engine and every
engine from your engine packs. Each catalog engine brings its own torque curve and engine data, its own engine and
exhaust sound, and its tuning parts (ECU, intake/turbo/supercharger, internals...). The car keeps its own engine
mounts, transmission and body. In vanilla cars the engine model is swapped too (see below).

It also adds a **Transmission catalog** option to every car's Transmission slot. Its **Catalog transmission**
sub-slot lists every vanilla and mod gearbox (clutch/torque converter + gearbox, ratios, shift settings and tuning
parts like converters). The car keeps its transmission body, transfer case and differentials.

In **vanilla cars**, a catalog engine also shows the **donor's engine model** (block, intake, turbo, headers),
placed on the car's gearbox axis. Each catalog engine has an **Engine model** sub-slot: donor model / car's own
model / no model, and visual-only nudge sliders. Engines whose model doesn't fit the car's body (checked against the
car's body panels; poking through the hood or grille is allowed) are hidden in that car unless "Hide engines that
don't fit" is unticked. Mod cars keep their own engine model.

New in 2.0: where a swapped engine doesn't meet the car's gearbox, a **bellhousing adapter** is generated for that
engine and car (shaped by the gearbox mouth and the engine's back, with flanges, bolts and ribs), **bent exhaust
pipes** connect the donor's headers to the car's exhaust, and turbo/intake tuning parts show on swapped engines.
The generated models are cleaned up in Blender when it is installed (optional). Changes: `CHANGELOG.txt`.

Engine swaps and transmission swaps were tested in game on BeamNG.drive **0.40.5.0**. The 2.0 engine models,
adapters and pipes were checked statically (validator, Blender previews) but not yet in game – see
`IN_GAME_TEST_PHASE4.md` in the repository.

## Requirements
- Windows, Python 3.10+ with tkinter (python.org installer, "tcl/tk" option on). No other packages.
- Optional: Blender 4.2+ (tested 5.2), found automatically in `Program Files\Blender Foundation`; run in the
  background to bevel and smooth the generated adapters/pipes. Without it they are built with flat shading.
- Source game files and mods are only read, never changed.

## Use
1. Double-click `START.cmd`.
2. Check **BeamNG game folder** (auto-detected from your Steam libraries) and **Mods folder**
   (auto-detected: `%LOCALAPPDATA%\BeamNG\BeamNG.drive\current\mods`; in game: Pause → Mods → Mod Manager →
   Open Mod Folder). Both are remembered in `%APPDATA%\beamng-swap-catalog\settings.json`.
3. **1. Scan** (about 40 s). The list shows which mods contain engines; untick any you do not want.
   "Also use engines of car mods" adds engines from car mods (Audi R8 etc.) to every car.
4. **2. Build catalog** (about 90 s incl. validation). Writes into the mods folder:
   - `engine_swap_catalog.zip` (≈560 MB, mostly copied mod sounds). BeamNG moves it into `mods\repo\` on first
     start; rebuilds then replace that copy and delete any other copy, so there is never more than one.
   - `engine_swap_catalog.report.json`
5. Start BeamNG, make sure the mod is enabled in the Mod Manager, open Vehicle Config → Parts → Engine →
   **Engine catalog**, then pick an engine under **Catalog engine**.

Rebuild whenever you add or remove mods. Keep the engine packs enabled: CEEP, RK and Dream Engines use their own
engine Lua code, which stays in those mods.

## Command line
```bash
py -m swapcat build
```
```bash
py -m swapcat validate
```
Options: `--game DIR --mods DIR --out DIR --car-mod-engines --no-validate`.

## Tests
```bash
py -m unittest discover -s tests -v
```

## Report (`engine_swap_catalog.report.json`)
- `stats` – counts. `validation` – static check results (`errors` must be empty).
- `targets` – each car: engine slot, base engine, catalog part, default catalog engine, fuel, notes.
- `donors` – each catalog engine: name, where it came from (`origins`), tuning slots, notes.
- `skipped_targets`, `skipped_donors`, `skipped_sources`, `parse_errors` – everything left out, with the reason.

## Layout
| Path | What |
|---|---|
| `gui.py`, `START.cmd` | GUI and launcher |
| `swapcat/jbeam.py` | SJSON/JBeam parser (mirrors the game's reader) |
| `swapcat/vfs.py` | read-only view of game zips, mod zips and unpacked mods |
| `swapcat/index.py`, `tables.py` | part index, slot normalisation, slot-tree resolver |
| `swapcat/analyze.py` | engine detection, car analysis, slot classification |
| `swapcat/build.py` | generator |
| `swapcat/validate.py` | static validator |
| `swapcat/pipeline.py` | scan → build → validate, CLI |
| `NOTES.md` | what was observed in the files and why the design is like this |

| `swapcat/transmission.py` | gearbox stage detection, transmission cars and donors |
| `swapcat/dae.py` | Collada model reader/writer (mesh names, vertices, copying meshes) |
| `swapcat/placement.py` | flexbody transform (ports of the game's Lua), placement maths, voxel fit check |
| `swapcat/visuals.py`, `swapcat/models.py` | engine visuals per car, fit check per pair, Engine model parts |
| `swapcat/vis2.py` | engine visuals v2: Engine model slot, wrappers, switches, sliders, their validator |
| `swapcat/joints.py` | engine–gearbox and header–exhaust measurements |
| `swapcat/shapes.py`, `swapcat/pipes.py` | generated bellhousing adapters and bent exhaust pipes |
| `swapcat/fitmesh.py`, `swapcat/blender/` | generated-model store, Blender cleanup / preview scripts, .dae writer |

CLI options for engine models: `--no-engine-models`, `--show-unfit`, `--blender PATH`, `--shift-max M`. Report keys: `engine_models` (`v2` → per car `joints`, `report_card`, `fit_meshes`).

Report keys for transmissions: `transmission_targets`, `transmissions`, `skipped_transmissions`,
`skipped_transmission_targets`. Per-car exclusions: `Builder.pair_exclusions(car, kind)`.
