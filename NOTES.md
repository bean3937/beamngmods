# NOTES – observed facts and design decisions

Everything below was observed on the developer's test setup (BeamNG.drive 0.40.5, November 2026)
unless marked **UNKNOWN**. Sources: the game files, the game's own Lua source (shipped readable in `<game>/lua`),
a set of popular public mods, and documentation.beamng.com.

## 0. Environment

| Item | Observed |
|---|---|
| Game folder | Steam library `steamapps\common\BeamNG.drive` (auto-detected from Steam's `libraryfolders.vdf`) |
| Game version | **0.40.5.0** (`integrity.json` → `"version": "0.40.5.0"`; also `%LOCALAPPDATA%\BeamNG\BeamNG.drive.ini`) |
| User folder | `%LOCALAPPDATA%\BeamNG\BeamNG.drive\current\` (0.38+ uses `current`, not `<version>`; older `2026-..__update_to_v0.xx` folders are backups). `startup.ini` `UserPath =` is empty, so no override. |
| Mods folder | `...\current\mods\` → mods live in `mods\repo\*.zip` (repo downloads), `mods\unpacked\<name>\` (folders), `mods\multiplayer\` (BeamMP). `mods\db.json` has `active` per mod. |
| Log file | `...\current\beamng.log` (+ rotated `beamng.1.log` ...) |
| Python | tested with 3.15 via the `py` launcher; `python` can be the Microsoft Store stub (START.cmd handles this). |

Mods disabled in db.json are skipped; the `mods\multiplayer\` folder (BeamMP) is not scanned.

## 1. Where vanilla vehicles live

- `content/vehicles/<model>.zip` (127 zips). Each contains `vehicles/<model>/...` (jbeam, `.pc` configs, `info.json`).
- `content/vehicles/common.zip` (4.3 GB) contains `vehicles/common/...`. Engines shared across vehicles live there,
  e.g. `vehicles/common/etk/etk_engine_i6_3.0_petrol.jbeam`, `vehicles/common/pickup/pickup_engine_v8_4.5.jbeam`,
  `vehicles/common/engines/bastion/bastion_engine_v8_large.jbeam`.
- Sounds: `content/art_sound.zip` → `art/sound/blends/*.sfxBlend2D.json` (118 vanilla blends). Vanilla blends only
  name an FMOD event (`"eventName": "event:>Engine>Bands>Synth>i6_eng", "samples": []`); FMOD banks are in `content/audio.zip`.
- Parsing all vanilla jbeam: 105 MB, 21 336 parts, 0 parse failures with our parser.

## 2. How the game finds parts (from `lua/common/jbeam/*.lua`, version 0.40.5)

- `loader.lua`: a vehicle loads parts from `{vehicleDir, '/vehicles/common/'}` – **only its own folder and common**.
- `io.lua`: the namespace of a part is `string.match(filename, "(/vehicles/[^/]*/).*$")`. `getPart` searches the
  vehicle dir first, then common. Same part name twice in one namespace → error "Duplicate part found", last loaded wins.
- `io.lua processSlotsDestructive`: legacy `slots` (`["type","default","description"]`, optional options dict) are converted
  to `slots2` (`name = type`, `allowTypes = {type}`, `denyTypes = {}`). A missing header row is added with a warning.
- `slotSystem.lua fillSlots_rec`:
  - slot id = `slotDef.name or slotDef.type`; slot path = parent path .. id .. `/`.
  - the user's choice is looked up **by slot id under the parent's node** (`partsTree.children[slotId]`) or, for old
    `.pc` files, in the flat `parts[slotId]` map, then by path.
  - a chosen part with the wrong slot type → error + default. **A default is loaded without a type check.**
  - `unifyJournal` is appended *after* recursing, so children are merged into their parent first, then the parent into
    its parent. `unifyParts`: dict sections merge key by key and the **child's value overwrites** the parent's
    (except `$+key`/`$*key`/`$<`/`$>` merge modifiers); list sections append rows.
