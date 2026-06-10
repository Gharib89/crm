# crm.spec — PyInstaller spec for the crm CLI.
# Builds the onedir bundle dist/crm/, containing the `crm` launcher and
# _internal/ (CPython runtime, dependencies, crm package data).
#
# Build:  pyinstaller crm.spec
# Output: dist/crm/  (directory bundle)

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
        # msal is lazy-imported inside D365Backend._make_oauth_auth (oauth scheme);
        # list it so the frozen bundle ships it despite no module-level reference.
        'msal',
        # yaml is lazy-imported inside crm.commands.apply (spec parsing); list it so
        # the frozen bundle ships PyYAML despite no module-level reference.
        'yaml',
        # The crm.commands.* modules below are resolved at runtime via
        # importlib.import_module() in _LazyJsonAwareGroup.get_command (crm/cli.py),
        # which PyInstaller's static analysis cannot follow. List every module that
        # appears in _LazyJsonAwareGroup._lazy_commands here, or that command will be
        # missing from the frozen bundle and crash on invocation. Keep the two in sync.
        # (crm.commands._helpers is statically imported by cli.py and listed for clarity.)
        'crm.commands._helpers',
        'crm.commands.action',
        'crm.commands.app',
        'crm.commands.apply',
        'crm.commands.async_ops',
        'crm.commands.batch',
        'crm.commands.connection',
        'crm.commands.data',
        'crm.commands.describe',
        'crm.commands.entity',
        'crm.commands.form',
        'crm.commands.metadata',
        'crm.commands.plugin',
        'crm.commands.profile',
        'crm.commands.query',
        'crm.commands.repl',
        'crm.commands.ribbon',
        'crm.commands.scaffold',
        'crm.commands.security',
        'crm.commands.session',
        'crm.commands.skill',
        'crm.commands.sla',
        'crm.commands.solution',
        'crm.commands.translation',
        'crm.commands.view',
        'crm.commands.webresource',
        'crm.commands.workflow',
        # keyring resolves its OS backend through entry points, which PyInstaller's
        # static analysis can't follow — bundle the package and every platform
        # backend so `connection set-password` works in the frozen binary on each
        # OS. Each build keeps only the backend whose deps it can import; the other
        # two emit a benign "hidden import not found" warning (e.g. SecretService's
        # secretstorage on Windows) and are simply skipped. win32ctypes is the
        # Windows backend's runtime dependency (pywin32-ctypes).
        'keyring',
        'keyring.backends.Windows',
        'keyring.backends.macOS',
        'keyring.backends.SecretService',
        'win32ctypes.pywin32.win32cred',
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
    [],
    exclude_binaries=True,
    name='crm',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='crm',
)
