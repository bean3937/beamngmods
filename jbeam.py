"""Tolerant JBeam parser (SJSON, as read by BeamNG's lua/common/ljson.lua) and writer.

Accepted on top of JSON, matching the game's reader:
- // and /* */ comments; commas are optional/ignored (trailing or missing)
- '=' as well as ':' between key and value; bare (unquoted) keys
- root object braces may be omitted ("name": {...}, "name2": {...})
Duplicate dict keys: last one wins (as in Lua).
"""
import json
import re

_TOKEN = re.compile(r'''
    (?P<ws>[\s,]+)
  | (?P<lc>//[^\n]*)
  | (?P<bc>/\*.*?(?:\*/|\Z))
  | (?P<str>"(?:[^"\\]|\\.)*")
  | (?P<num>[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)
  | (?P<punct>[{}\[\]:=])
  | (?P<word>[A-Za-z_][A-Za-z0-9_.]*)
''', re.S | re.X)

_WORDS = {'true': True, 'false': False, 'null': None,
          'nan': float('nan'), 'NaN': float('nan'),
          'inf': float('inf'), 'Infinity': float('inf')}


class JBeamError(ValueError):
    pass


def _tokens(text):
    pos, n = 0, len(text)
    match = _TOKEN.match
    while pos < n:
        m = match(text, pos)
        if not m:
            line = text.count('\n', 0, pos) + 1
            raise JBeamError(f'unexpected character {text[pos]!r} at line {line}')
        kind = m.lastgroup
        if kind == 'str':
            yield 'v', m.group()
        elif kind == 'num':
            yield 'n', m.group()
        elif kind == 'punct':
            yield m.group(), None
        elif kind == 'word':
            yield 'w', m.group()   # bare key or literal; checked by the parser
        pos = m.end()


def _decode_str(s):
    try:
        return json.loads(s)
    except ValueError:
        # stray control chars / bad escapes inside strings: keep raw content
        return s[1:-1]


def _decode_num(s):
    if s.startswith('+'):
        s = s[1:]
    if re.fullmatch(r'-?\d+', s):
        return int(s)
    return float(s)


def loads(text):
    if text.startswith('﻿'):
        text = text[1:]
    toks = list(_tokens(text))
    if not toks:
        raise JBeamError('empty document')
    pos = 0

    def value():
        nonlocal pos
        kind, raw = toks[pos]
        pos += 1
        if kind == 'v':
            return _decode_str(raw)
        if kind == 'n':
            return _decode_num(raw)
        if kind == 'w':
            if raw not in _WORDS:
                raise JBeamError(f'unknown bare word {raw!r}')
            return _WORDS[raw]
        if kind == '[':
            out = []
            while True:
                if pos >= len(toks):
                    raise JBeamError('unterminated list')
                if toks[pos][0] == ']':
                    pos += 1
                    return out
                out.append(value())
        if kind == '{':
            out = {}
            while True:
                if pos >= len(toks):
                    raise JBeamError('unterminated object')
                k, r = toks[pos]
                if k == '}':
                    pos += 1
                    return out
                if k not in ('v', 'n', 'w'):
                    raise JBeamError(f'bad object key token {k!r}')
                pos += 1
                key = _decode_str(r) if k == 'v' else r
                pos = sep(pos, key)
                v = value()
                if v is None:            # Lua: t[k] = nil removes the key
                    out.pop(key, None)
                else:
                    out[key] = v
        raise JBeamError(f'unexpected token {kind!r}')

    def sep(p, key):
        if p >= len(toks) or toks[p][0] not in (':', '='):
            raise JBeamError(f'missing ":" after key {key!r}')
        return p + 1

    if toks[0][0] in ('{', '['):
        return value()   # like ljson: anything after the root value is ignored
    root = {}            # SJSON implicit root object
    while pos < len(toks):
        k, r = toks[pos]
        if k not in ('v', 'w'):
            raise JBeamError(f'bad root key token {k!r}')
        pos += 1
        key = _decode_str(r) if k == 'v' else r
        pos = sep(pos, key)
        root[key] = value()
    return root


def dumps(obj):
    """Write strict JSON (valid JBeam). NaN/inf are not expected in output."""
    return json.dumps(obj, indent=1, ensure_ascii=False, allow_nan=False)
