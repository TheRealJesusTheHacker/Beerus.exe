"""Beerus Portable — the no-install Windows app edition of BEERUS.EXE.

Beerus has two forms. The Destruct Key Pro is the bootable live
USB: it boots a machine into Beerus and destroys drives there.
Beerus Portable is the same destruction engine — the same NIST
800-88 methods, the same consent gates, the same verification and
certificates — as a Windows app that runs on a machine that is
already switched on, from a folder or a USB stick, with nothing to
install.

This package is a thin shell over the ``beerus`` engine package.
It contains no wipe logic of its own and no second copy of the
gates: every destruction goes through ``beerus.engine.WipeEngine``
behind a token minted by ``beerus.gates.GateAuthority``, exactly
as on the boot key. What lives here is only what the portable
form needs and the boot form does not:

- a Windows drive inventory (PowerShell, read-only) that feeds
  the engine the same ``Drive`` records the boot form gets from
  ``lsblk``;
- the portable refusal rules for a live Windows system (the drive
  Windows is running from, and the drive this app is running
  from, can never be targets);
- the window itself.

Destruction here is real destruction: the engine overwrites or
sanitizes the drive's surface — whole drives, through the
engine's NIST 800-88 plans. Nothing in this app "deletes" files
and calls it erasing; removing a directory entry so data lingers
is exactly what Beerus exists to never do.
"""

from __future__ import annotations

__all__ = ["APP_NAME"]

APP_NAME = "Beerus Portable"