- `ge/extensions/core/vehicle/partmgmt.lua setPartsTreeConfig` just merges the tree sent by the UI. So when a parent
  part changes, stale child entries may stay in the config, but they are only used if the new part has a slot
  **with the same id**. → **Decision:** every catalog engine's tuning slots get ids unique to that engine
  (`esc<id6>_<orig>`), so the previous engine's tuning choices can never apply.
- File format (`lua/common/json.lua`, `ljson.lua`): SJSON. Root braces optional, `=` or `:`, bare keys, commas optional,
  comments. `null` deletes a key. Ford Engine Pack files have no root braces; RK uses bare keys. Our parser mirrors this.
- Official docs (documentation.beamng.com, "slots2"): header `["name","allowTypes","denyTypes","default","description"]`,
  supported since 0.31; options `coreSlot`, `variables`, `maxRecursion`, `nodeOffset/nodeMove/nodeRotate`.
  Docs say denyTypes only applies to parts whose slotType is a table; io.lua also checks it for strings.
  Our generated parts always use table slotTypes, so both readings agree.
- Docs ("variables"): header `name,type,unit,category,default,min,max,title,description`; `$=` expressions.
  **UNKNOWN** from docs: undeclared/duplicate variable behaviour. → Decision: never generate an undeclared reference;
  donors using undeclared variables are skipped.

## 3. Engine definitions (5+ vehicles inspected)

Every vanilla engine part (153) has a powertrain row `["combustionEngine","mainEngine","dummy",0]` and sections
`mainEngine`, `soundConfig`, `soundConfigExhaust`, `vehicleController`, usually nodes/beams/flexbodies.

| Car | Engine slot (path) | Base engine (default .pc) | Format | Child slots |
|---|---|---|---|---|
| pickup (Gavril D-Series) | `pickup_engine` (`/pickup_frame/pickup_engine/`) | `pickup_engine_v8_4.5` (common/pickup) | slots | enginemounts, oilpan_v8, intake_v8, header_v8, engine_v8_ecu, engine_v8_internals, transmission, n2o_system |
| etk800 | `etk_engine` (`/etk800_body/etk_engine/`) | `etk_engine_i4_2.0_diesel` (common/etk) | slots | enginemounts, oilpan, intake (turbo inside), exhaust, ecu, internals, transmission, n2o |
| covet | `covet_engine` | `covet_engine_sohc` (vanilla name, **overridden by Cgoya1 mod file** `covet_engine_dualtest.jbeam`) | slots | intake, oilpan, internals, ecu, transmission, mounts, n2o |
| bastion | `bastion_engine` | `bastion_engine_v6` (common/engines/bastion) | slots | mounts, oilpan, v6_intake (intake+exhaust+supercharger), v6_ecu, internals, transmission, n2o |
| bolide | `bolide_engine` (`/bolide_chassis/bolide_subframe_R/bolide_engine/`, nested) | `bolide_engine_3.5_v8` | **slots2** | mounts, intake, ecu, oilpan, internals, transaxle, n2o |
| sunburst2 | `sunburst2_engine` | `sunburst2_engine_2_5` | **slots2** | intake, internals, oilpan, mounts, ecu, transaxle, n2o |
| us_semi | `us_semi_engine` | `us_semi_engine_I6` | **slots2** | turbo, ecu, internals, transmission, mounts, oilpan, hydraulic_pump, air_compressor; engine has a `controller` section |

- Slot type names differ per car; 117 vanilla engines use `slots2`, 36 use `slots`.
- The transmission is a **child of the engine**. Its powertrain rows take `inputName: "mainEngine"`.
- Tuning children: the ECU sets `mainEngine` keys (rev limiter) and often `turbocharger`; the intake holds the turbo
  or supercharger data plus geometry; internals hold torque modifiers and damage limits; the exhaust holds
  `torqueModExhaust` + `soundConfigExhaust` tweaks + the visible exhaust geometry.
- `mainEngine` keys that reference the *car*: `torqueReactionNodes:` (node names), `waterDamage/radiator/engineBlock`
  (`{"[engineGroup]:": [...]}` node groups), `breakTriggerBeam`, `energyStorage` (tank names), `deformGroups*`,
  `oilpanNodes:`, radiator-fan keys, `numberOfOutputPorts`. Everything else is engine performance.
