"""Engine-level refusal proofs for Beerus Portable.

The GUI's greyed rows are presentation. These tests prove the
protection does not depend on presentation: the engine and the
gates themselves refuse the portable app's protected drives, and
the shipped (disarmed) executor refuses a fully-confirmed wipe
before anything is sent to a drive.
"""

from __future__ import annotations

import pytest

from beerus.engine import WipeEngine
from beerus.errors import BootDeviceError, ExecutorNotArmedError
from beerus.executor import RealExecutor
from beerus.gates import GateAuthority
from beerus.methods import select_plan
from conftest import make_boot_drive, make_drive


def _confirmed_token(drive):
    authority = GateAuthority()
    session = authority.begin_session(drive, select_plan(drive))
    session.confirm_identity(drive.serial)
    session.acknowledge_scope()
    return authority, session.final_confirmation(f"ERASE {drive.serial}")


def test_gates_refuse_a_boot_marked_drive():
    # On a live Windows system both the system drive and the
    # app's own drive arrive marked is_boot (see
    # beerus_portable.windows_inventory); the gate refuses either
    # before a session can even start.
    authority = GateAuthority()
    drive = make_boot_drive()
    with pytest.raises(BootDeviceError):
        authority.begin_session(drive, select_plan(drive))


def test_engine_refuses_a_boot_marked_drive_with_a_real_token():
    drive = make_drive()
    authority, token = _confirmed_token(drive)
    boot_twin = make_boot_drive(
        device_path=drive.device_path,
        model=drive.model,
        serial=drive.serial,
        size_bytes=drive.size_bytes,
        interface=drive.interface,
    )
    engine = WipeEngine(
        authority, RealExecutor(), enumerator=lambda: [boot_twin]
    )
    with pytest.raises(BootDeviceError):
        engine.wipe(boot_twin, token)


def test_disarmed_executor_refuses_a_fully_confirmed_wipe(tmp_path):
    # The shipped portable build's executor state: RealExecutor in
    # its default, disarmed construction. A perfect gate run still
    # ends in refusal — nothing is sent to any drive.
    target_file = tmp_path / "fake-drive.img"
    target_file.write_bytes(b"\xaa" * (8 * 1024 * 1024))
    drive = make_drive(device_path=str(target_file))
    authority, token = _confirmed_token(drive)
    engine = WipeEngine(
        authority, RealExecutor(), enumerator=lambda: [drive]
    )
    with pytest.raises(ExecutorNotArmedError):
        engine.wipe(drive, token)
    assert target_file.read_bytes() == b"\xaa" * (8 * 1024 * 1024)
