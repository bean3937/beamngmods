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
- Per-pair exclusion hook: `Builder.pair_exclusions()` returns the donors to hide; the car's Catalog engine slot then
  gets an **allow-list** of the remaining `esc_d_<id>` types. **Never use denyTypes:** BeamNG's parts menu
  (`ui/ui-vue/src/modules/vehicleConfig/components/PartsBranch.vue`, `partsList`) does
  `list = child.suitablePartNames; list.push(...unsuitable)`, mutating the reactive array it is computed from. Any slot
  with unsuitable (denied) parts loops forever and freezes the game when its dropdown opens. Found in game with the
  first engine-model build (pickup slot had 202 denied engines); the validator now rejects any generated denyTypes.
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

## 9. Visible engine models (vanilla cars only)

Observed (game Lua + files, 0.40.5):
- Model files: every vehicle zip and `common.zip` ship `.dae` (Collada XML, readable with the stdlib) next to a
  compiled `.cdae`; 373 vanilla `.dae`, 3.9 GB. Mods ship `.dae` too (Cgoya1 even ships `vehicles/autobello/covetengineonly.dae`,
  i.e. mods copy engine meshes into other cars' folders to make them visible there).
- A car finds meshes in its own folder and `vehicles/common` (resource checker `collectFiles(..., vehAware)`;
  jbeam `meshs.lua` asks the vehicle object for meshes by name: `addFlexmesh(mesh)`). Flexbody `mesh` = Collada `<node name>`.
- Materials: `core/vehicle/manager.lua` loads `*materials.json` from the car's folder and `/vehicles/common/` per spawn.
  A Collada `<material name>` maps to a materials.json entry's `mapTo`; texture paths in those entries are game-absolute
  (`/vehicles/covet/covet_mechanical_b.color.png`). Engine materials of cars with their own engines (covet_mechanical,
  bolide_engine) are only in that car's folder; shared engines (ETK, Gavril V8, Bastion, Ibishu) are in common.
- Collada frame: Z up, metre units; node `<matrix>` scales cm → m. After the node matrix, vertices are in car
  coordinates (ETK 3.0 I6 mesh box y −1.65…−0.95, z 0.25…0.88 matches its engine nodes y −1.60…−0.97, z 0.26…0.87).
- Flexbody rows: `mesh`, `[group]:` (node groups the mesh binds to), options `pos`/`rot`/`scale`.
  `meshs.lua` passes them to `setInitialTransformation(pos, rad(rot), scale)`. A flexbody whose groups have no nodes in
  the vehicle is skipped silently ("flexmesh has no node bindings, ignoring") - that is how shared parts carry meshes
  for several cars (ETK engine part lists `etk800_engbaycrap` bound to `etk800_body` and `etkc_engbaycrap` bound to `etkc_body`).
- Transform semantics, from `jbeam/utils.lua getFlexbodyPosRotAfterNodeRotateOffsetMove` (composes slot nodeRotate/nodeOffset
  with a flexbody's pos/rot exactly like with nodes): mesh vertex v → R·v + pos, rotation about the car origin;
  R = `setRotate(setQuatFromEuler(-rx,-ry,-rz))` (ZXY intrinsic); `LuaVec3:setRotate` rotates by the conjugate quaternion.
  The generator uses ports of these exact Lua functions (no sign guessing). **Assumption:** the C++ side applies the same
  transform the Lua composition implies (the Lua would be inconsistent otherwise). Only rotations about Z are generated.
- Vanilla places shared engines per car with slot options (`etkc_body` → `etk_engine` `nodeOffset {y 0.11, z -0.03}`,
  md_series/us_semi frames → `nodeOffset`/`nodeMove`). Slot options apply to the part and all its children's nodes and
  flexbodies, so donor visuals placed relative to the car's raw engine nodes inherit those offsets automatically.
- Engine visuals per car (6 cars inspected: pickup, etk800, covet, bastion, bolide, sunburst2):
  engine-attached meshes (block, heads, covers, belts, intake, turbo, headers/manifold) bind to the engine node group
  plus intake/turbo/header groups; body-side meshes (downpipe, pipes, muffler, tips, airbox on the body, intercooler,
  radiator hoses, engine-bay clutter) bind to body/exhaust/radiator groups.
  Headers live in different places: separate header slot (pickup `pickup_header_v8`), the intake part (ETK, Sunburst2
  manifold+turbo, Bolide headers under the intake), or a header part that also has the downpipe (Covet).
- Body for the fit check: each car's default-config collision triangles near the engine come from labelled parts:
  hood/hoodscoop, bumper/bumperbar/fascia/grille/valance/radsupport/headlights (front), body (inner panels, firewall),
  fenders, frame/subframe, suspension, dash/interior/windshield/doors, undertray; rear-engine cars: rearbody, tailgate,
  quarter panels. Some node positions are `$variables`/`$=` expressions (evaluated with their defaults).
- `variables.lua`: `$var` / `$=expr` are substituted in nested values (flexbody pos/rot) and in slot options; variables
  are vehicle-global with slot-scoped overrides; `$>>components` only resolves against the root part.

Decisions:
- **Visuals following the selected tuning part: not implemented.** In jbeam a part's visuals cannot depend on what is
  chosen in a sibling slot (the tuning slots and the Engine model slot are siblings under the catalog engine), and the
  shared tuning parts cannot carry car-specific placement. Workarounds exist (hidden per-car variables in shared rows,
  dummy anchor nodes for visibility) but rely on behaviour that cannot be checked without the game.
  The donor model shows the donor's **default** intake/turbo/headers.
- Engine model sub-slot `esc<id>_model` added to every catalog engine (slot name unique per engine, part IDs unchanged):
  `esc_m_<id>` (donor model, per vanilla car, in that car's folder), `esc_m_own` (car's own engine visuals), or empty
  (= "No engine model"). Mod cars have no parts for that slot, so it stays empty and nothing changes for them.
- Car side (vanilla cars): engine-attached visuals of the car's engine, flattened tuning parts and header parts move out of the
  Engine catalog part into `esc_m_own`; header slots get per-car copies without the header meshes (physics unchanged);
  the car keeps downpipe/pipes/muffler/tips and body-side meshes.
- Donor meshes from `vehicles/common` are referenced in place. Meshes that live only in another car's folder are copied
  (with their materials, renamed `esc_…`) into `vehicles/common/engine_swap_catalog/meshes/` - the only way to make them
  visible in another car. Textures are not copied (materials point at the installed textures).
- Placement: crank axis from the engine→transmission direction (snapped to 90°), donor block's
  transmission-side face, lateral centre and bottom aligned with the car's own block.
- Placement anchors on the engines' **physics nodes** (block, transmission side face, lateral centre, bottom), not on
  meshes: first runs showed mesh blocks are inconsistent (the D-Series block mesh has no oil pan, the Bastion's has).
- Fit check: donor engine-attached meshes sampled to points, voxelised (4 cm), tested against the car's body triangles
  (voxelised, triangles within 0.8 m of the engine).
  - Allowed by part: hood/scoop/lids, bumper/fascia/grille/valance/radiator support/lights.
  - Allowed by direction (one "body" part often holds both radiator support and firewall): above the car's engine
    envelope (hood) and ahead of it toward the nearest bumper (grille), within its sides.
  - **Calibration:** space used by any engine the game itself offers for that car (all vanilla engine options, not
    only the default) is free. First run without it rejected the Bastion's own factory 6.5 V8 - collision triangles are
    coarse and real engine meshes pass through them.
  - If blocked, the model is moved up (≤ 12 cm) and/or toward the nearest bumper (≤ 16 cm) in 4 cm steps;
    the translation used for the check is rounded to 4 cm (placement itself is exact).
  - Result on 0.40.5 with the test mods: 31 256 car × engine pairs over 42 vanilla cars - 4 942 fit, 5 833 fit after a
    move, 21 481 don't fit (mostly huge or transverse/longitudinal mismatches in small cars). Every car's own engine
    fits itself at zero offset.
- **Fit tolerance** (GUI / `--fit`): Strict = 4 cm of slack around the space of the car's factory engines (the original
  behaviour), Normal = 8 cm, Loose = 12 cm; the move search grows with it (Normal 16 cm up / 20 cm forward,
  Loose 20 / 24). Every blocked cell knows its distance (in 4 cm layers) from the factory-engine space.
- **Show donor models even if they clip** (`--show-clipping`): one more layer and one more move step than the chosen
  tolerance; offered as "Donor engine model (clips)".
- **Hard size limit** (always, every car): anything a tolerance or "clips" would add is refused if it is bigger than
  1.25× the factory-engine space in width, 1.5× in length (it may poke out of the grille), 1.4× in height (hood),
  or 1.8× in volume. Measured over 31 000 pairs: 90% of engines that fit strictly are ≤ 1.0/1.0/1.08×.
  Engines that fit strictly are never refused by it (some trucks have bays far bigger than their own engines).
- Engines offered with a donor model, all 42 vanilla cars together: Strict 10 733, Normal 17 163, Loose 20 761,
  Loose + clips 23 366; 7 463 car × engine pairs are "too big" in every mode. Covet: 47 / 192 / 245 / 338;
  Pigeon: 40 / 44 / 44 / 44.
- Dedup: identical engines from different folders share one catalog engine; its model comes from the first origin.
- Mod engine packs: their model is used only where the car can already load it (common folder or the car's own
  folder). Copies are made only from vanilla files. 155 donors have no readable block mesh -> car's own model.
- Header slots of vanilla cars inside the Engine catalog are renamed `esc_<slot>` and point at copies without the header
  mesh (an old config's header choice would otherwise not fit and log "wrong slot type"). Non-default header variants
  lose their header *visual* in "Car's own engine model" mode (physics unchanged).
- A copied mesh whose material has no materials.json entry anywhere (3 vanilla lettering/decal materials) keeps its
  original material name - same result as vanilla.

## 7. Known UNKNOWNs (need in-game confirmation)

- Engine models: the game imports **every** .dae in `vehicles/common` at spawn (log: "Importing COLLADA: esc_….dae"
  for all copied files), not only the meshes used; first spawn after a build compiles them.
- Engine models: whether the C++ flexbody transform matches the Lua composition (position/rotation of donor models,
  especially 90° turns); spawn time / memory with copied meshes; how donor models deform in crashes.

- Transmissions: whether auto ↔ manual ↔ DCT ↔ CVT swaps shift correctly with the car's own engine and with catalog engines;
  whether the car's shifter animation (hPattern controller, kept) misbehaves with a different gearbox type.

- Whether the Parts menu stays responsive with ~920 entries in one dropdown.
- Whether flattened car-side tuning geometry (intake/turbo meshes) looks right with an NA donor (it stays visible).
- Whether a donor's tuning variables (sliders) show up correctly in the Tuning menu next to the car's.
- Mod mount order when two mods define the same part name in one folder.
- Behaviour of `deformGroups_turbo` etc. when the donor's turbo deform groups have no beams in the target (expected: turbo never gets damaged).

## 10. Engine visuals architecture 2 - PHASE 1 TEST ONLY (not in the normal catalog)
Built only with `py -3 -m swapcat build --phase1-test --out <folder>` (Bastion + ETK 800, six engines, output
`engine_swap_catalog_phase1.zip`; any `engine_swap_catalog*.zip` is never read as a source). Code: `swapcat/vis2.py`,
`Builder(visuals_v2=...)`, `ModelBuilder.car_parts_v2`, `placement.solve_slot_placement`. Checked in the 0.40.5 Lua:
- slotSystem.lua fillSlots_rec: `slotOptions = tableMerge(parentOptions, slotDef)` is passed down the whole subtree;
  unifyParts inserts it as a row-options entry before every list section (flexbodies too), so slot options reach
  tuning parts any number of levels below.
- utils.lua: `nodeRotate##/nodeOffset##/nodeMove##` (index = 1-2 digits after the name, none = 0), applied per index
  in order rotate, offset, move. **nodeOffset.x is multiplied by sign(pos.x)** (0 for a centred mesh), so placement
  uses nodeRotate9 + nodeMove9 (index 9: after any index a car uses). Ported 1:1 in placement.py, solved numerically.
- variables.lua processParts: slot `variables` are merged over the parent scope and applied to slot options and the
  part; a part's own declaration only sets the global default. tableSchema.lua drops rows whose `disable == true`.
Result of the static build: 0 validator errors; 7 tuning switches change the visible meshes. Awaiting in-game result
(IN_GAME_TEST_PHASE1.md) before Phase 2.

## 11. Engine visuals v2 - PHASE 2 TEST BUILD (every vanilla car; not in the normal catalog)
Built only with `py -3 -m swapcat build --phase2-test --out <folder>` (`--v2-cars a,b` for a quick subset); output
`engine_swap_catalog_phase2.zip`. The normal (v1, confirmed in game) build and the GUI are unchanged. Phase 1 (M1-M4)
was NOT confirmed in game before Phase 2 was built (user's decision). Tests: `IN_GAME_TEST_ENGINE_VISUALS.md`.
Checked in the 0.40.5 Lua (lua/common/jbeam):
- variables.lua processParts: `apply(slotOptions, svars)` resolves `$var` and `$=expr` in slot options (nodeMove etc.)
  before unifyParts uses them; the root scope holds every declared variable (getAllVariables), so Tuning sliders
  declared in the catalog part reach the catalog engine slot. expressionParser.lua: `$x` -> `var_x`, Lua math and
  `case(bool, a, b)` available; a lone `=` is rejected (>=, <= are fine).
- slotSystem.lua: the journal's slot options are deep copies made in fillSlots (raw strings), each resolved with its
  own part's scope; unifyPartJournal merges children into parents post-order: later siblings and deeper parts win.
- utils.lua getNodeRotOffsetMoves needs numbers after variables; two-digit indices (up to 99).
- ui-vue tuningStore.js skips variables with `hideInUI`.
Design (vis2.py docstring has the tree):
- Mode parts set `$esc_vis` (donor model) and `$esc_own` (car's model); native wrappers set `$esc_nat = 1` on the
  catalog engine slot; engines without a model set `$esc_vis = 0` there. Row switches: engine side `vis < 0.5`,
  native-only body rows `vis + nat < 1.5` (original groups; the part's own node groups -> esc_engine_vis), foreign-only
  default headers of the donor's exhaust part `vis - nat < 0.5`, car headers (header copies) `own < 0.5`.
- The car's exhaust-class slots move from the catalog into each catalog-engine part (before the engine slot, so the
  donor still wins dict keys as before). Native: the engine's own exhaust slots (defaults as the engine resolves them
  in this car), only if every beam of that chain reaches an existing node, else the car's exhaust (reported).
- Nested exhaust: an exhaust slot inside a tuning part (Bastion V8: intake -> exhmanifold -> exhaust -> muffler) is
  recorded by analyze_engine as `nested_exhaust` (v1 classification unchanged). Natives bring that chain too. 8
  vanilla cars have such an exhaust in their default config; the catalog flattens it (as v1 does), so there natives
  keep the car's exhaust and only the car's header meshes move to the Engine model parts.
- Car side (B): body-side meshes of the car's engine + default tuning parts (from the default config) move out of the
  catalog: own mode shows all, foreign keeps all but INTAKE_NAME (airbox/filter/intercooler/icpipe/turbo/heatshield,
  list checked against every vanilla car), native hides all (it brings its own), none keeps the non-intake ones.
- C: `placement.place_gearbox`: bellhousing faces aligned as before, sideways/vertical the donor's own gearbox node
  centre onto the car's (same layout only; > 15 cm shift vs block alignment = implausible -> block alignment).
- D/F: `placement.placement_options`: nodeMove = linear expression in the nudge sliders (exact for a fixed rotation),
  turn = nodeRotate<i+1> about the donor's engine centre (pivot px/py/pz), 90 deg engines get a `case($esc_flip...)`
  180 deg alternative about the same centre. H: index = first free after the car's own (9 if none).
- G: validator checks every visible donor mesh's materials against the car folder + common + our materials.json;
  materials no materials.json defines anywhere (vanilla gaps, e.g. `shaded_red`) are counted, not errors.
- Engines whose block mesh is in a tuning part (engine packs) get a block via `block_row(allow_tuning=True)`.
- E (header-downpipe bridge pipe): skipped - the only straight pipe meshes in vehicles/common are cargo loads
  (loads/pipes.dae, 6.3 m x 0.77 m, material `pipes`); no new meshes are created.
Result of the final static build (<work folder>\phase2_test): 42 vanilla cars, 763 native / 18291
foreign / 8294 no-model car x engine entries, 0 placements refused, 6879 gearbox-aligned / 11412 block-aligned;
538 natives with their own exhaust, 155 fallbacks (93 of them in the 8 nested-exhaust cars); validator 0 errors in
4782 pairs + 4527 v2 configurations. Donors without any model: 67 (was 159; 92 fixed by the tuning-part block).
Awaiting the in-game result (IN_GAME_TEST_ENGINE_VISUALS.md).

## 12. PHASE 3 TEST BUILD (joints, exhaust pipes, foreign intake/turbo meshes)
Built only with `py -3 -m swapcat build --phase3-test --out <folder>`; output `engine_swap_catalog_phase3.zip`.
Engine positions are exactly Phase 2's. Code: `swapcat/joints.py`, `ModelBuilder.joint/exhaust`, `RowMaker(induction)`.
Checked in the 0.40.5 Lua:
- meshs.lua processFlexbodies: a flexbody is placed by `setInitialTransformation(pos, rot, scale)` and bound to the
  nodes of its groups; so one unit tube mesh can be reused for every filler through pos/rot/scale.
- utils.lua / meshs.lua: nodeMove/nodeOffset/nodeRotate apply to nodes AND flexbodies (same keys, no flexbody-only
  key); a "visual only" gearbox slide is therefore impossible without copying the transmission part -> not done.
- Texture-free vanilla materials (version 1.5, baseColorFactor/metallicFactor/roughnessFactor, e.g.
  bastion_gauges_break) -> our `esc_filler_metal` in vehicles/common/engine_swap_catalog/meshes/.
Measurement (vanilla = reference):
- gap: the donor block's gearbox-side face (mesh) vs the car's gearbox mesh face. This matched the in-game report
  (BX 5.5 V8 in the Bastion: 6.6 cm gap; ETK 4.4 V8: 1.8 cm overlap = "surprisingly good"). A gap the car's own
  engine also shows (how that gearbox is modelled) counts as normal.
- centring: the donor's OWN gearbox (its home default transmission) face centre, placed with the engine, vs the car's
  gearbox face centre - the crank axis, 0 for every factory engine. (Block-face centres were tried first: factory
  engines came out 20 cm "off", so that was rejected.) Without a home gearbox: block face vs the car's own engine.
- grades: perfect (gap <= 1.5 cm + car's own, off <= 3 cm); filler (open gap <= 30 cm, off <= 20 cm: tube from the
  engine face to the gearbox face, covering the larger face); adapter (touching, 3-20 cm off: 4 cm plate at the
  gearbox face); too far (nothing added).
- exhaust: donor outlet = its own exhaust-chain node nearest its header meshes (per side), car inlet = the car's
  exhaust-chain node nearest the car's header meshes; straight 2.5 cm pipe if 3-35 cm long and not through body cells.
- intake: body-bound induction rows (INTAKE_NAME, not FRONT_MOUNT intercooler/icpipe/chargepipe) that touch the
  engine or the tuning part's own nodes get a foreign twin row; they are added to the fit check for the label
  "(intake clips)" only.
Result of the final static build (<work folder>\phase3_test, 680 MB): validator 0 errors;
foreign joints perfect 1345 / filler 4321 / adapter 6741 / too far 5884; exhaust pipes 1603, meets 587. Not run in
the game yet (IN_GAME_TEST_PHASE3.md).

## 13. PHASE 4 TEST BUILD (generated bellhousings + bent exhaust pipes, Blender cleanup)
Built only with `py -3 -m swapcat build --phase4-test --out <folder>` (options `--shift-max`, `--blender`,
`--previews <folder>`, `--v2-cars`); output `engine_swap_catalog_phase4.zip`. Engine positions are exactly Phase 2/3's.
Code: `swapcat/shapes.py` (bellhousing), `swapcat/pipes.py` (pipes), `swapcat/fitmesh.py` (store, Blender, .dae),
`swapcat/blender/cleanup.py` + `preview.py` (run by Blender), `ModelBuilder.fit_joint / fit_exhaust`, tests
`tests/test_phase4.py`.

Checked (game files / tools, 0.40.5):
- Blender 5.2.3 (`blender -b --factory-startup`): `wm.collada_export` / `wm.collada_import` do not exist any more;
  `wm.obj_export` / `wm.obj_import` do -> Blender reads/writes OBJ, our own writer makes the .dae.
- links.lua resolveGroupLinks: a flexbody's `[group]:` list becomes `_group_nodes` = every node in ANY listed group;
  meshs.lua processFlexbodies binds all of them (`addNodeBinding`). veFlexbodyDebug.lua shows each vertex has its own
  locator nodes (ref/x/y) from that pool. Which nodes a vertex picks is C++ (not visible) - in-game check.
  Vanilla does the same: the car's own gearbox mesh row binds two groups (bastion_transmission: ["bastion_engine",
  "bastion_transmission"]). Our pieces bind esc_engine_vis + the car gearbox row's groups (adapters) / the car exhaust
  row's groups nearest the inlet (pipes).
- A car loads meshes from its own folder and vehicles/common (section 9; resourceUtil.lua collectFiles walks the car
  folder recursively) -> one `vehicles/<car>/esc_fit_<car>.dae` per car (only that car imports it at spawn), materials
  in `vehicles/common/engine_swap_catalog/meshes/esc_fit.materials.json` (texture-free v1.5 PBR, doubleSided).
- No vanilla .dae (400 checked) shares one index offset for VERTEX/NORMAL/TEXCOORD -> ours use offsets 0/1/2 like
  vanilla; positions in mm with a 0.001 node matrix (vanilla uses 0.01 cm matrices).

Why the Phase 3 joints looked wrong (measured in Blender renders of the real meshes):
- the Bastion's gearbox mouth is slanted, so "one face plane" measurements were off; and most open gaps come from the
  fit check's move (e.g. BX 5.5 / ETK 4.4 V8 in the Bastion: moved 12 cm up, 0-8 cm forward) - the donor's crank axis
  was on the car's gearbox axis before that move.
Bellhousing (shapes.plan / bellhousing):
- mouth = outline (convex hull) of the gearbox's front 10 cm; engine back = rearmost engine-part surface (block, sump,
  covers; no headers) inside the mouth moved by the fit-check shift (a hose reaching back beside the gearbox is
  ignored); engine end = the mouth outline moved by that shift, clipped to the engine's rear outline (taper where the
  mouth hangs past the engine).
