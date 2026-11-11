BeamNG Engine & Transmission Swap Catalog
by bean3937  -  version 2.0  -  built for BeamNG.drive 0.40.5
=============================================================

WHAT IT DOES
------------
A small Windows tool that builds ONE mod for your own game, made from what you already have
installed. After that, in the in-game parts menu you can put:

  - any engine (vanilla or from your engine-pack mods) into any car, and
  - any transmission (vanilla or from your mods) into any car.

A swapped engine brings its own power and torque, its own engine and exhaust sound, and its
own tuning parts (ECU, turbo/supercharger, intake, internals...).
A swapped transmission brings its own gears, clutch / torque converter, shift behaviour,
gearbox sounds and tuning parts (converter, flywheel, gear ratios...).
The car keeps its own body, engine mounts, transfer case and differentials.

NEW IN 2.0 - in vanilla (base game) cars you also SEE the new engine, and it is fitted in:
  - The donor engine's own model (block, intake, turbo, headers) sits on the car's gearbox, on
    the car's engine mounts. Engines that would stick into the cabin, out of the sides or out of
    the bottom are hidden for that car (poking through the hood or the grille is allowed).
  - Where the new engine does not meet the car's gearbox, a bellhousing adapter is generated
    for that exact engine and car: it follows the gearbox mouth and the back of the engine, with
    flanges, bolts and ribs.
  - Bent exhaust pipes are generated from the new engine's headers to the car's exhaust, routed
    around the body, with a flange and a clamp.
  - Turbo kits and intakes you pick in the tuning slots now show on the swapped engine.
  - Every swap has an "Engine model" choice (donor model / car's own model / no model) and
    sliders to nudge the model.
Modded cars get the real engine swap too, but keep showing their own engine model.


WHAT YOU NEED
-------------
1. Windows 10 or 11 with BeamNG.drive.
2. Python 3 (free): https://www.python.org/downloads/windows/
   During setup tick "Add python.exe to PATH" and keep "tcl/tk and IDLE" selected.
   No other packages are needed.
3. Optional: Blender 4.2 or newer (free): https://www.blender.org/download/
   The tool uses it (in the background, no window) to round the edges and smooth the shading of
   the generated adapters and exhaust pipes. Without Blender they are still built, but look
   blockier. Tested with Blender 5.2.


INSTALL
-------
1. Unzip this package anywhere, for example to your Desktop.
   (Do NOT put it in the BeamNG mods folder. This is the tool, not the mod.)
2. Double-click START.cmd. If Python is missing, the window tells you what to install.


BUILD THE MOD (first time, and every time you add/remove mods or BeamNG updates)
---------------------------------------------------------------------------------
1. Close BeamNG (recommended).
2. Start the tool with START.cmd.
3. Check the folders at the top:
     - BeamNG game folder: found automatically from your Steam libraries.
       If it is wrong, click Browse and pick the folder that contains BeamNG.drive.exe.
     - Mods folder: found automatically. In the game you can see it under
       Pause > Mods > Mod Manager > Open Mod Folder.
     - Blender (optional): found automatically in Program Files\Blender Foundation.
       Leave it empty if you don't have Blender.
4. Click "1. Scan" (about a minute). The list shows which of your mods contain engines and
   transmissions. Untick any mod you do not want in the catalog.
   Optional: "Also use engines/transmissions of car mods" also takes them from car mods
   (normally a car mod's engine is only offered in that car).
   "Show donor engine models in vanilla cars" (on by default) shows the swapped engine's model,
   with the gearbox adapters and exhaust pipes.
   "Hide engines that don't fit (vanilla cars)" (on by default): engines whose model would stick
   into the cabin/sides/bottom are not offered in that car. Untick it to offer them anyway, with
   the car's own engine model.
   "Fit tolerance": Strict / Normal / Loose = how far (about 4 / 8 / 12 cm) a new engine may clip
   into the car's inner panels, and how far it may be moved up/forward to fit. Higher = more engines.
   "Show donor models even if they clip a little": also offers engines that just miss the chosen
   tolerance, labelled "(clips)". Engines far too big for a car are never shown, in any setting.
5. Click "2. Build catalog". With engine models on this takes about 30 minutes (every engine is
   measured against every vanilla car and the adapters and pipes are made); with engine models
   off, one to three minutes. When it says Done, the tool has written:
     - engine_swap_catalog.zip          -> your mods folder (or mods\repo\ if BeamNG moved it there)
     - engine_swap_catalog.report.json  -> your mods folder
6. Start BeamNG. In Mod Manager make sure "engine_swap_catalog" is enabled.

Rebuilding always replaces the old catalog; you never end up with two copies.


USE IT IN GAME
--------------
Engine:
  Vehicle Config (Ctrl+E) > Parts > Engine slot > choose "Engine catalog"
  > sub-slots appear > pick any engine under "Catalog engine" > Apply.
  The engine's own tuning parts appear as sub-slots under it.
  In vanilla cars there is also an "Engine model" sub-slot:
    Donor engine model (default)  /  Car's own engine model  /  No engine model.
  If a donor model looks wrong, switch to "Car's own engine model" - the engine itself stays.
  Labels after an engine's name tell you what to expect in that car:
    (moved to fit), (clips slightly), (clips), (intake clips), (no model),
    (doesn't fit, car's own model).
  Tuning menu > "Engine swap catalog": sliders that move / turn the donor model (visual only).

Transmission:
  Vehicle Config > Parts > Transmission slot (on most cars it is under Engine)
  > choose "Transmission catalog" > sub-slot "Catalog transmission" > pick any gearbox.

You can use both at the same time. To go back to normal, pick the car's original engine or
transmission in the same slot. Each catalog starts on the car's own engine/transmission.

Names in the list show where a part comes from, e.g.
  "Vanilla ETK: 3.0L I6 Engine"   or   "Ford Engines: Ford Coyote V8 Engines".
A [folder] tag at the end means several different versions with the same name exist.


UNINSTALL
---------
Delete engine_swap_catalog.zip (mods folder or mods\repo\) and engine_swap_catalog.report.json.
Delete this tool's folder. Settings are in %APPDATA%\beamng-swap-catalog (safe to delete).
Your game files and other mods are never changed by this tool.


KNOWN ISSUES / LIMITATIONS
--------------------------
New in 2.0 (engine models, adapters, pipes):
- Version 2.0's engine visuals have NOT been tested in game yet (see NOT TESTED). Treat them as
  a beta: if something looks wrong, switch that swap's "Engine model" to "Car's own engine model".
- Adapters and pipes are made for the engine's default position. The model sliders move the
  engine but not the adapters or pipes - leave the sliders at 0 if you want them to line up.
- Bellhousing adapters are made-up parts: they look like a bellhousing, not like one made for
  that car. No adapter is made when the fit check had to move the engine more than 15 cm, when
  it would be longer than 30 cm, or when one of its ends would hang in the air. The gap stays
  open in those cases (the report lists them under "joints").
- Each adapter / pipe is bound to both the engine and the car's gearbox (or exhaust), like the
  car's own gearbox model. Whether both ends stay together when the engine rocks is decided by
  the game engine and is not confirmed yet.
- Many swaps get no exhaust pipe: the car has no exhaust near the engine (Bastion, for example),
  the donor has no header data, or a pipe would hit the body, need a too tight bend or be longer
  than 80 cm. The new engine's headers then end in the air, as in 1.x.
- Front-mounted intercoolers and their charge pipes are never shown on a swapped engine, and a
  few turbo heat shields / mesh guards stay invisible in other cars.
- Saved configurations that use a catalog engine in a vanilla car load with the default engine
  once after updating from 1.x (the parts under the Engine catalog changed). Re-pick the engine.
- The generated zip is larger (about 750 MB with many engine packs) and the first spawn of each
  vanilla car is slower (the game reads that car's new adapter/pipe model file).
- Small cars (Pigeon, ATV, Covet...) hide most big engines. With Normal/Loose or "clips", some
  engines will visibly clip into inner panels - that is the trade-off for more choice.
- Modded cars and engines whose model files can't be found keep the car's own engine model.
- With "Car's own engine model", the car shows its default header visual even if you picked a
  different header part.

From 1.x (unchanged):
- Keep the engine packs enabled. Some packs (for example CEEP, RK's engines, Dream Engines)
  run their own engine code that stays inside their mod.
- The parts list is long (often several hundred entries), sorted by name and source.
- A diesel engine in a petrol car runs on the car's tank (the car keeps its own fuel system).
- The car's own exhaust still slightly changes the power of a swapped engine.
- Turbo/supercharger damage may never trigger on a swapped engine.
- The shifter lever animation inside the car stays the car's own.
- Some engines/transmissions are left out on purpose because the source mod is broken
  (missing sound files, wrong parts, missing gear ratios). The report lists every one with
  the reason (see below).
- Saved configurations that use a catalog part go back to the default part if you rebuild after
  a source mod changed or was removed. Re-pick it in the parts menu.
- Cars without a combustion engine (electric cars, trailers, props) get no engine catalog.
- The catalog must be rebuilt after a BeamNG update or after adding/removing mods.


NOT TESTED
----------
Tested in game by the author: engine swaps and transmission swaps (vanilla and mod parts, in
vanilla and mod cars, engine + transmission together) on BeamNG.drive 0.40.5 with Python 3.15;
an earlier version of the donor engine models (version 2.0 beta) in a few vanilla cars.
Checked by the tool's own validator (0 errors in all 47 cars) and Blender preview pictures,
but NOT yet run in game:
- the 2.0 gearbox adapters, exhaust pipes and foreign turbo/intake parts,
- the 2.0 engine models in most car / engine combinations.
Also not tested:
- Other BeamNG versions (newer game updates may change the part format).
- Python versions other than 3.15 (3.10 or newer should work); Blender versions other than 5.2.
- Career mode (part shop / inventory) and BeamMP multiplayer.
- Non-Steam installs (should work: pick the game folder with Browse).
- Mods other than the ones the author had installed.


IF SOMETHING GOES WRONG
-----------------------
- The tool's window shows a log. Errors are shown there and in a popup.
- engine_swap_catalog.report.json (in your mods folder) explains, for every car, engine and
  transmission, what was used, what was skipped and why. "validation" -> "errors" should be [].
  Engine models: "engine_models" -> "v2" -> car -> "joints" says, per engine, whether it got an
  adapter / pipes and why not.
- Game problems: send BeamNG's log right after the problem, before restarting:
  %LOCALAPPDATA%\BeamNG\BeamNG.drive\current\beamng.log
  together with the report, the car, and the engine/transmission you picked.


FOR ADVANCED USERS
------------------
Command line (from the tool folder):   py -m swapcat build      py -m swapcat validate
Options: --game DIR  --mods DIR  --out DIR  --car-mod-engines  --no-validate  --no-engine-models
         --fit strict|normal|loose  --show-unfit  --show-clipping  --blender PATH  --shift-max M
Tests:                                  py -m unittest discover -s tests
Technical notes: NOTES.md and README.md. Version history: CHANGELOG.txt.


RIGHTS AND POLICY
-----------------
This tool is an independent work by bean3937. Its code was written from scratch; no code,
models, sounds or other content from BeamNG GmbH or any mod author is included in this package.
The tool is released under the MIT License (see LICENSE.txt).

IMPORTANT: the catalog mod the tool BUILDS on your PC (engine_swap_catalog.zip) is made from
your own installed game and mods. It contains copies of their engine and transmission data and
sound files, which belong to BeamNG GmbH and to the respective mod authors. It is for your
personal use only. Do not upload or share engine_swap_catalog.zip; share this tool instead,
so everyone builds their own from what they have installed.

This project is not affiliated with or endorsed by BeamNG GmbH. "BeamNG" and "BeamNG.drive"
are trademarks of BeamNG GmbH. All mod names mentioned belong to their authors.
Use at your own risk; the tool only reads your game and mod files and writes its own two files.
