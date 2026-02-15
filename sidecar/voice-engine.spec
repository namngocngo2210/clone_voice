# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_all

block_cipher = None

# Collect everything for the core AI libraries
datas = []
binaries = []
hidden_imports = []

libs_to_collect = ['chatterbox', 'vieneu', 'vieneu_utils', 'transformers']
for lib in libs_to_collect:
    tmp_ret = collect_all(lib)
    datas += tmp_ret[0]
    binaries += tmp_ret[1]
    hidden_imports += tmp_ret[2]

# Standard hidden imports
hidden_imports += [
    'torch',
    'torchaudio',
    'chatterbox',
    'vieneu',
    'faster_whisper',
    'pydub',
    'librosa',
    'scipy.signal',
    'scipy.sparse.csgraph._validation',
    'numpy',
    'onnxruntime',
    's3tokenizer',
    'conformer',
    'diffusers',
    'perth'
]
# Keep onefile EXE lean: heavy runtime DLLs are shipped externally via copy_dlls.bat
excluded_packages = ['torch', 'ctranslate2']

# Include ffmpeg binaries
binaries += [
    ('ffmpeg.exe', '.'),
    ('ffprobe.exe', '.'),
    ('ffplay.exe', '.')
]

# Bundle MSVC runtime next to executable to avoid torch._C load failures
msvc_runtime_candidates = [
    os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs', 'Python', 'Python311', 'vcruntime140.dll'),
    os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs', 'Python', 'Python311', 'vcruntime140_1.dll'),
    r'C:\Windows\System32\msvcp140.dll',
    r'C:\Windows\System32\msvcp140_1.dll',
    r'C:\Windows\System32\concrt140.dll',
]
for dll_path in msvc_runtime_candidates:
    if os.path.exists(dll_path):
        binaries.append((dll_path, '.'))

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter'], # Some AI deps import stdlib modules like unittest/pydoc at runtime.
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Robust exclusion of heavy external DLLs from packed EXE
def is_excluded_dll(name, path):
    name_lower = name.lower()
    path_lower = path.lower()
    if not name_lower.endswith('.dll'):
        return False
    for pkg in excluded_packages:
        if f'\\{pkg}\\' in path_lower or f'/{pkg}/' in path_lower:
            return True
    return False

a.binaries = [b for b in a.binaries if not is_excluded_dll(b[0], b[1])]
a.datas = [d for d in a.datas if not is_excluded_dll(d[0], d[1])]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='voice-engine-x86_64-pc-windows-msvc',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