- Fuel: `combustionEngine.lua` only registers storages whose `energyType == requiredEnergyType` (default gasoline).
  Tanks are separate car parts (e.g. pickup has gasoline and diesel tank variants).
- Expressions such as `"$=include('vehicles/common/engines/v8_classic/....csv')"` load external files (`expressionParser.lua`).
- Controllers: `controller.lua` loads `controller/<fileName>`; some live in sub-folders and are relocated at runtime
  (e.g. `controller/braking/compressionBrake.lua`, `controller/vehicleController/vehicleController.lua`).

## 4. Sounds

- `combustionEngine.lua`: `samplePath = (soundConfig.sampleFolder or "art/sound/blends/") .. sampleName .. ".sfxBlend2D.json"`;
  the section name comes from `mainEngine.soundConfig` / `mainEngine.soundConfigExhaust`. Lookup is case-insensitive
  in practice (vanilla mixes `.sfxBlend2D` / `.sfxBlend2d`).
- Mod blends list sample files: `["art/sound/engine/AluminatorV8/EXH_803.wav", 803]` (Ford pack: 52 blends, 3342 wavs).
- Starter/shutoff/afterfire/turbo sounds are FMOD `event:>...` strings (not files) in vanilla.
- **Decision:** vanilla blends are referenced as-is (always installed). Mod blends + samples are copied once into
  `art/sound/esc/<mod>/...` and `sampleFolder` points there; identical files from several mods are stored once (SHA-1).
- Mod bugs found (donor skipped, reported): Ford Kent Crossflow `sampleName` typo `KentCroKentCrossflowI4`;
  DEP_lite SR20DET blends absent; DEP RB25 missing `1700o.wav`; KLJP SR-TEC missing a sample; UPP 5.9L references
  a blend that is not installed; one Cgoya1 V8 blend missing.

## 5. Mods examined (public mods on the test setup)

