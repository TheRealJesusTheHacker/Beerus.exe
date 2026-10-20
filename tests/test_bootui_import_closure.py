"""Import-closure guard for the boot UI (F-01 regression).

The live image stages ONLY the ``beerus`` package into
``/opt/beerus`` (``image/stage_image.py`` copies that one
package and nothing else). The boot UI therefore may import
only from ``beerus.*``, ``PySide6.*``, and the standard
library. Phase 4 as built imported ``human_size`` from
``beerus_portable.app`` — a package the image does not stage —
so ``python3 -m beerus.bootui`` died at import time on every
real image and the graphical session could never start, while
the repo test suite (which runs with ``beerus_portable``
importable) never noticed.

Two guards, so the hole cannot reopen:

- a STATIC guard: every module under ``beerus/bootui/`` is
  parsed and every import it makes is checked against the
  allowed set — every ``Import``/``ImportFrom`` node anywhere
  in the tree (a lazy import inside a function body counts),
  and every ``__import__`` / ``importlib.import_module`` call
  whose first argument is a constant string naming a package
  outside the allowed set;
- a DYNAMIC guard: only the ``beerus`` package is staged into
  a scratch tree the way ``stage_image`` stages it, and a
  subprocess whose PYTHONPATH is exactly that tree must
  import ``beerus.bootui.app`` cleanly, with
  ``beerus_portable`` never entering ``sys.modules``.

Plus the size formatter's pinned format: the boot UI carries
its own copy, and its output is the format the drive list and
the screenshots have always shown.
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

REPO_ROOT = Path(__file__).resolve().parent.parent
BOOTUI_DIR = REPO_ROOT / "beerus" / "bootui"

_ALLOWED_TOP_LEVELS = {"beerus", "PySide6"}


def _imports_of(path: Path):
    """(module, level) for every import in one source file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, 0
        elif isinstance(node, ast.ImportFrom):
            yield node.module or "", node.level


def _top_allowed(top: str) -> bool:
    return (
        top in _ALLOWED_TOP_LEVELS
        or top in sys.stdlib_module_names
        or top == "__future__"
    )


