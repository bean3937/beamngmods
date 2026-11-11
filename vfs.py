"""Read-only virtual file system over the game folder, vanilla content zips and mods.

Paths are game-style ('vehicles/pickup/x.jbeam'), compared case-insensitively
like BeamNG does on Windows. Later sources override earlier ones for the same path.
Nothing here ever writes to a source.
"""
import json
import os
import zipfile

OUTPUT_NAME = 'engine_swap_catalog.zip'


class Source:
    def __init__(self, kind, name, path, title=None, mount=''):
        self.mount = mount        # game path prefix for folder sources (e.g. 'lua/')
        self.kind = kind          # 'vanilla' | 'mod'
        self.name = name          # short id, e.g. 'pickup.zip' or mod name
        self.path = path
        self.title = title or name
        self.is_zip = os.path.isfile(path)
        self._zip = None
        self.files = {}           # lower path -> real member/relative path

    def open(self):
        if self.is_zip:
            self._zip = zipfile.ZipFile(self.path)
            for n in self._zip.namelist():
                if not n.endswith('/'):
                    self.files[n.replace('\\', '/').lower()] = n
        else:
            for root, _dirs, files in os.walk(self.path):
                for f in files:
                    rel = os.path.relpath(os.path.join(root, f), self.path).replace('\\', '/')
                    self.files[(self.mount + rel).lower()] = rel

    def read(self, lower_path):
        real = self.files[lower_path]
        if self._zip is not None:
            return self._zip.read(real)
        with open(os.path.join(self.path, real), 'rb') as fh:
            return fh.read()

    def real(self, lower_path):
        return self.files[lower_path]

    def __repr__(self):
        return f'Source({self.kind}:{self.name})'


def norm(p):
    return p.replace('\\', '/').lstrip('/').lower()


class VFS:
    def __init__(self):
        self.sources = []
        self.index = {}           # lower path -> Source (winning)
        self.shadowed = []        # (path, loser, winner)

    def add(self, src):
        src.open()
        for p in src.files:
            prev = self.index.get(p)
            if prev is not None and prev.kind == 'mod' and p.endswith('.jbeam'):
                self.shadowed.append((p, prev.name, src.name))
            self.index[p] = src
        self.sources.append(src)

    def exists(self, path):
        return norm(path) in self.index

    def source_of(self, path):
        return self.index.get(norm(path))

    def read(self, path):
        p = norm(path)
        return self.index[p].read(p)

    def real_path(self, path):
        p = norm(path)
        return self.index[p].real(p)

    def files_under(self, prefix, suffix=''):
        prefix = norm(prefix)
        return [p for p in self.index if p.startswith(prefix) and p.endswith(suffix)]


def read_mod_db(mods_dir):
    """mods/db.json -> {lower fullpath: (active, title)}. Missing db -> {}."""
    out = {}
    try:
        with open(os.path.join(mods_dir, 'db.json'), encoding='utf-8') as fh:
            db = json.load(fh)
        for name, m in (db.get('mods') or {}).items():
            fp = (m.get('fullpath') or '').replace('\\', '/').lower()
            md = m.get('modData') if isinstance(m.get('modData'), dict) else {}
            out[fp.rstrip('/')] = (m.get('active', True) is not False, md.get('title') or name)
    except (OSError, ValueError, AttributeError):
        pass
    return out


def discover_mods(mods_dir):
    """Yield (path, rel_game_path) for mod zips and unpacked mod folders.
    Our own output zip is never treated as a source."""
    found = []
    for sub in ('', 'repo'):
        d = os.path.join(mods_dir, sub)
        if os.path.isdir(d):
            for f in sorted(os.listdir(d)):
                if f.lower().endswith('.zip') and f.lower() != OUTPUT_NAME:
                    found.append((os.path.join(d, f), f'/mods/{sub + "/" if sub else ""}{f}'))
    d = os.path.join(mods_dir, 'unpacked')
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if os.path.isdir(os.path.join(d, f)):
                found.append((os.path.join(d, f), f'/mods/unpacked/{f}'))
    return found


def build_vfs(game_dir, mods_dir, log=print, include_inactive=False):
    """Returns (vfs, skipped) where skipped is a list of (name, reason)."""
    vfs, skipped = VFS(), []
    content = os.path.join(game_dir, 'content')
    # loose game files (lua/ etc.) are used only for existence checks
    vfs.add(Source('vanilla', '<game folder lua>', os.path.join(game_dir, 'lua'), mount='lua/'))
    for sub in ('vehicles', ''):
        d = os.path.join(content, sub)
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if f.lower().endswith('.zip'):
                try:
                    vfs.add(Source('vanilla', f, os.path.join(d, f)))
                except (OSError, zipfile.BadZipFile) as e:
                    skipped.append((f, f'unreadable vanilla zip: {e}'))
    db = read_mod_db(mods_dir) if mods_dir else {}
    for path, gp in (discover_mods(mods_dir) if mods_dir and os.path.isdir(mods_dir) else []):
        name = os.path.basename(path)
        active, title = db.get(gp.lower().rstrip('/'), (True, os.path.splitext(name)[0]))
        if not active and not include_inactive:
            skipped.append((name, 'mod is disabled in BeamNG Mod Manager (db.json)'))
            continue
        try:
            src = Source('mod', name, path, title)
            vfs.add(src)
            log(f'  mod: {name} ({len(src.files)} files)')
        except (OSError, zipfile.BadZipFile) as e:
            skipped.append((name, f'unreadable mod archive: {e}'))
    return vfs, skipped