Engine-providing mods examined (engine parts outside a car mod's own vehicle folder):
Ford Engines (`Ford_Engine_Pack_JITTERUSA.zip`, 229 donor engines), CEEP (`classic_engine_expansion_pack.zip`, 105),
RK's Highly Customizable Engines (`highly_customizable_engines_rk.zip`, 840 parts → 95 distinct),
Dream Engines Pack (`DEP_lite.zip`), D-Series engines pack (`engines_pack.zip`), Gavril 6cyl diesel,
plus config/parts packs that add engines to vanilla cars: CJD Specials, UPP, Cgoya1, KLJP, HighPowerCarPack, DTS.
**Decision:** all of these donate by default; the GUI lists them with tick boxes so they can be excluded.
Engines of car mods (Audi R8, FR17, Model 1900, KLJP's own cars) are offered only in their own car (see Design)
unless "use car-mod engines" is ticked.

How mods differ from vanilla:
- Engine packs drop engine parts into **vanilla vehicle folders** (`vehicles/pickup/...`, `vehicles/bluebuck/...`) and
  `vehicles/common/...`, so they are visible only to those cars. Ford also ships engines for car mods that are not installed (`vehicles/65stang/`).
- Custom engine devices: CEEP `classic_combustionEngine` (+ its own turbo/supercharger lua), RK `combustionEngine_rk`,
  DEP `combustionEngine_fixed`. These lua files live in the mod → **the donor mod must stay enabled** for those engines.
- Car mods made with Automation/Camso (Audi R8, Model 1900) use their own slot naming (`Camso_Engine`) and hashed names.
- Some packs override vanilla files/parts (Cgoya1 redefines `covet_engine_sohc`; 427 same-name parts in one folder across sources). Which copy the game uses is load-order dependent (**UNKNOWN** mod mount order); we take the alphabetically last source.

### RK's Highly Customizable Engines (checked in files)
- After implementing SJSON (bare keys like `maxOverRevDamage: 1000000`) all RK files parse.
- RK engine parts use the vehicle's real engine slot type (e.g. `covet_engine`, `pickup_engine`), so they do load.
- Breakage on 0.40: RK engine parts reference **vehicle parts that no longer exist** (`bolide_transaxle_6M_race`,
  `nine_exhaust_v8_flathead`, `sunburst_*` – the old Sunburst folder has no vehicle any more, `us_semi_exhaust`,
  `vivace_exhaust_i5_2.5_petrol`, `hatch_*`, `lansdale_radiator_v10`). Those are car-side slots that the catalog
  replaces with the target car's own, so they do not affect the catalog.
- RK's own I6: 8 child slots default to **I5 parts with a different slot type** (`engine_i5_cylinderhead_st_RK` in slot
  `engine_i6_cylinderhead_RK`, ...). The game loads such defaults but they cannot be re-selected. **Skipped and reported, not fixed.**
- RK V12 exhaust header default `engine_V12_exhaustheader_st_RK` does not exist (slot left empty, as in game).

Other donors skipped for the same reason (wrong-type defaults): 7 Cgoya1 engines, 1 engines_pack diesel I6. A wrong-type
default inside an *optional* choice only removes that choice (CEEP supercharger filter option); it no longer skips the engine.

## 6. Design (what the generator does)

```
<car engine slot>      -> "Engine catalog"  esc_catalog_<car>   (vehicles/<car>/esc_catalog_<car>.jbeam)
   car-side child slots kept as they are: mounts, oil pan, exhaust/header, radiator, transmission, n2o, pure visuals
   car's own tuning parts (intake/ECU/internals) flattened to geometry only (nodes, beams, flexbodies...)
   esc_engine slot        -> catalog engine esc_e_<hash>       (vehicles/common/engine_swap_catalog/esc_engines_NN.jbeam)
      esc<id>_<slot>       -> donor tuning part esc_t_<hash>   (.../esc_tuning_NN.jbeam), recursively ('esc_' prefixed ids)
```
- **Catalog part** = deep copy of the car's base engine (default config) minus donor-owned data: performance
  `mainEngine` keys, sound sections, `vehicleController`, the combustion powertrain row, non-vehicleController
  controllers. It keeps the car's interface keys (§3), physics, visuals, and car-side slots. The `esc_engine` slot is last,
  so the donor's values win over sibling parts (child-overwrites rule, §2).
- **Catalog engine** = donor's `mainEngine` minus interface/node keys, its sound sections (+ any section named by a
  mainEngine string: turbocharger, supercharger, ...), `vehicleController`, engine-type controllers
  (`powertrainControl/*`, `compressionBrake`, `twoStepLaunch`, `nitrousOxideInjection`), variables, its combustion
  row (custom device type kept), and its tuning slots.
- Slot classification (by slot name/description and candidate contents): drivetrain (has non-engine powertrain
  devices), exhaust/header/muffler, mounts, oil pan, radiator, n2o (`n2o_system`), tuning (has performance/sound
  sections somewhere in its subtree), visual (rest). Only *tuning* travels with an engine.
- Dedup: content hash of the converted engine/tuning part. Identical engines (e.g. one RK engine copied into 40
  vehicle folders) are stored once; a tuning part used by many engines is one part with a list `slotType`.
  Same display name but different data → name gets `[folder]` suffix.
- Each car's own engine is always the default "Catalog engine" of its own catalog. If that engine is not a donor
  (car mods), it is added with a car-only slot type `esc_own_<car>`.
- Fuel: interface keys incl. `requiredEnergyType` and `energyStorage` come from the car, so a diesel donor in a petrol
  car runs on the car's tank (§3 shows a mismatch would starve the engine). Reported per car (`fuel`).
