"""English display names: BeamNG stores many names as translation keys
(info.json "Name": "vehiclesData.pickup.Name", part names as {"txt": key, "ctx": {...}})."""
import os
import re

from . import jbeam

_PH = re.compile(r'\{\{\s*([A-Za-z0-9_]+)\s*(\|\s*translate\s*)?\}\}')


class Translator:
    def __init__(self, game_dir=None, vfs=None):
        self.t = {}
        d = os.path.join(game_dir or '', 'locales', 'translations', 'en-US')
        if game_dir and os.path.isdir(d):
            for f in sorted(os.listdir(d)):
                if f.endswith('.json'):
                    self._load(open(os.path.join(d, f), 'rb').read())
        for p in (vfs.index if vfs else ()):
            if p.startswith('locales/') and 'en-us' in p and p.endswith('.json'):
                self._load(vfs.read(p))

    def _load(self, raw):
        try:
            data = jbeam.loads(raw.decode('utf-8', 'replace'))
        except jbeam.JBeamError:
            return
        if isinstance(data, dict):
            self.t.update({k: v for k, v in data.items() if isinstance(v, str)})

    def __call__(self, x):
        if isinstance(x, dict):
            txt = self.t.get(x.get('txt'), x.get('txt') or '')
            ctx = x.get('ctx') or {}

            def sub(m):
                v = ctx.get(m.group(1), '')
                return self.t.get(v, v) if m.group(2) else str(v)
            return _PH.sub(sub, str(txt))
        if isinstance(x, str):
            return self.t.get(x, x)
        return '' if x is None else str(x)
