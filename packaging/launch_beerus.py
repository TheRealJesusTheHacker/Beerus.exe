"""PyInstaller entry point for Beerus Portable (Beerus.exe).

Kept outside the packages so PyInstaller's analysis starts from
a plain script (the fleet pattern from Argus and Charon).

Running it opens the Beerus Portable window. Everything is wired
here, in the open:

- the engine is the same ``beerus`` engine the boot key uses;
- the executor is ``RealExecutor`` in its shipped, DISARMED
  state — this build cannot send a single destructive command
  until a Sentinel deployment arms it the engine's way (the
  constructor flag in a deployment build, plus the
  BEERUS_REAL_WIPE_ARMED environment variable), never from any
  setting inside the app;
- verification reads the wiped surface back through the
  engine's own surface reader, so a finished wipe is checked
  before any certificate is issued;
- documents (certificates and failure reports) are written to a
  ``certificates`` folder beside the app — inside the portable
  folder, nowhere else.
"""

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from beerus.engine import WipeEngine
from beerus.executor import RealExecutor, _default_runner
from beerus.gates import GateAuthority
from beerus.verify import DeviceSurfaceReader, WipeVerifier
from beerus_portable.app import MainWindow
from beerus_portable.windows_inventory import WindowsDriveEnumerator


def _app_base_dir() -> Path:
    """The folder the app is running from (the portable folder)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def main() -> int:
    app = QApplication(sys.argv)
    base = _app_base_dir()
    documents_dir = base / "certificates"

    enumerator = WindowsDriveEnumerator(app_path=sys.executable)
    authority = GateAuthority()
    executor = RealExecutor()  # ships disarmed — see module docstring
    verifier = WipeVerifier(
        surface_factory=lambda drive: DeviceSurfaceReader(
            drive.device_path, drive.size_bytes
        ),
        firmware_runner=_default_runner,
    )
    engine = WipeEngine(
        authority,
        executor,
        enumerator=enumerator,
        verifier=verifier,
    )
    window = MainWindow(
        engine,
        authority,
        documents_dir=documents_dir,
        context_provider=lambda: enumerator.context,
    )
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
