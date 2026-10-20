"""Stage the Beerus package and the image marker into the image tree.

Run by ``image/build.sh`` on the build machine, before the live
image is assembled. Two things are staged, and both are part of
how the finished key protects itself:

1. **The beerus package**, copied from this repository into the
   image's ``/opt/beerus`` — a fresh copy from source at every
   build, never a leftover from a previous build (the staged
   tree is deleted and recreated).

2. **The image marker**, ``/etc/beerus/live-image.json`` inside
   the image, generated here from the package's own constants
   (``beerus.bootmedium``): the volume label the image is built
   with, the Beerus version, and where job documents live. The
   running console (``beerus.live``) refuses to arm the
   real-drive executor unless this marker is present and valid,
   and inventory marks any disk carrying the label as a boot
   device — so the marker and the label must agree, and both come
   from the one constants module rather than being typed twice.

This script only ever writes inside ``image/config/includes.chroot``
(the staged tree is listed in the repo's ``.gitignore``). It does
not build the image; ``build.sh`` does that next, on a real build
machine.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# Files the running image must contain for the console to exist.
_REQUIRED_PACKAGE_FILES = (
    "beerus/live.py",
    "beerus/bootmedium.py",
    "beerus/bootui/__init__.py",
    "beerus/bootui/app.py",
)


class StageError(Exception):
    """Staging could not produce a complete image tree."""


def _literal_assignments(path: Path) -> dict:
    """String literals assigned to names at a module's top level.

    The marker's facts are read out of the package's own source
    files with the AST — never by importing them. Staging runs
    inside other processes (build scripts, test runners), and
    importing a second copy of the package there would hand the
    marker whatever stale module the process happened to hold,
    or worse, split the package's classes in two. Reading the
    literals keeps the code the single source of truth without
    executing any of it.
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: dict = {}
    for node in tree.body:
        target = None
        value = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        if isinstance(target, ast.Name) and isinstance(value, ast.Constant):
            if isinstance(value.value, str):
                found[target.id] = value.value
    return found


def _package_facts(repo_root: Path) -> dict:
    """Version + image constants, read from the package sources."""
    init_facts = _literal_assignments(repo_root / "beerus" / "__init__.py")
    medium_facts = _literal_assignments(repo_root / "beerus" / "bootmedium.py")
    facts = {
        "version": init_facts.get("__version__"),
        "image": medium_facts.get("IMAGE_MARKER_FORMAT"),
        "volume_label": medium_facts.get("LIVE_VOLUME_LABEL"),
        "documents_dir": medium_facts.get("DEFAULT_DOCUMENTS_DIR"),
    }
    missing = [name for name, value in facts.items() if not value]
    if missing:
        raise StageError(
            "Could not read "
            + ", ".join(missing)
            + " from the beerus package sources. The image was "
            "not staged."
        )
    return facts


def _image_marker(facts: dict, built: str) -> dict:
    """The marker content, drawn from the package's own constants."""
    return {
        "image": facts["image"],
        "version": facts["version"],
        "volume_label": facts["volume_label"],
        "documents_dir": facts["documents_dir"],
        "built": built,
    }


def stage(repo_root: Path, image_dir: Path, *, built: str | None = None) -> dict:
    """Stage package + marker under *image_dir*'s includes tree.

    Returns the marker dict that was written. Raises StageError
    with a plain message when the repository tree is incomplete —
    an image staged from a broken tree must fail here, loudly,
    rather than on a client's machine.
    """
    repo_root = Path(repo_root)
    image_dir = Path(image_dir)
    package_src = repo_root / "beerus"
    if not package_src.is_dir():
        raise StageError(
            f"The beerus package was not found at {package_src}. "
            "Run this from a full checkout of the Beerus repository."
        )
    for relative in _REQUIRED_PACKAGE_FILES:
        if not (repo_root / relative).is_file():
            raise StageError(
                f"The repository is missing {relative}, which the "
                "boot console needs. The image was not staged."
            )

    facts = _package_facts(repo_root)

    if built is None:
        built = datetime.now(timezone.utc).date().isoformat()
    marker = _image_marker(facts, built)

    includes = image_dir / "config" / "includes.chroot"

    # A symlink inside the package has no business in the image:
    # copying one would silently stage whatever it points at —
    # possibly a file that was never reviewed as part of the
    # package. Refuse, loudly, before anything is copied.
    for path in package_src.rglob("*"):
        if path.is_symlink():
            raise StageError(
                f"The package contains a symbolic link ({path}), "
                "which staging will not follow into the image. "
                "Replace it with a real file. The image was not "
                "staged."
            )

    staged_package = includes / "opt" / "beerus"
    if staged_package.exists():
        shutil.rmtree(staged_package)
    staged_package.mkdir(parents=True)
    shutil.copytree(
        package_src,
        staged_package / "beerus",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    for relative in _REQUIRED_PACKAGE_FILES:
        if not (staged_package / relative).is_file():
            raise StageError(
                f"Staging did not produce {relative} in the image "
                "tree. The image was not staged."
            )

    marker_path = includes / "etc" / "beerus" / "live-image.json"
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(
        json.dumps(marker, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return marker


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    image_dir = Path(argv[0]) if argv else Path(__file__).resolve().parent
    repo_root = image_dir.parent
    try:
        marker = stage(repo_root, image_dir)
    except StageError as exc:
        print(f"stage_image: {exc}", file=sys.stderr)
        return 1
    print(
        "Staged the beerus package "
        f"(version {marker['version']}) into the image tree."
    )
    print(
        "Wrote the image marker: volume label "
        f"{marker['volume_label']}, documents at "
        f"{marker['documents_dir']}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