- Per-pair exclusion hook: `Builder.pair_exclusions()` returns donor ids for the slot's `denyTypes`.
  No static incompatibility was found (all interface data is the car's), so it is empty. Transmissions can plug in the same way.
- Skipped cars: no combustion engine in default config or any slot (props, trailers, electric), `simple_traffic` (AI proxy).
- Skipped donors: wrong-type tuning defaults, no torque curve, missing sound files, undeclared variables.

## 8. Transmissions (added after the engine catalog passed in-game testing)

Observed (game Lua + all 47 cars' default configs):
- Every car's transmission part holds one *stage*: coupling device(s) fed by `mainEngine`
  (`frictionClutch:clutch`, `torqueConverter:torqueConverter`, `centrifugalClutch:clutch`; none for DCTs)
  → one gearbox device named exactly **`gearbox`** (manual/automatic/dct/cvt/sequential).
- What reads from it: torsionReactor, shaft/differential/splitShaft "transfercase", or rangeBox — always `gearbox` output 1,
  except parts that put more devices after the gearbox in the same part (`us_semi`/`md_series` rangeBox,
  `percheron` splitter+shaft, `wl40` shaft+transfercase, `atv` diffs).
- `vehicleController.lua` does `powertrain.getDevice("gearbox")` and loads `shiftLogic/<gearbox type>`; shift RPM arrays of
  the wrong length are padded, so a different gear count is handled.
- `powertrain.lua`: `tableMergeRecursive(jbeamData, v.data[jbeamData.name])` → a device's config is the section named
  after the device (`gearbox`, `clutch`, `torqueConverter`).
- `gearbox` sections hold car node references (`"gearboxNode:": "tra1"`).
- Child slots: flywheel (`clutch` section), converter (`torqueConverter`), valve body / RK ratio parts (`gearbox`,
  `vehicleController`) are tuning; transfer case / differential slots have powertrain devices (car side).
- CVT ratios are `minGearRatio`/`maxGearRatio`, often in a child part (UTV `utv_clutch_*`).
- In vanilla the transmission is a **child of the engine part**, so its dict keys overwrite the engine's.

Design:
```
<car transmission slot>  -> "Transmission catalog" esc_tcatalog_<car>  (vehicles/<car>/esc_tcatalog_<car>.jbeam)
   car body of the gearbox: nodes, beams, flexbodies, gearbox node refs, devices after the gearbox,
   transfer case / differential slots (kept, after the catalog slot), car tuning parts flattened to geometry
   esc_transmission slot   -> catalog transmission esc_x_<hash>  (.../esc_transmissions_NN.jbeam)
      esx<id>_<slot>        -> tuning (converter, flywheel, ratios...) esc_t_<hash>
```
- Donor carries: the stage rows, sections named after its stage devices + `gearbox`/`clutch`/`torqueConverter`,
  `vehicleController`, variables. Node refs stripped; the car's are kept in the catalog part.
- The Transmission catalog exists in the car's transmission slot, so it works with the car's own engine and,
  where the transmission slot is a child of the engine (all vanilla cars), under the Engine catalog too
  (the engine catalog keeps that slot by name).
- **Change to the engine catalog:** its `esc_engine` slot now comes *before* the drivetrain slots, so the transmission
  keeps overriding the engine's `vehicleController` keys as in vanilla (before, the donor engine won).
- Donors skipped (reported): stage not inside one part (vanilla Vivace manuals keep the clutch in a child part),
  extra device branching off engine/coupling, gearbox not named `gearbox`, no gear ratios in part + default children,
  missing whine sound files (DEP typos like `5Wh5ine97.wav`), undeclared variables, mistyped child defaults.
  An option that adds its own powertrain devices to a tuning slot (CJD UTV converter clutch) is left out of that slot.
- Cars skipped: `large_roller` (engine and gearbox in one part). 46 of 47 engine-catalog cars get a transmission catalog.

## 7. Known UNKNOWNs (need in-game confirmation)

- Transmissions: whether auto ↔ manual ↔ DCT ↔ CVT swaps shift correctly with the car's own engine and with catalog engines;
  whether the car's shifter animation (hPattern controller, kept) misbehaves with a different gearbox type.

- Whether the Parts menu stays responsive with ~920 entries in one dropdown.
- Whether flattened car-side tuning geometry (intake/turbo meshes) looks right with an NA donor (it stays visible).
- Whether a donor's tuning variables (sliders) show up correctly in the Tuning menu next to the car's.
- Mod mount order when two mods define the same part name in one folder.
- Behaviour of `deformGroups_turbo` etc. when the donor's turbo deform groups have no beams in the target (expected: turbo never gets damaged).
