# PyInstaller spec for Beerus Portable — the portable app edition
# of BEERUS.EXE.
#
# Build (from the repo root, on Windows):
#     pyinstaller packaging/Beerus.spec --noconfirm
# Output: dist/Beerus/  (a one-folder build: Beerus.exe plus its
# runtime libraries in the same folder)
#
# One-FOLDER, not one-file, on purpose: this is the portable
# edition. The folder unzips anywhere — a USB stick included —
# and runs with no install and no Python on the machine. A
# one-folder build also starts faster and shows its contents
# honestly; nothing is unpacked to a temp folder at launch.
#
# No console window: the portable edition is the window. The
# launcher (packaging/launch_beerus.py) wires the engine and
# opens the gate flow.
#
# Beerus ships no data files in the build: the theme is code,
# the palette is code, and the test fixtures are not part of
# the product — so datas is empty and that is correct, not an
# omission. Certificates are written at run time, by the app,
# into a certificates folder beside the exe (see the launcher).

import sys
from pathlib import Path

# PyInstaller 6 does NOT inject the Windows version-info classes
# into the spec namespace — they must be imported explicitly, or
# the build dies with NameError the first time the spec runs
# (the lesson Argus and Charon both recorded in their specs).
# Import exactly the classes used below. The import itself is
# Windows-only (the module needs pywin32), so it is guarded the
# same way the version value below is: on Windows the resource
# is built; anywhere else the spec still runs for smoke builds.
if sys.platform.startswith("win"):
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VSVersionInfo,
        VarFileInfo,
        VarStruct,
    )

ROOT = Path(SPECPATH).parent

# Windows version resource for the exe: File Explorer, the task
# manager, and SmartScreen all read THIS — an exe with no version
# info looks anonymous. Version 0.6.3 matches beerus.__version__
# (the release workflow's tag check keeps them from drifting).
# Version resources exist only on Windows; the guard keeps this
# spec runnable for off-Windows smoke builds.
version_info = None
if sys.platform.startswith("win"):
    version_info = VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=(0, 6, 2, 0),
            prodvers=(0, 6, 2, 0),
            mask=0x3F,
            flags=0x0,
            OS=0x40004,
            fileType=0x1,
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            StringFileInfo(
                [
                    StringTable(
                        "040904B0",
                        [
                            StringStruct(
                                "CompanyName", "Sentinel Safeguard LLC"
                            ),
                            StringStruct(
                                "FileDescription",
                                "BEERUS.EXE - Beerus Portable: "
                                "secure drive destruction",
                            ),
                            StringStruct("FileVersion", "0.6.3.0"),
                            StringStruct("InternalName", "Beerus"),
                            StringStruct(
                                "LegalCopyright",
                                "(c) Sentinel Safeguard LLC",
                            ),
                            StringStruct(
                                "OriginalFilename", "Beerus.exe"
                            ),
                            StringStruct(
                                "ProductName", "BEERUS.EXE"
                            ),
                            StringStruct("ProductVersion", "0.6.3"),
                        ],
                    )
                ]
            ),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )

a = Analysis(
    [str(ROOT / "packaging" / "launch_beerus.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Beerus",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=version_info,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Beerus",
)