- 32 outline points; depth per point = exact ray hit on the part's triangles (Surface), probing up to 3 cm inside the
  outline; misses and outliers (> 6 cm from the median: hollows) -> median, then a 5-point circular median (no
  spikes). Each end reaches 1 cm into its part. 'hits' = share of end points sitting exactly on the surface.
- shell (6 mm wall) + flange lips (14 mm) + hex bolts (6-10 per flange) + 5 ribs; Blender: merge, outward normals,
  1.5 mm bevel on edges > 40 deg, triangulate, decimate to <= 2400 tris (limit 3000), weighted normals.
- grades: perfect (largest local gap <= 1.5 cm + the car's own engine's gap), adapter, too far (adapter > 30 cm,
  < 25% of the mouth behind the engine, or fit-check move > shift cutoff), does not meet (an end < 50% on its part),
  not in front (the mouth is not behind the engine at all), factory gap (natives: graded only).
- shift cutoff: chosen by the user from the preview pictures = 15 cm (moves are 4 cm steps: 12 cm still gets one).
- dedupe: geometry rounded to 1 cm (DECIDE: 5 mm made the zip too big; every end overlaps its part 1 cm and wraps
  the mouth by 6 mm, so no seam opens).
Pipes (pipes.py):
- car inlet = the open end of the car's exhaust mesh nearest the car's own header mesh (per side); donor outlet = the
  open end of the donor header mesh nearest that inlet; both with direction and radius measured from the mesh.