def _closure_offenders(path: Path):
    """Every import-closure offence in one source file.

    The whole AST is walked, so position does not matter: a
    plain import nested in a function body, a ``__import__``
    call, and an ``importlib.import_module`` call are all
    judged by the package they name. A dynamic call whose
    argument is not a constant string cannot be judged here —
    the staged-import guard below is the backstop for those.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not _top_allowed(alias.name.split(".", 1)[0]):
                    offenders.append(f"{path.name}: imports {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                if not _top_allowed(node.module.split(".", 1)[0]):
                    offenders.append(f"{path.name}: imports {node.module}")
        elif isinstance(node, ast.Call):
            func = node.func
            dynamic = (
                isinstance(func, ast.Name) and func.id == "__import__"
            ) or (
                isinstance(func, ast.Attribute)
                and func.attr == "import_module"
            )
            if (
                dynamic
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and node.args[0].value
            ):
                module = node.args[0].value
                if not _top_allowed(module.split(".", 1)[0]):
                    offenders.append(
                        f"{path.name}: dynamically imports {module}"
                    )
    return offenders


def _closure_offenders_in(directory: Path):
    offenders = []
    for path in sorted(directory.glob("*.py")):
        offenders.extend(_closure_offenders(path))
    return offenders


def test_bootui_imports_nothing_outside_the_staged_package():
    modules = sorted(BOOTUI_DIR.glob("*.py"))
    assert modules, "the boot UI package is missing"
    offenders = []
    for path in modules:
        for module, level in _imports_of(path):
            if level > 0:
                # A relative import stays inside beerus.bootui.
                continue
            top = module.split(".", 1)[0]
            if top in _ALLOWED_TOP_LEVELS:
                continue
            if top in sys.stdlib_module_names:
                continue
            if top == "__future__":
                continue
            offenders.append(f"{path.name}: imports {module}")
    assert not offenders, (
        "the boot UI imports outside {beerus.*, PySide6.*, "
        "stdlib} — the image stages only the beerus package, so "
        "these imports would fail on the real image: "
        + "; ".join(offenders)
    )


def test_bootui_never_imports_beerus_portable():
    for path in sorted(BOOTUI_DIR.glob("*.py")):
        for module, level in _imports_of(path):
            assert level > 0 or not module.startswith(
                "beerus_portable"
            ), f"{path.name} imports {module}, which the image does not stage"


def test_bootui_has_no_dynamic_import_of_an_unstaged_package():
    """R-06 regression: a lazy ``__import__('beerus_portable')``
    or ``importlib.import_module('beerus_portable')`` inside a
    function body is not an Import node and never fires during
    a staged import — the static guard must name it anyway."""
    offenders = _closure_offenders_in(BOOTUI_DIR)
    assert not offenders, (
        "the boot UI reaches outside {beerus.*, PySide6.*, "
        "stdlib}: " + "; ".join(offenders)
    )


def test_closure_guard_catches_lazy_dynamic_imports(tmp_path):
    """R-06 regression proof, in a scratch copy: the guard
    fails on each lazy spelling, and stays silent on dynamic
    imports of allowed packages."""
    scratch = tmp_path / "bootui"
    scratch.mkdir()
    (scratch / "lazy_dunder.py").write_text(
        "def load():\n    return __import__('beerus_portable')\n",
        encoding="utf-8",
    )
    (scratch / "lazy_importlib.py").write_text(
        "import importlib\n\n\n"
        "def load():\n"
        "    return importlib.import_module('beerus_portable.app')\n",
        encoding="utf-8",
    )
    (scratch / "lazy_plain.py").write_text(
        "def load():\n    import beerus_portable\n    return beerus_portable\n",
        encoding="utf-8",
    )
    (scratch / "clean.py").write_text(
        "import importlib\nimport os\n\n\n"
        "def load():\n"
        "    importlib.import_module('beerus.live')\n"
        "    __import__('os')\n"
        "    return os.sep\n",
        encoding="utf-8",
    )
    offenders = _closure_offenders_in(scratch)
    assert any("lazy_dunder.py" in item for item in offenders)
    assert any("lazy_importlib.py" in item for item in offenders)
    assert any("lazy_plain.py" in item for item in offenders)
    assert not any("clean.py" in item for item in offenders)


def test_bootui_app_imports_from_a_staged_only_tree(tmp_path):
    """Stage ONLY the beerus package (the way stage_image does:
    the package copied under a PYTHONPATH root) and import
    beerus.bootui.app in a subprocess that can see nothing
    else from this repository."""
    staging = tmp_path / "opt_beerus"
    shutil.copytree(
        REPO_ROOT / "beerus",
        staging / "beerus",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    assert not (staging / "beerus_portable").exists()
    program = (
        "import sys\n"
        "import beerus.bootui.app\n"
        "assert 'beerus_portable' not in sys.modules, "
        "'beerus_portable leaked into the staged import'\n"
        "print('STAGED-IMPORT-OK')\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(staging)
    env["QT_QPA_PLATFORM"] = "offscreen"
    proc = subprocess.run(
        [sys.executable, "-c", program],
        cwd=staging,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        "beerus.bootui.app did not import from the staged-only "
        f"tree:\n{proc.stdout}\n{proc.stderr}"
    )
    assert "STAGED-IMPORT-OK" in proc.stdout


def test_human_size_format_is_the_shown_format():
    from beerus.bootui.app import human_size

    assert human_size(0) == "0 bytes"
    assert human_size(-5) == "0 bytes"
    assert human_size(512) == "512 bytes"
    assert human_size(1023) == "1023 bytes"
    assert human_size(1024) == "1.00 KB (1,024 bytes)"
    # The drive-list/screenshot form: 8 MiB of synthetic drive.
    assert human_size(8 * 1024 * 1024) == "8.00 MB (8,388,608 bytes)"
    assert human_size(500 * 1000 * 1000 * 1000) == (
        "465.66 GB (500,000,000,000 bytes)"
    )
    assert human_size(2 * 1024**4) == "2.00 TB (2,199,023,255,552 bytes)"
