"""Path detection and remembered settings (%APPDATA%/beamng-swap-catalog/settings.json)."""
import json
import os
import re

STEAM_DEFAULT = r'C:\Program Files (x86)\Steam'
GAME_SUBDIR = os.path.join('steamapps', 'common', 'BeamNG.drive')


def _steam_roots():
    roots = []
    try:
        import winreg
        for hive, key, val in ((winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam', 'SteamPath'),
                               (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Valve\Steam', 'InstallPath')):
            try:
                with winreg.OpenKey(hive, key) as k:
                    roots.append(os.path.normpath(winreg.QueryValueEx(k, val)[0]))
            except OSError:
                pass
    except ImportError:          # not Windows
        pass
    return roots + [STEAM_DEFAULT]


def detect_game_dir():
    """BeamNG.drive folder from Steam's library list (steamapps/libraryfolders.vdf), else None."""
    libs = []
    for root in _steam_roots():
        libs.append(root)
        try:
            with open(os.path.join(root, 'steamapps', 'libraryfolders.vdf'), encoding='utf-8') as fh:
                libs += [p.replace('\\\\', '\\') for p in re.findall(r'"path"\s+"([^"]+)"', fh.read())]
        except OSError:
            pass
    for lib in dict.fromkeys(libs):
        g = os.path.join(lib, GAME_SUBDIR)
        if os.path.isdir(os.path.join(g, 'content', 'vehicles')):
            return g
    return None


DEFAULT_GAME = os.path.join(STEAM_DEFAULT, GAME_SUBDIR)


def detect_blender():
    """Newest blender.exe under Program Files/Blender Foundation (or on PATH), else ''. Blender is optional: it
    rounds and smooths the generated engine adapters and exhaust pipes (Phase 4)."""
    import glob
    import shutil
    found = []
    for root in (os.environ.get('ProgramFiles', r'C:\Program Files'), os.environ.get('ProgramFiles(x86)', '')):
        if root:
            for p in glob.glob(os.path.join(root, 'Blender Foundation', 'Blender *', 'blender.exe')):
                m = re.search(r'Blender (\d+)\.(\d+)', p)
                found.append(((int(m.group(1)), int(m.group(2))) if m else (0, 0), p))
    if found:
        return max(found)[1]
    return shutil.which('blender') or ''


def default_game_dir():
    return detect_game_dir() or DEFAULT_GAME
SETTINGS_FILE = os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'),
                             'beamng-swap-catalog', 'settings.json')


def load():
    try:
        with open(SETTINGS_FILE, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save(data):
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=2)


def game_version(game_dir):
    """Version from integrity.json ("version": "0.40.5.0"), else None."""
    try:
        with open(os.path.join(game_dir, 'integrity.json'), encoding='utf-8') as fh:
            head = fh.read(4096)
        m = re.search(r'"version"\s*:\s*"([^"]+)"', head)
        if m:
            return m.group(1)
        with open(os.path.join(game_dir, 'integrity.json'), encoding='utf-8') as fh:
            m = re.search(r'"version"\s*:\s*"([^"]+)"', fh.read())
        return m.group(1) if m else None
    except OSError:
        return None


def user_folder(game_dir=None):
    """BeamNG user folder: startup.ini UserPath override, else %LOCALAPPDATA%/BeamNG/BeamNG.drive."""
    if game_dir:
        try:
            with open(os.path.join(game_dir, 'startup.ini'), encoding='utf-8') as fh:
                for line in fh:
                    m = re.match(r'\s*UserPath\s*=\s*(\S.*?)\s*$', line)
                    if m:
                        p = m.group(1)
                        return p if os.path.isabs(p) else os.path.join(game_dir, p)
        except OSError:
            pass
    return os.path.join(os.environ.get('LOCALAPPDATA', ''), 'BeamNG', 'BeamNG.drive')


def detect_mods_dir(game_dir=None):
    """<user folder>/current/mods (0.38+), else the highest <version>/mods folder."""
    uf = user_folder(game_dir)
    cur = os.path.join(uf, 'current', 'mods')
    if os.path.isdir(cur):
        return cur
    best = None
    if os.path.isdir(uf):
        for d in os.listdir(uf):
            if re.fullmatch(r'\d+\.\d+', d) and os.path.isdir(os.path.join(uf, d, 'mods')):
                if best is None or tuple(map(int, d.split('.'))) > tuple(map(int, best.split('.'))):
                    best = d
    return os.path.join(uf, best, 'mods') if best else cur


def log_file(mods_dir):
    """BeamNG writes beamng.log into the user folder version directory (parent of mods)."""
    return os.path.join(os.path.dirname(mods_dir.rstrip('\\/')), 'beamng.log')