- route: cubic Hermite leaving/entering along the pipe directions, else through one via point 6 / 12 cm lower (<= 3
  bends); gaps <= 12 cm get a straight slip joint. Limits: <= 80 cm, bend radius >= 2 x pipe radius, no pipe
  centreline point closer than radius + 1 cm to the car's other meshes near the engine bay (body, frame,
  suspension, gearbox; 1 cm surface samples), ends excluded within 6 cm. Ends reach 1 cm into both pipes.
- 12-sided tube, radius eases from the header's to the car pipe's; flange + 3 bolts at the header, clamp band at the
  car pipe; <= 1200 tris (limit 1500).
Validator: per configuration exactly the fit meshes the build made (none in car/no-model modes), loadable, our
materials, esc_engine_vis among the groups, the donor-model switch; adapters' end hits >= 50%; every esc_fit .dae
parses, triangles within 3000 / 1500, only our materials.
Result of the final static build (<work folder>\phase4_test, 764 MB, cutoff 15 cm): validator 0
errors; 3321 generated meshes in 41 car files (max 2386 / 1176 tris); foreign joints perfect 5142 / adapter 7303 /
too far 2818 / does not meet 1411 / not in front 1617; adapter end hits: mean of the weaker end 71%; pipes in 4618
pairs (would clip 2049, bend too tight 3335, too far/long 2258, no car exhaust 2842, no donor header data 2977).
Not run in the game yet (IN_GAME_TEST_PHASE4.md).

