"""BeamNG Engine Swap Catalog - tkinter front end."""
import os
import queue
import sys
import threading
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from swapcat import pipeline, settings                     # noqa: E402
from swapcat.analyze import find_donors                     # noqa: E402
from swapcat.transmission import find_transmission_donors   # noqa: E402

STAGES = {'jbeam': 'Reading JBeam files', 'vehicles': 'Analysing vehicles',
          'engines': 'Converting engines', 'catalog': 'Building catalog parts',
          'transmissions': 'Converting transmissions'}


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('BeamNG Engine Swap Catalog')
        self.geometry('900x680')
        self.minsize(700, 500)
        self.q = queue.Queue()
        self.scanned = None
        self.busy = False
        s = settings.load()
        game = s.get('game_dir') or settings.default_game_dir()
        self.game = tk.StringVar(value=game)
        self.mods = tk.StringVar(value=s.get('mods_dir') or settings.detect_mods_dir(game))
        self.carmods = tk.BooleanVar(value=bool(s.get('car_mod_engines')))
        self.models = tk.BooleanVar(value=s.get('engine_models', True))
        self.hide_unfit = tk.BooleanVar(value=s.get('hide_unfit', True))
        self.tolerance = tk.StringVar(value=s.get('fit_tolerance', 'strict'))
        self.clipping = tk.BooleanVar(value=bool(s.get('show_clipping')))
        self.blender = tk.StringVar(value=s.get('blender') or settings.detect_blender())
        self.excluded = set(s.get('excluded_sources') or [])
        self.src_vars = {}
        self._build_ui()
        self.after(100, self._poll)

    # ---------------------------------------------------------------- UI
    def _build_ui(self):
        pad = dict(padx=6, pady=3)
        top = ttk.Frame(self)
        top.pack(fill='x', **pad)
        for row, (label, var, cmd) in enumerate([('BeamNG game folder', self.game, self._pick_game),
                                                 ('Mods folder', self.mods, self._pick_mods)]):
            ttk.Label(top, text=label).grid(row=row, column=0, sticky='w')
            ttk.Entry(top, textvariable=var).grid(row=row, column=1, sticky='we', padx=4)
            ttk.Button(top, text='Browse...', command=cmd).grid(row=row, column=2)
        ttk.Button(top, text='Detect', command=self._detect).grid(row=1, column=3, padx=2)
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text='In BeamNG: Pause > Mods > Mod Manager > Open Mod Folder shows the mods folder.',
                  foreground='#555').grid(row=2, column=1, sticky='w')

        mid = ttk.Frame(self)
        mid.pack(fill='x', **pad)
        self.btn_scan = ttk.Button(mid, text='1. Scan', command=self._scan)
        self.btn_scan.pack(side='left')
        self.btn_build = ttk.Button(mid, text='2. Build catalog', command=self._build, state='disabled')
        self.btn_build.pack(side='left', padx=6)
        ttk.Checkbutton(mid, text='Also use engines/transmissions of car mods as donors', variable=self.carmods,
                        command=self._refresh_sources).pack(side='left', padx=10)
        opt = ttk.Frame(self)
        opt.pack(fill='x', padx=6)
        ttk.Checkbutton(opt, text='Show donor engine models in vanilla cars', variable=self.models).pack(side='left')
        ttk.Checkbutton(opt, text="Hide engines that don't fit (vanilla cars)", variable=self.hide_unfit).pack(side='left', padx=10)
        opt2 = ttk.Frame(self)
        opt2.pack(fill='x', padx=6)
        ttk.Label(opt2, text='Fit tolerance:').pack(side='left')
        ttk.Combobox(opt2, textvariable=self.tolerance, values=['strict', 'normal', 'loose'], state='readonly',
                     width=8).pack(side='left', padx=4)
        ttk.Checkbutton(opt2, text='Show donor models even if they clip a little', variable=self.clipping).pack(side='left', padx=10)
        ttk.Label(opt2, text='(engines far too big for a car are never shown)', foreground='#555').pack(side='left')
        opt3 = ttk.Frame(self)
        opt3.pack(fill='x', padx=6)
        ttk.Label(opt3, text='Blender (optional):').pack(side='left')
        ttk.Entry(opt3, textvariable=self.blender, width=60).pack(side='left', padx=4, fill='x', expand=True)
        ttk.Button(opt3, text='Browse...', command=self._pick_blender).pack(side='left')
        ttk.Label(opt3, text='rounds and smooths the gearbox adapters and pipes (blender.org, free)',
                  foreground='#555').pack(side='left', padx=6)
        ttk.Button(mid, text='Open report', command=self._open_report).pack(side='right')
        ttk.Button(mid, text='Open mods folder', command=lambda: self._open(self.mods.get())).pack(side='right', padx=6)

        self.src_box = ttk.LabelFrame(self, text='Engine sources (untick to leave out of the catalog)')
        self.src_box.pack(fill='x', **pad)
        self.src_canvas = tk.Canvas(self.src_box, height=110, highlightthickness=0)
        sb = ttk.Scrollbar(self.src_box, orient='vertical', command=self.src_canvas.yview)
        self.src_inner = ttk.Frame(self.src_canvas)
        self.src_inner.bind('<Configure>', lambda e: self.src_canvas.configure(scrollregion=self.src_canvas.bbox('all')))
        self.src_canvas.create_window((0, 0), window=self.src_inner, anchor='nw')
        self.src_canvas.configure(yscrollcommand=sb.set)
        self.src_canvas.pack(side='left', fill='x', expand=True)
        sb.pack(side='right', fill='y')
        ttk.Label(self.src_inner, text='Press Scan to list the vanilla game and mods that contain engines.').pack(anchor='w')

        prog = ttk.Frame(self)
        prog.pack(fill='x', **pad)
        self.stage = ttk.Label(prog, text='Idle')
        self.stage.pack(anchor='w')
        self.bar = ttk.Progressbar(prog, mode='determinate', maximum=100)
        self.bar.pack(fill='x')

        self.logw = ScrolledText(self, height=18, state='disabled', font=('Consolas', 9))
        self.logw.pack(fill='both', expand=True, **pad)

    # ---------------------------------------------------------------- helpers
    def _pick_game(self):
        d = filedialog.askdirectory(initialdir=self.game.get(), title='BeamNG.drive game folder')
        if d:
            self.game.set(os.path.normpath(d))
            self.scanned = None

    def _pick_mods(self):
        d = filedialog.askdirectory(initialdir=self.mods.get(), title='BeamNG mods folder')
        if d:
            self.mods.set(os.path.normpath(d))
            self.scanned = None

    def _pick_blender(self):
        f = filedialog.askopenfilename(title='blender.exe', filetypes=[('Blender', 'blender.exe'), ('All', '*.*')])
        if f:
            self.blender.set(os.path.normpath(f))

    def _detect(self):
        self.mods.set(settings.detect_mods_dir(self.game.get()))
        self.scanned = None

    def _open(self, path):
        if path and os.path.exists(path):
            os.startfile(path)        # Windows only, as is BeamNG
        else:
            messagebox.showinfo('Not found', f'{path}\ndoes not exist yet.')

    def _open_report(self):
        self._open(os.path.join(self.mods.get(), 'engine_swap_catalog.report.json'))

    def _save(self):
        settings.save({'game_dir': self.game.get(), 'mods_dir': self.mods.get(),
                       'car_mod_engines': self.carmods.get(),
                       'engine_models': self.models.get(), 'hide_unfit': self.hide_unfit.get(),
                       'fit_tolerance': self.tolerance.get(), 'show_clipping': self.clipping.get(),
                       'blender': self.blender.get(),
                       'excluded_sources': sorted(n for n, v in self.src_vars.items() if not v.get())
                       if self.src_vars else sorted(self.excluded)})

    def log(self, msg):
        self.q.put(('log', msg))

    def progress(self, stage, i, n):
        self.q.put(('progress', (stage, i, n)))

    def _poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == 'log':
                    self.logw.configure(state='normal')
                    self.logw.insert('end', val + '\n')
                    self.logw.see('end')
                    self.logw.configure(state='disabled')
                elif kind == 'progress':
                    stage, i, n = val
                    self.stage.configure(text=f'{STAGES.get(stage, stage)}: {i}/{n}')
                    self.bar['value'] = 100 * i / max(n, 1)
                elif kind == 'done':
                    self._finish(*val)
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _run(self, fn, label):
        if self.busy:
            return
        self.busy = True
        self._save()
        for b in (self.btn_scan, self.btn_build):
            b.configure(state='disabled')
        self.stage.configure(text=label)

        def work():
            try:
                self.q.put(('done', (label, fn(), None)))
            except Exception as e:           # show, never crash the GUI
                self.log(traceback.format_exc())
                self.q.put(('done', (label, None, e)))
        threading.Thread(target=work, daemon=True).start()

    def _finish(self, label, result, err):
        self.busy = False
        self.btn_scan.configure(state='normal')
        if err:
            self.stage.configure(text=f'{label} failed: {err}')
            messagebox.showerror('Error', str(err))
        elif label == 'Scanning':
            self.scanned = result
            self._refresh_sources()
            self.stage.configure(text='Scan complete')
            self.bar['value'] = 100
        else:
            zpath, rpath, R = result
            v = R.get('validation') or {}
            st = R['stats']
            self.stage.configure(text=f"Built {st['catalog_engines']} engines for {st['targets']} vehicles; "
                                      f"validation errors: {len(v.get('errors', []))}")
            self.bar['value'] = 100
            messagebox.showinfo('Done', f'Written:\n{zpath}\n\nReport:\n{rpath}\n\n'
                                        'Restart BeamNG (or reload mods) to load it.')
        if self.scanned:
            self.btn_build.configure(state='normal')

    def _refresh_sources(self):
        for w in self.src_inner.winfo_children():
            w.destroy()
        if not self.scanned:
            return
        vfs, index, _ = self.scanned
        vehicles = index.vehicles()
        counts = {}                     # (kind, name, title) -> [engines, transmissions]
        for i, found in enumerate((find_donors(index, vfs, vehicles, self.carmods.get())[0],
                                   find_transmission_donors(index, vehicles, self.carmods.get())[0])):
            for d in found:
                key = (d.part.source.kind, d.part.source.name, d.part.source.title)
                counts.setdefault(key, [0, 0])[i] += 1
        ve = sum(c[0] for (k, _n, _t), c in counts.items() if k == 'vanilla')
        vt = sum(c[1] for (k, _n, _t), c in counts.items() if k == 'vanilla')
        ttk.Label(self.src_inner, text=f'Vanilla game: {ve} engine parts, {vt} transmission parts (always included)').grid(
            row=0, column=0, columnspan=2, sticky='w')
        self.src_vars = {}
        mods = sorted(((n, t, c) for (k, n, t), c in counts.items() if k == 'mod'), key=lambda x: x[1].lower())
        for i, (name, title, c) in enumerate(mods):
            v = tk.BooleanVar(value=name not in self.excluded)
            self.src_vars[name] = v
            ttk.Checkbutton(self.src_inner, text=f'{title}  ({name}: {c[0]} engines, {c[1]} transmissions)', variable=v).grid(
                row=1 + i // 2, column=i % 2, sticky='w', padx=4)

    # ---------------------------------------------------------------- actions
    def _scan(self):
        game, mods = self.game.get(), self.mods.get()
        self._run(lambda: pipeline.scan(game, mods, log=self.log, progress=self.progress), 'Scanning')

    def _build(self):
        game, mods = self.game.get(), self.mods.get()
        excluded = [n for n, v in self.src_vars.items() if not v.get()]
        scanned, carmods = self.scanned, self.carmods.get()
        models, hide = self.models.get(), self.hide_unfit.get()
        tol, clip = self.tolerance.get(), self.clipping.get()
        # version 2.0: engine models in vanilla cars are built with the Phase 4 visuals (gearbox adapters,
        # exhaust pipes, turbo/intake parts); Blender is optional
        v2 = dict(pipeline.PHASE4, blender=self.blender.get().strip()) if models else None
        if models and not (v2['blender'] and os.path.isfile(v2['blender'])):
            self.log('Blender not found: adapters and pipes are built without rounded edges / smooth shading.')
        self._run(lambda: pipeline.build(game, mods, log=self.log, progress=self.progress,
                                         include_car_mod_engines=carmods, excluded_sources=excluded,
                                         scanned=scanned, engine_models=models, hide_unfit=hide,
                                         fit_tolerance=tol, show_clipping=clip, visuals_v2=v2), 'Building')


if __name__ == '__main__':
    App().mainloop()
