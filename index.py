"""Part index + slot resolver, following lua/common/jbeam/io.lua and slotSystem.lua.

Namespace = '/vehicles/<dir>/' taken from the file path. A vehicle sees parts from
its own namespace first, then '/vehicles/common/' (loader.lua: {vehicleDir, '/vehicles/common/'}).
"""
import json
import re

from . import jbeam
from .tables import part_slots, part_fits, slot_types

COMMON = '/vehicles/common/'
_NS = re.compile(r'^vehicles/([^/]+)/')


class Part:
    __slots__ = ('name', 'data', 'file', 'source', 'ns')

    def __init__(self, name, data, file, source, ns):
        self.name, self.data, self.file, self.source, self.ns = name, data, file, source, ns

    @property
    def slots(self):
        return part_slots(self.data)

    def __repr__(self):
        return f'Part({self.name} @ {self.file} [{self.source.name}])'


def namespace_of(path):
    m = _NS.match(path)
    return f'/vehicles/{m.group(1)}/' if m else None


class PartIndex:
    def __init__(self):
        self.ns = {}                 # ns -> {name: Part}
        self.by_type = {}            # ns -> {slotType: [names]}
        self.errors = []             # (file, source, message)
        self.duplicates = []         # (ns, name, file_kept, file_lost)

    def add_part(self, ns, name, data, file, source):
        bucket = self.ns.setdefault(ns, {})
        if name in bucket:
            self.duplicates.append((ns, name, file, bucket[name].file))
            for t in slot_types(bucket[name].data):
                self.by_type[ns][t].remove(name)
        bucket[name] = Part(name, data, file, source, ns)
        for t in slot_types(data):
            self.by_type.setdefault(ns, {}).setdefault(t, []).append(name)

    def load_from_vfs(self, vfs, progress=None):
        files = sorted(p for p in vfs.index if p.startswith('vehicles/') and p.endswith('.jbeam'))
        for i, p in enumerate(files):
            if progress and i % 200 == 0:
                progress(i, len(files))
            src = vfs.index[p]
            # shortcut: namespaces compared lower-case; the game matches the real path case
            # (vehicle dir names are lower-case in practice). Upgrade if a mod uses mixed case.
            ns = namespace_of(p)
            try:
                doc = jbeam.loads(src.read(p).decode('utf-8', 'replace'))
            except (jbeam.JBeamError, RecursionError, IndexError) as e:
                self.errors.append((p, src.name, f'JBeam parse error: {e}'))
                continue
            if not isinstance(doc, dict):
                self.errors.append((p, src.name, 'root is not an object'))
                continue
            for name, part in doc.items():
                if not isinstance(part, dict):
                    continue
                if not slot_types(part):
                    self.errors.append((p, src.name, f'part {name} has no slotType (game ignores it)'))
                    continue
                if not isinstance(part.get('information', {}), dict):
                    part['information'] = {}
                self.add_part(ns, name, part, p, src)
        if progress:
            progress(len(files), len(files))

    # --- lookups -------------------------------------------------------
    def dirs(self, ns):
        return [ns, COMMON] if ns != COMMON else [COMMON]

    def get(self, ns, name):
        for d in self.dirs(ns):
            p = self.ns.get(d, {}).get(name)
            if p is not None:
                return p
        return None

    def candidates(self, ns, slot):
        """Parts that fit the slot as seen from vehicle namespace ns."""
        seen, out = set(), []
        for d in self.dirs(ns):
            bt = self.by_type.get(d, {})
            for t in slot['allowTypes']:
                for name in bt.get(t, ()):
                    if name in seen:
                        continue
                    p = self.get(ns, name)          # vehicle dir wins on name clash
                    if p is not None and part_fits(p.data, slot):
                        seen.add(name)
                        out.append(p)
        return out

    def vehicles(self):
        """Namespaces that define a 'main' part -> list of main parts."""
        res = {}
        for ns, parts in self.ns.items():
            if ns == COMMON:
                continue
            mains = [p for p in parts.values() if 'main' in slot_types(p.data)]
            if mains:
                res[ns] = mains
        return res


def read_pc(vfs, path):
    """Load a .pc config (strict-ish JSON). Returns dict or None."""
    try:
        return jbeam.loads(vfs.read(path).decode('utf-8', 'replace'))
    except (KeyError, jbeam.JBeamError, ValueError):
        return None


def read_info(vfs, ns):
    for name in ('info.json',):
        p = ns.strip('/') + '/' + name
        if vfs.exists(p):
            try:
                return jbeam.loads(vfs.read(p).decode('utf-8', 'replace'))
            except (jbeam.JBeamError, ValueError):
                return None
    return None


class Choice:
    """User choices for slot resolution: by slot path, then by slot name (old flat .pc)."""

    def __init__(self, pc=None, overrides=None):
        self.flat, self.tree = {}, None
        self.overrides = overrides or {}       # slot path -> part name ('' = empty)
        if isinstance(pc, dict):
            if isinstance(pc.get('parts'), dict):
                self.flat = pc['parts']
            elif isinstance(pc.get('partsTree'), dict):
                self.tree = pc['partsTree']

    def pick(self, path, slot_name, tree_node):
        if path in self.overrides:
            return self.overrides[path], None
        if self.tree is not None:
            child = (tree_node or {}).get('children', {}).get(slot_name) if tree_node else None
            if child is not None:
                v = child.get('chosenPartName')
                return ('' if v == 'none' else v), child
            return None, None
        v = self.flat.get(slot_name)
        if v is None:
            v = self.flat.get(path)
        return ('' if v == 'none' else v), None


class TreeNode:
    __slots__ = ('slot', 'path', 'part', 'children', 'reason')

    def __init__(self, slot, path, part, reason):
        self.slot, self.path, self.part, self.reason = slot, path, part, reason
        self.children = []

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()


def resolve(index, ns, root, choice=None, problems=None, max_depth=50):
    """Resolve the slot tree like slotSystem.fillSlots_rec. Returns TreeNode root.
    problems: list collecting (path, message) for missing/unfit parts."""
    choice = choice or Choice()
    problems = problems if problems is not None else []
    top = TreeNode(None, '/', root, 'root')
    tree_root = choice.tree

    def rec(node, part, path, tnode, depth):
        if depth > max_depth:
            problems.append((path, 'more than 50 recursion levels (cycle?)'))
            return
        for slot in part_slots(part.data):
            spath = path + slot['name'] + '/'
            user, child_t = choice.pick(spath, slot['name'], tnode)
            chosen, reason = None, None
            if user == '':
                continue
            if user:
                p = index.get(ns, user)
                if p is None:
                    problems.append((spath, f'chosen part "{user}" not found, using default'))
                elif not part_fits(p.data, slot):
                    problems.append((spath, f'chosen part "{user}" has wrong slot type, using default'))
                else:
                    chosen, reason = p, 'user'
            if chosen is None:
                d = slot['default']
                if not d:
                    continue
                chosen = index.get(ns, d)
                reason = 'default'
                if chosen is None:
                    problems.append((spath, f'default part "{d}" not found'))
                    continue
                if not part_fits(chosen.data, slot):
                    # the game loads a default even if its type does not match; flag it
                    problems.append((spath, f'default part "{d}" does not fit slot types {slot["allowTypes"]}'))
            child = TreeNode(slot, spath, chosen, reason)
            node.children.append(child)
            rec(child, chosen, spath, child_t, depth + 1)

    rec(top, root, '/', tree_root, 1)
    return top


def dumps_compact(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)
