# crm.spec — PyInstaller spec for the crm CLI.
# Builds a single-file executable that bundles the CPython runtime, all
# Python dependencies, and the crm package data (skills/*.md).
#
# Build:  pyinstaller crm.spec
# Output: dist/crm  (Linux/macOS)  or  dist/crm.exe  (Windows)

# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['crm/__main__.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('crm/skills', 'crm/skills'),
    ],
    hiddenimports=[
        'requests_ntlm',
        'prompt_toolkit',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='crm',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
