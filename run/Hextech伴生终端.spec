# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['pandas', 'numpy', 'requests', 'PIL', 'PIL.ImageTk', 'win32gui', 'psutil', 'fastapi', 'uvicorn', 'filelock']
hiddenimports += collect_submodules('uvicorn')


a = Analysis(
    ['hextech_ui.py'],
    pathex=[],
    binaries=[],
    datas=[('C:\\Users\\apple\\claudecode\\run\\build\\_bundle_runtime\\static', 'static'), ('C:\\Users\\apple\\claudecode\\run\\build\\_bundle_runtime\\config', 'config'), ('C:\\Users\\apple\\claudecode\\run\\build\\_bundle_runtime\\assets', 'assets'), ('C:\\Users\\apple\\claudecode\\run\\build\\_bundle_runtime\\bundle_manifest.json', '.')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter.test', 'unittest', 'pydoc', 'scipy', 'matplotlib', 'botocore', 'boto3', 's3transfer', 'jmespath'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Hextech伴生终端',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='C:\\Users\\apple\\claudecode\\run\\version_info.txt',
    icon='NONE',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Hextech伴生终端',
)
