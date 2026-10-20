"""Image-definition tests — the Phase 3 definition, checked not built.

SAFETY: staging runs into pytest tmp dirs only. Nothing here runs
live-build, mounts anything, or touches a device.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

# The definition under test is a Debian live image: POSIX file
# modes (the executable hook) and symlinks (the wants/ entry)
# are part of its meaning, and a Windows checkout can represent
# neither — git on Windows materializes files without an exec
# bit and the symlink as a plain copy, so on Windows the
# checker would be grading the checkout, not the definition.
# These tests run where images are defined and built: Linux.
# (Kit-hunt v0.5.1: the windows-latest release pipeline was
# this file's first Windows run — 39 checkout-artifact
# failures, none of them about the definition itself.)
pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="Debian image definition checks need POSIX modes/symlinks (Linux)",
)

import beerus
from beerus.bootmedium import (
    DEFAULT_DOCUMENTS_DIR,
    IMAGE_MARKER_FORMAT,
    LIVE_VOLUME_LABEL,
    load_image_marker,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
IMAGE_DIR = REPO_ROOT / "image"


def _load_image_tool(name: str):
    path = IMAGE_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"beerus_image_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_definition = _load_image_tool("check_definition")
stage_image = _load_image_tool("stage_image")


def _copy_definition_tree(tmp_path: Path) -> tuple[Path, Path]:
    """A minimal repo layout: the package + the definition."""
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copytree(
        REPO_ROOT / "beerus",
        repo / "beerus",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    image = repo / "image"
    shutil.copytree(
        IMAGE_DIR,
        image,
        symlinks=True,
        ignore=shutil.ignore_patterns(
            "__pycache__", "opt", "live-image.json", "*.iso", "*.img"
        ),
    )
    return repo, image


def _hook_text() -> str:
    """The hook's three genuine proofs, for tests that put
    something in front of them."""
    return (
        "test -f /opt/beerus/beerus/live.py || exit 1\n"
        "test -f /etc/beerus/live-image.json || exit 1\n"
        "systemctl mask getty@tty1.service\n"
    )


# ------------------------------------------------------------------ the check


def test_the_checked_in_definition_is_sound():
    assert check_definition.check(IMAGE_DIR) == []


def test_check_catches_a_missing_definition_file(tmp_path):
    _repo, image = _copy_definition_tree(tmp_path)
    (image / "config/includes.chroot/etc/hostname").unlink()
    problems = check_definition.check(image)
    assert any("hostname" in problem for problem in problems)


def test_check_catches_a_label_that_disagrees_with_the_code(tmp_path):
    _repo, image = _copy_definition_tree(tmp_path)
    build_sh = image / "build.sh"
    text = build_sh.read_text(encoding="utf-8")
    build_sh.write_text(
        text.replace(
            f'--iso-volume "{LIVE_VOLUME_LABEL}"', '--iso-volume "SOMETHING_ELSE"'
        ),
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("volume label" in problem for problem in problems)


def test_check_catches_a_missing_engine_tool(tmp_path):
    _repo, image = _copy_definition_tree(tmp_path)
    package_list = image / "config/package-lists/beerus.list.chroot"
    text = package_list.read_text(encoding="utf-8")
    package_list.write_text(text.replace("nvme-cli\n", ""), encoding="utf-8")
    problems = check_definition.check(image)
    assert any("nvme-cli" in problem for problem in problems)


def test_check_catches_a_service_that_only_mentions_the_launcher(tmp_path):
    """M3 regression: the required ExecStart as a comment, with a
    different real command, must not pass."""
    _repo, image = _copy_definition_tree(tmp_path)
    service = (
        image
        / "config/includes.chroot/etc/systemd/system/beerus-live.service"
    )
    text = service.read_text(encoding="utf-8")
    service.write_text(
        text.replace(
            "ExecStart=/usr/local/bin/beerus-live",
            "# ExecStart=/usr/local/bin/beerus-live\nExecStart=/bin/sh",
        ),
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("ExecStart" in problem for problem in problems)


def test_check_catches_an_emptied_build_hook(tmp_path):
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_catches_a_non_executable_launcher(tmp_path):
    import os

    _repo, image = _copy_definition_tree(tmp_path)
    launcher = image / "config/includes.chroot/usr/local/bin/beerus-live"
    os.chmod(launcher, 0o644)
    problems = check_definition.check(image)
    assert any("not executable" in problem for problem in problems)


def test_check_catches_an_unlisted_package(tmp_path):
    _repo, image = _copy_definition_tree(tmp_path)
    package_list = image / "config/package-lists/beerus.list.chroot"
    text = package_list.read_text(encoding="utf-8")
    package_list.write_text(text + "\nopenssh-server\n", encoding="utf-8")
    problems = check_definition.check(image)
    assert any("openssh-server" in problem for problem in problems)


def test_check_catches_directives_in_the_wrong_section(tmp_path):
    """Round-3 regression: a directive systemd would ignore —
    User= sitting in [Unit] — must not satisfy the checker."""
    _repo, image = _copy_definition_tree(tmp_path)
    service = (
        image
        / "config/includes.chroot/etc/systemd/system/beerus-live.service"
    )
    text = service.read_text(encoding="utf-8")
    text = text.replace("User=root\n", "")
    text = text.replace("[Unit]\n", "[Unit]\nUser=root\n", 1)
    service.write_text(text, encoding="utf-8")
    problems = check_definition.check(image)
    assert any("User=root" in problem for problem in problems)


def test_check_catches_an_echo_only_hook(tmp_path):
    """Round-3 regression: hook needles printed by echo are
    output, not action."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\necho live.py live-image.json getty@tty1\nexit 0\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_catches_an_echo_only_launcher(tmp_path):
    _repo, image = _copy_definition_tree(tmp_path)
    launcher = image / "config/includes.chroot/usr/local/bin/beerus-live"
    launcher.write_text(
        "#!/bin/sh\necho 'PYTHONPATH=/opt/beerus python3 -m beerus'\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("launcher" in problem for problem in problems)


def test_check_catches_flags_that_only_appear_in_an_echo(tmp_path):
    _repo, image = _copy_definition_tree(tmp_path)
    build_sh = image / "build.sh"
    text = build_sh.read_text(encoding="utf-8")
    text = text.replace(
        f'--iso-volume "{LIVE_VOLUME_LABEL}"', '--iso-volume "OTHER"'
    )
    text += f"\necho '--iso-volume \"{LIVE_VOLUME_LABEL}\"'\n"
    build_sh.write_text(text, encoding="utf-8")
    problems = check_definition.check(image)
    assert any("volume label" in problem for problem in problems)


def test_check_catches_flags_smuggled_in_a_trailing_comment(tmp_path):
    """Round-4 regression: the correct flags trailing a '#' on
    the lb config line must not satisfy the checker when the
    real flags disagree."""
    _repo, image = _copy_definition_tree(tmp_path)
    build_sh = image / "build.sh"
    text = build_sh.read_text(encoding="utf-8")
    text = text.replace(
        f'--iso-volume "{LIVE_VOLUME_LABEL}"', '--iso-volume "OTHER"'
    )
    text = text.replace(
        "--binary-images iso-hybrid", "--binary-images iso"
    )
    text = text.replace(
        "--source false",
        '--source false # --iso-volume "'
        + LIVE_VOLUME_LABEL
        + '" --binary-images iso-hybrid',
    )
    build_sh.write_text(text, encoding="utf-8")
    problems = check_definition.check(image)
    assert any("volume label" in problem for problem in problems)
    assert any("hybrid" in problem for problem in problems)


def test_check_catches_an_indirect_echo_hook(tmp_path):
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n/bin/echo live.py live-image.json getty@tty1\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_catches_a_comment_smuggled_launcher(tmp_path):
    _repo, image = _copy_definition_tree(tmp_path)
    launcher = image / "config/includes.chroot/usr/local/bin/beerus-live"
    launcher.write_text(
        "#!/bin/sh\n"
        "PYTHONPATH=/tmp # /opt/beerus\n"
        "export PYTHONPATH\n"
        "exec true # python3 -m beerus\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("PYTHONPATH" in problem for problem in problems)
    assert any("console" in problem for problem in problems)


def test_check_catches_a_launcher_that_never_exports(tmp_path):
    _repo, image = _copy_definition_tree(tmp_path)
    launcher = image / "config/includes.chroot/usr/local/bin/beerus-live"
    text = launcher.read_text(encoding="utf-8")
    launcher.write_text(
        text.replace("export PYTHONPATH\n", ""), encoding="utf-8"
    )
    problems = check_definition.check(image)
    assert any("export" in problem for problem in problems)


@pytest.mark.parametrize(
    "body",
    [
        "env FOO=1 echo live.py live-image.json getty@tty1",
        "command printf 'live.py live-image.json getty@tty1'",
        "/usr/bin/printf 'live.py live-image.json getty@tty1'",
        "/bin/true live.py live-image.json getty@tty1",
        "'echo' live.py live-image.json getty@tty1",
        "FOO=bar echo live.py live-image.json getty@tty1",
        "true; echo live.py live-image.json getty@tty1",
        "(echo live.py live-image.json getty@tty1)",
        "eval echo live.py live-image.json getty@tty1",
        "X='live.py live-image.json getty@tty1'",
    ],
)
def test_check_catches_hook_needles_only_printed_or_stored(
    tmp_path, body
):
    """Round-5 regression: the round-4 checker rewrite filtered
    by each line's first word, so needles carried by a wrapped,
    prefixed, compounded or merely stored line still passed.
    Commands are now analysed one by one — a needle counts only
    in a command that actually runs."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_catches_hook_needles_in_a_heredoc(tmp_path):
    """Round-5 regression: heredoc lines are a command's input,
    not commands — needles inside one prove nothing."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\ncat <<EOF\nlive.py live-image.json getty@tty1\nEOF\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_catches_flags_behind_a_semicolon_on_the_lb_line(tmp_path):
    """Round-5 regression: ';' ends the real lb config command —
    correct flags echoed after it are a different command and
    must not satisfy the label check."""
    _repo, image = _copy_definition_tree(tmp_path)
    build_sh = image / "build.sh"
    text = build_sh.read_text(encoding="utf-8")
    text = text.replace(
        f'--iso-volume "{LIVE_VOLUME_LABEL}"', '--iso-volume "OTHER"'
    )
    text = text.replace("--binary-images iso-hybrid", "--binary-images iso")
    text = text.replace(
        "--source false",
        "--source false; echo '--iso-volume \""
        + LIVE_VOLUME_LABEL
        + "\" --binary-images iso-hybrid'",
    )
    build_sh.write_text(text, encoding="utf-8")
    problems = check_definition.check(image)
    assert any("volume label" in problem for problem in problems)
    assert any("hybrid" in problem for problem in problems)


def test_check_catches_a_launcher_that_execs_something_else(tmp_path):
    """Round-5 regression: 'exec true python3 -m beerus' runs
    true, not the console — the exec check names the program
    actually exec'd, by token, not by substring."""
    _repo, image = _copy_definition_tree(tmp_path)
    launcher = image / "config/includes.chroot/usr/local/bin/beerus-live"
    launcher.write_text(
        "#!/bin/sh\n"
        "PYTHONPATH=/opt/beerus\n"
        "export PYTHONPATH\n"
        "exec true python3 -m beerus\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("console" in problem for problem in problems)


@pytest.mark.parametrize(
    "export_line", ["export FOO=PYTHONPATH", "export PYTHONPATHX=1"]
)
def test_check_catches_an_export_of_the_wrong_variable(
    tmp_path, export_line
):
    """Round-5 regression: PYTHONPATH appearing as another
    variable's value, or as a prefix of a longer name, exports
    nothing the console can use."""
    _repo, image = _copy_definition_tree(tmp_path)
    launcher = image / "config/includes.chroot/usr/local/bin/beerus-live"
    launcher.write_text(
        "#!/bin/sh\n"
        "PYTHONPATH=/opt/beerus\n"
        f"{export_line}\n"
        "exec python3 -m beerus\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("export" in problem for problem in problems)


@pytest.mark.parametrize(
    "body",
    [
        "xargs echo live.py live-image.json getty@tty1",
        "timeout 5 echo live.py live-image.json getty@tty1",
        "nohup echo live.py live-image.json getty@tty1",
        "bash -c 'echo live.py live-image.json getty@tty1'",
        "tee live.py live-image.json getty@tty1 </dev/null",
        "mkdir -p live.py live-image.json getty@tty1",
    ],
)
def test_check_catches_hook_needles_behind_heavier_wrappers(
    tmp_path, body
):
    """Round-6 regression: the round-5 re-audit smuggled the
    hook needles past the checker behind heavier wrappers, a
    nested shell, and bystander commands that carry the needles
    as plain arguments. The hook check is now semantic: the
    staged files count only where a command existence-tests them
    by their full paths, and getty@tty1 counts only where
    systemctl masks it."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_catches_a_hook_that_never_masks_getty(tmp_path):
    """Round-6 regression: systemctl merely looking at
    getty@tty1 is not masking it."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "[ -f /opt/beerus/beerus/live.py ] || exit 1\n"
        "[ -f /etc/beerus/live-image.json ] || exit 1\n"
        "systemctl status getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("getty@tty1" in problem for problem in problems)


def test_check_accepts_a_minimal_genuine_hook(tmp_path):
    """Positive control for the semantic hook check: a hook that
    does the three jobs in their minimal real form passes."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "[ -f /opt/beerus/beerus/live.py ] || exit 1\n"
        "[ -f /etc/beerus/live-image.json ] || exit 1\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("hook" in problem for problem in problems)


@pytest.mark.parametrize(
    "value",
    ["/opt/beerus_evil", "/tmp/opt/beerus", "/opt/beerus2:/tmp"],
)
def test_check_catches_a_pythonpath_that_only_mentions_the_dir(
    tmp_path, value
):
    """Round-6 regression: /opt/beerus must be a whole component
    of the PYTHONPATH value — a longer directory that merely
    starts with it, or a path that merely ends with it, puts
    nothing on the console's path."""
    _repo, image = _copy_definition_tree(tmp_path)
    launcher = image / "config/includes.chroot/usr/local/bin/beerus-live"
    launcher.write_text(
        "#!/bin/sh\n"
        f"PYTHONPATH={value}\n"
        "export PYTHONPATH\n"
        "exec python3 -m beerus\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("PYTHONPATH" in problem for problem in problems)


def test_check_catches_hook_tests_of_the_wrong_location(tmp_path):
    """Round-7 regression: existence-testing a same-named file in
    /tmp proves nothing about the staged console at
    /opt/beerus/beerus/live.py or the marker at
    /etc/beerus/live-image.json — the paths are anchored."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "[ -f /tmp/live.py ] || exit 1\n"
        "[ -f /tmp/live-image.json ] || exit 1\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_catches_a_mask_of_the_wrong_unit(tmp_path):
    """Round-7 regression: getty@tty12 is not getty@tty1 — the
    unit name is matched exactly, not as a substring."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "[ -f /opt/beerus/beerus/live.py ] || exit 1\n"
        "[ -f /etc/beerus/live-image.json ] || exit 1\n"
        "systemctl mask getty@tty12.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("getty@tty1" in problem for problem in problems)


def test_check_catches_hook_proofs_in_a_dead_branch(tmp_path):
    """Round-7 regression: a proof inside ``if false; then`` can
    never run — the checker marks dead branches and ignores
    them."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "if false; then\n"
        "[ -f /opt/beerus/beerus/live.py ] || exit 1\n"
        "[ -f /etc/beerus/live-image.json ] || exit 1\n"
        "systemctl mask getty@tty1.service\n"
        "fi\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_catches_hook_tests_whose_results_are_discarded(tmp_path):
    """Round-7 regression: ``test || true`` performs the test
    and throws the verdict away — the build would succeed with
    the files missing. A proof's failure must stop the hook."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "[ -f /opt/beerus/beerus/live.py ] || true\n"
        "[ -f /etc/beerus/live-image.json ] || true\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_catches_a_shadowed_pythonpath(tmp_path):
    """Round-7 regression: a later assignment wins — setting
    PYTHONPATH correctly and then overwriting it before the
    exec leaves the console with the wrong value."""
    _repo, image = _copy_definition_tree(tmp_path)
    launcher = image / "config/includes.chroot/usr/local/bin/beerus-live"
    launcher.write_text(
        "#!/bin/sh\n"
        "PYTHONPATH=/opt/beerus\n"
        "export PYTHONPATH\n"
        "PYTHONPATH=/tmp\n"
        "exec python3 -m beerus\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("PYTHONPATH" in problem for problem in problems)


def test_check_catches_a_pythonpath_set_in_a_nested_shell(tmp_path):
    """Round-7 regression: assignments and exports inside a
    ``bash -c`` die with that shell — the launcher is judged on
    its own scope, in order."""
    _repo, image = _copy_definition_tree(tmp_path)
    launcher = image / "config/includes.chroot/usr/local/bin/beerus-live"
    launcher.write_text(
        "#!/bin/sh\n"
        "bash -c 'PYTHONPATH=/opt/beerus; export PYTHONPATH'\n"
        "exec python3 -m beerus\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("PYTHONPATH" in problem for problem in problems)


def test_check_catches_a_prefix_only_assignment(tmp_path):
    """Round-7 regression: ``PYTHONPATH=/opt/beerus true``
    assigns only for that one command — it does not persist to
    the exec."""
    _repo, image = _copy_definition_tree(tmp_path)
    launcher = image / "config/includes.chroot/usr/local/bin/beerus-live"
    launcher.write_text(
        "#!/bin/sh\n"
        "PYTHONPATH=/opt/beerus true\n"
        "export PYTHONPATH\n"
        "exec python3 -m beerus\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("PYTHONPATH" in problem for problem in problems)


@pytest.mark.parametrize(
    "condition", ["! true", "[ 1 -eq 2 ]"]
)
def test_check_catches_hook_proofs_behind_a_false_condition(
    tmp_path, condition
):
    """Round-8 regression: a constant-false condition is dead
    code however it is spelled — negated, or a literal test
    whose answer is fixed. The checker evaluates both."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        f"if {condition}; then\n"
        "[ -f /opt/beerus/beerus/live.py ] || exit 1\n"
        "[ -f /etc/beerus/live-image.json ] || exit 1\n"
        "systemctl mask getty@tty1.service\n"
        "fi\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_catches_a_set_e_that_comes_too_late(tmp_path):
    """Round-8 regression: ``set -e`` switched on AFTER the
    tests were run cannot enforce them — enforcement is judged
    in order, and ``set +e`` switches it back off."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "set -e\n"
        "set +e\n"
        "test -f /opt/beerus/beerus/live.py\n"
        "test -f /etc/beerus/live-image.json\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_catches_a_subshell_exit_offered_as_enforcement(tmp_path):
    """Round-8 regression: ``|| (exit 1)`` exits a subshell, not
    the hook — the build would carry on with the files missing."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "[ -f /opt/beerus/beerus/live.py ] || (exit 1)\n"
        "[ -f /etc/beerus/live-image.json ] || (exit 1)\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_catches_hook_proofs_in_an_uncalled_function(tmp_path):
    """Round-8 regression: a function body that is never called
    executes nothing — its proofs count for nothing."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "prove() {\n"
        "[ -f /opt/beerus/beerus/live.py ] || exit 1\n"
        "[ -f /etc/beerus/live-image.json ] || exit 1\n"
        "systemctl mask getty@tty1.service\n"
        "}\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_accepts_hook_proofs_in_a_called_function(tmp_path):
    """Positive control: the same body, actually called, proves
    exactly what the inline form proves."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "prove() {\n"
        "[ -f /opt/beerus/beerus/live.py ] || exit 1\n"
        "[ -f /etc/beerus/live-image.json ] || exit 1\n"
        "systemctl mask getty@tty1.service\n"
        "}\n"
        "prove\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("hook" in problem for problem in problems)


def test_check_catches_an_exec_behind_a_false_condition(tmp_path):
    """Round-8 regression: an exec inside a constant-false
    branch never runs — the console would never start."""
    _repo, image = _copy_definition_tree(tmp_path)
    launcher = image / "config/includes.chroot/usr/local/bin/beerus-live"
    launcher.write_text(
        "#!/bin/sh\n"
        "PYTHONPATH=/opt/beerus\n"
        "export PYTHONPATH\n"
        "if [ 1 -eq 2 ]; then\n"
        "exec python3 -m beerus\n"
        "fi\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("console" in problem for problem in problems)


def test_check_catches_launcher_setup_in_an_uncalled_function(tmp_path):
    """Round-8 regression: assignments and exports inside a
    function the launcher never calls leave the exec without a
    PYTHONPATH."""
    _repo, image = _copy_definition_tree(tmp_path)
    launcher = image / "config/includes.chroot/usr/local/bin/beerus-live"
    launcher.write_text(
        "#!/bin/sh\n"
        "setup() { PYTHONPATH=/opt/beerus; export PYTHONPATH; }\n"
        "exec python3 -m beerus\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("PYTHONPATH" in problem for problem in problems)


def test_check_catches_hook_proofs_behind_an_arithmetic_condition(tmp_path):
    """Round-9 regression: ``if (( 1 == 2 ))`` is a constant-
    false condition whose parentheses the segmenter splits —
    the checker joins the condition back up, evaluates literal
    arithmetic, and treats a condition it cannot evaluate as
    untrusted (its branch proves nothing)."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "if (( 1 == 2 )); then\n"
        "[ -f /opt/beerus/beerus/live.py ] || exit 1\n"
        "[ -f /etc/beerus/live-image.json ] || exit 1\n"
        "systemctl mask getty@tty1.service\n"
        "fi\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_catches_hook_proofs_in_a_nonmatching_case_arm(tmp_path):
    """Round-9 regression: a ``case`` arm whose pattern cannot
    match the subject never runs — the checker tracks arm
    reachability for literal subjects."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "case 1 in\n"
        "2)\n"
        "[ -f /opt/beerus/beerus/live.py ] || exit 1\n"
        "[ -f /etc/beerus/live-image.json ] || exit 1\n"
        "systemctl mask getty@tty1.service\n"
        ";;\n"
        "esac\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_accepts_hook_proofs_in_a_matching_case_arm(tmp_path):
    """Positive control: the matching arm runs, and its proofs
    count in full."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "case 1 in\n"
        "1)\n"
        "[ -f /opt/beerus/beerus/live.py ] || exit 1\n"
        "[ -f /etc/beerus/live-image.json ] || exit 1\n"
        "systemctl mask getty@tty1.service\n"
        ";;\n"
        "esac\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("hook" in problem for problem in problems)


def test_check_catches_a_function_called_only_in_a_subshell(tmp_path):
    """Round-9 regression: ``(prove)`` runs the body in a
    subshell — its exits cannot stop the hook, so its file
    proofs are unproven even though the body runs."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "prove() {\n"
        "[ -f /opt/beerus/beerus/live.py ] || exit 1\n"
        "[ -f /etc/beerus/live-image.json ] || exit 1\n"
        "}\n"
        "(prove)\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_accepts_a_subshell_test_with_a_top_level_exit(tmp_path):
    """Positive control: ``( test ) || exit 1`` — the subshell's
    status reaches the chain, and the exit itself is at the top
    level, so a missing file genuinely stops the hook."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "( [ -f /opt/beerus/beerus/live.py ] ) || exit 1\n"
        "( [ -f /etc/beerus/live-image.json ] ) || exit 1\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("hook" in problem for problem in problems)


def test_check_accepts_errexit_set_inside_a_called_function(tmp_path):
    """Positive control: ``set -e`` inside a function the hook
    actually calls persists for the tests that follow it."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "arm() { set -e; }\n"
        "arm\n"
        "test -f /opt/beerus/beerus/live.py\n"
        "test -f /etc/beerus/live-image.json\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("hook" in problem for problem in problems)


def test_check_catches_launcher_setup_called_only_in_a_subshell(tmp_path):
    """Round-9 regression: ``(setup)`` runs the body in a
    subshell — its assignments die there and never reach the
    exec."""
    _repo, image = _copy_definition_tree(tmp_path)
    launcher = image / "config/includes.chroot/usr/local/bin/beerus-live"
    launcher.write_text(
        "#!/bin/sh\n"
        "setup() { PYTHONPATH=/opt/beerus; export PYTHONPATH; }\n"
        "(setup)\n"
        "exec python3 -m beerus\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("PYTHONPATH" in problem for problem in problems)


def test_check_catches_launcher_setup_called_after_the_exec(tmp_path):
    """Round-9 regression: a call placed after the exec never
    happens — exec replaces the shell. Bodies take effect only
    at call sites that precede the exec."""
    _repo, image = _copy_definition_tree(tmp_path)
    launcher = image / "config/includes.chroot/usr/local/bin/beerus-live"
    launcher.write_text(
        "#!/bin/sh\n"
        "setup() { PYTHONPATH=/opt/beerus; export PYTHONPATH; }\n"
        "exec python3 -m beerus\n"
        "setup\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("PYTHONPATH" in problem for problem in problems)


def test_check_accepts_launcher_setup_via_a_called_function(tmp_path):
    """Positive control: a function called at the top level
    before the exec sets the launcher up exactly as inline
    statements would."""
    _repo, image = _copy_definition_tree(tmp_path)
    launcher = image / "config/includes.chroot/usr/local/bin/beerus-live"
    launcher.write_text(
        "#!/bin/sh\n"
        "setup() { PYTHONPATH=/opt/beerus; export PYTHONPATH; }\n"
        "setup\n"
        "exec python3 -m beerus\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("PYTHONPATH" in problem for problem in problems)
    assert not any("console" in problem for problem in problems)


_PROVE_FUNCTION = (
    "prove() {\n"
    "[ -f /opt/beerus/beerus/live.py ] || exit 1\n"
    "[ -f /etc/beerus/live-image.json ] || exit 1\n"
    "systemctl mask getty@tty1.service\n"
    "}\n"
)


@pytest.mark.parametrize(
    "guard", ["[ 1 -eq 2 ] && prove", "[ 1 -eq 1 ] || prove"]
)
def test_check_catches_hook_proofs_behind_a_constant_guard(
    tmp_path, guard
):
    """Round-10 regression: a provably-constant ``&&``/``||``
    guard is a dead branch spelled sideways — the checker
    judges the guarded command by the guard's evaluated value,
    exactly as it judges an ``if`` condition."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        f"#!/bin/sh\n{_PROVE_FUNCTION}{guard}\n", encoding="utf-8"
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_accepts_hook_proofs_behind_a_guard_that_passes(tmp_path):
    """Positive control: ``[ 1 -eq 2 ] || prove`` — the guard
    fails, so the call runs and its proofs count in full."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        f"#!/bin/sh\n{_PROVE_FUNCTION}[ 1 -eq 2 ] || prove\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("hook" in problem for problem in problems)


_SETUP_FUNCTION = "setup() { PYTHONPATH=/opt/beerus; export PYTHONPATH; }\n"


@pytest.mark.parametrize(
    "guard",
    [
        "[ 1 -eq 2 ] && setup",
        "[ 1 -eq 1 ] || setup",
        "test 1 -eq 2 && setup",
        "! true && setup",
        "(( 1 == 2 )) && setup",
    ],
)
def test_check_catches_launcher_setup_behind_a_constant_guard(
    tmp_path, guard
):
    """Round-10 regression: the same constant-guard family in
    the launcher — the call never runs, so the setup never
    happens, whatever shape the guard takes."""
    _repo, image = _copy_definition_tree(tmp_path)
    launcher = image / "config/includes.chroot/usr/local/bin/beerus-live"
    launcher.write_text(
        f"#!/bin/sh\n{_SETUP_FUNCTION}{guard}\nexec python3 -m beerus\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("PYTHONPATH" in problem for problem in problems)


def test_check_accepts_launcher_setup_behind_a_guard_that_passes(tmp_path):
    """Positive control: ``true && setup`` runs the call, and
    the setup counts."""
    _repo, image = _copy_definition_tree(tmp_path)
    launcher = image / "config/includes.chroot/usr/local/bin/beerus-live"
    launcher.write_text(
        f"#!/bin/sh\n{_SETUP_FUNCTION}true && setup\nexec python3 -m beerus\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("PYTHONPATH" in problem for problem in problems)


def test_check_catches_an_lb_build_behind_a_constant_guard(tmp_path):
    """Round-10 regression: the guard family reaches build.sh
    too — the build command itself parked behind a guard that
    never lets it run."""
    _repo, image = _copy_definition_tree(tmp_path)
    build_sh = image / "build.sh"
    text = build_sh.read_text(encoding="utf-8")
    build_sh.write_text(
        text.replace("lb build", "[ 1 -eq 2 ] && lb build"),
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("lb build" in problem for problem in problems)


@pytest.mark.parametrize(
    "guard",
    [
        "false && true && prove",
        "[ 1 -eq 2 ] && true && prove",
        "true || false || prove",
    ],
)
def test_check_catches_a_resurrected_chain(tmp_path, guard):
    """Round-11 regression: a skipped command leaves the chain
    status untouched — ``false && true && prove`` never runs
    prove, because the dead ``true`` does not resurrect the
    chain with its own value."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        f"#!/bin/sh\n{_PROVE_FUNCTION}{guard}\n", encoding="utf-8"
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


@pytest.mark.parametrize(
    "chain", ["false && prove || prove", "true || prove && prove"]
)
def test_check_accepts_a_chain_that_really_reaches_the_call(
    tmp_path, chain
):
    """Positive control for chain propagation in the other
    direction: the final call genuinely runs in both chains —
    the first through the ``||``, the second through the
    ``&&``."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        f"#!/bin/sh\n{_PROVE_FUNCTION}{chain}\n", encoding="utf-8"
    )
    problems = check_definition.check(image)
    assert not any("hook" in problem for problem in problems)


def test_check_catches_launcher_setup_behind_a_resurrected_chain(tmp_path):
    """Round-11 regression: the chain family in the launcher —
    ``false && true && setup`` never calls setup."""
    _repo, image = _copy_definition_tree(tmp_path)
    launcher = image / "config/includes.chroot/usr/local/bin/beerus-live"
    launcher.write_text(
        f"#!/bin/sh\n{_SETUP_FUNCTION}false && true && setup\n"
        "exec python3 -m beerus\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("PYTHONPATH" in problem for problem in problems)


def test_check_catches_an_lb_build_behind_a_resurrected_chain(tmp_path):
    """Round-11 regression: the chain family in build.sh —
    ``false && true && lb build`` never builds."""
    _repo, image = _copy_definition_tree(tmp_path)
    build_sh = image / "build.sh"
    text = build_sh.read_text(encoding="utf-8")
    build_sh.write_text(
        text.replace("lb build", "false && true && lb build"),
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("lb build" in problem for problem in problems)


@pytest.mark.parametrize(
    "chain",
    ["false &&\ntrue && prove", "[ 1 -eq 2 ] &&\nprove"],
)
def test_check_catches_a_chain_split_across_lines(tmp_path, chain):
    """Round-12 regression: a newline after ``&&`` continues
    the chain in shell — the checker carries the operator (and
    the chain status) across the line break instead of
    restarting the command unconditionally."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        f"#!/bin/sh\n{_PROVE_FUNCTION}{chain}\n", encoding="utf-8"
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_catches_launcher_setup_behind_a_split_chain(tmp_path):
    """Round-12 regression: the split chain in the launcher —
    ``false &&`` on one line, ``true && setup`` on the next,
    still never calls setup."""
    _repo, image = _copy_definition_tree(tmp_path)
    launcher = image / "config/includes.chroot/usr/local/bin/beerus-live"
    launcher.write_text(
        f"#!/bin/sh\n{_SETUP_FUNCTION}false &&\ntrue && setup\n"
        "exec python3 -m beerus\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("PYTHONPATH" in problem for problem in problems)


def test_check_catches_hook_proofs_called_into_a_pipeline(tmp_path):
    """Round-12 regression: a function called into a pipeline
    runs in a subshell — its exits cannot stop the hook, the
    pipe spelling of the subshell-call defect."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "prove() {\n"
        "[ -f /opt/beerus/beerus/live.py ] || exit 1\n"
        "[ -f /etc/beerus/live-image.json ] || exit 1\n"
        "}\n"
        "prove | cat\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_catches_a_piped_test_under_set_e(tmp_path):
    """Round-12 regression: ``test -f … | cat`` under ``set -e``
    — the pipeline's status is cat's, so the test's failure is
    invisible and enforces nothing."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "set -e\n"
        "test -f /opt/beerus/beerus/live.py | cat\n"
        "test -f /etc/beerus/live-image.json\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_accepts_a_piped_mask(tmp_path):
    """Positive control: masking is an external side effect —
    ``systemctl mask … | cat`` really masks the unit, and the
    checker credits it."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "[ -f /opt/beerus/beerus/live.py ] || exit 1\n"
        "[ -f /etc/beerus/live-image.json ] || exit 1\n"
        "systemctl mask getty@tty1.service | cat\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("hook" in problem for problem in problems)


def test_check_accepts_a_last_component_test(tmp_path):
    """Positive control: a test that is the pipeline's last
    component contributes the pipeline's status, so
    ``… | test -f … || exit 1`` genuinely enforces."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "true | test -f /opt/beerus/beerus/live.py || exit 1\n"
        "true | test -f /etc/beerus/live-image.json || exit 1\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("hook" in problem for problem in problems)


@pytest.mark.parametrize(
    "spelling",
    [
        # A test credited only via set -e is NOT enforced when
        # it is a non-final && member, is backgrounded, or is
        # negated — set -e exempts all three (shell-verified).
        "test -f /opt/beerus/beerus/live.py && echo ok",
        "test -f /opt/beerus/beerus/live.py && true",
        "[ -f /opt/beerus/beerus/live.py ] && echo ok",
        "true | test -f /opt/beerus/beerus/live.py && echo ok",
        "true | test -f /opt/beerus/beerus/live.py &&\necho ok",
        "test -f /opt/beerus/beerus/live.py &",
        "true | test -f /opt/beerus/beerus/live.py &",
        "! test -f /opt/beerus/beerus/live.py",
        "! [ -f /opt/beerus/beerus/live.py ]",
        "! true | test -f /opt/beerus/beerus/live.py",
    ],
)
def test_check_catches_tests_exempt_from_errexit(tmp_path, spelling):
    """Round-13 regression: appending ``&& …`` (or ``&``, or
    prefixing ``!``) to a test whose only enforcement was
    ``set -e`` silently unenforces it — a one-token respelling
    of the piped-test defect. The second file keeps a genuine
    standalone test under the same ``set -e``, so only the
    respelled first test can be at fault."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "set -e\n"
        f"{spelling}\n"
        "test -f /etc/beerus/live-image.json\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_catches_a_backgrounded_or_exit(tmp_path):
    """Round-13 regression: ``test || exit 1 &`` backgrounds the
    whole chain — the exit only ever leaves a subshell, so the
    hook itself runs on. Same family as the errexit exemptions."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "test -f /opt/beerus/beerus/live.py || exit 1 &\n"
        "test -f /etc/beerus/live-image.json || exit 1\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_accepts_a_chain_that_reaches_an_or_exit(tmp_path):
    """Positive control: ``test && echo ok || exit 1`` really
    does stop the hook when the file is missing — the test's
    failure skips the echo and the chain's status reaches the
    ``|| exit``. It is credited for the exit, not for set -e
    (there is none here)."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "test -f /opt/beerus/beerus/live.py && echo ok || exit 1\n"
        "test -f /etc/beerus/live-image.json && echo ok || exit 1\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("hook" in problem for problem in problems)


@pytest.mark.parametrize(
    "spelling",
    [
        # Round-14 regressions: the errexit exemptions reached
        # through a GROUP — the operator sits on the group's
        # close, not beside the test (context propagation).
        "( test -f /opt/beerus/beerus/live.py ) && echo ok",
        "{ test -f /opt/beerus/beerus/live.py; } && echo ok",
        "( test -f /opt/beerus/beerus/live.py ) | cat",
        "{ test -f /opt/beerus/beerus/live.py; } | cat",
        "( test -f /opt/beerus/beerus/live.py ) &",
        "{ test -f /opt/beerus/beerus/live.py; } &",
        "( test -f /opt/beerus/beerus/live.py ) || true",
        # Negation one token further away than the test.
        "{ ! test -f /opt/beerus/beerus/live.py; }",
        "! ( test -f /opt/beerus/beerus/live.py )",
        # A test inside command substitution used as an
        # argument: its failure is data, never a status.
        "echo $(test -f /opt/beerus/beerus/live.py)",
        # Negation inside the test expression itself.
        "test ! -f /opt/beerus/beerus/live.py",
        "[ ! -f /opt/beerus/beerus/live.py ]",
    ],
)
def test_check_catches_grouped_tests_exempt_from_errexit(
    tmp_path, spelling
):
    """Round-14 regression: round 13 closed the segment-local
    spellings; these carry the same exemptions on a neighbouring
    segment — a group's closing operator, a ``!`` shifted one
    token, a substitution. The checker propagates statement
    context (effective tail, statement negation) instead of
    judging the test segment alone."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "set -e\n"
        f"{spelling}\n"
        "test -f /etc/beerus/live-image.json\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_catches_an_if_negation_with_the_exit_in_the_else(
    tmp_path,
):
    """Round-14 regression: ``if ! test …`` takes the THEN
    branch when the file is missing — an exit in the else
    enforces nothing. (The checker previously read negation
    from the stripped tokens, where the leading ``!`` no
    longer exists, and credited the else-exit.)"""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "if ! test -f /opt/beerus/beerus/live.py\n"
        "then :\n"
        "else\n"
        "exit 1\n"
        "fi\n"
        "test -f /etc/beerus/live-image.json || exit 1\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_accepts_an_if_negation_with_the_exit_in_the_then(
    tmp_path,
):
    """Positive control: the genuine converse — the exit sits
    in the branch the missing file actually takes."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "if ! test -f /opt/beerus/beerus/live.py\n"
        "then\n"
        "exit 1\n"
        "fi\n"
        "if ! test -f /etc/beerus/live-image.json; then exit 1; fi\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("hook" in problem for problem in problems)


@pytest.mark.parametrize(
    "call",
    [
        "prove && echo ok",
        "prove || true",
        "! prove",
        "prove &",
        "if prove; then echo ok; fi",
        "{ prove; } && echo ok",
    ],
)
def test_check_catches_function_proofs_called_in_exempt_contexts(
    tmp_path, call
):
    """Round-14 regression: a body whose proofs rest on
    ``set -e`` holds only where a call runs it with errexit in
    force. In every exempt call context the shell switches
    ``set -e`` OFF inside the body — the exemption is inherited
    at the call site, so the body counts for nothing."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "set -e\n"
        "prove() {\n"
        "test -f /opt/beerus/beerus/live.py\n"
        "test -f /etc/beerus/live-image.json\n"
        "}\n"
        f"{call}\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_accepts_set_e_proofs_in_a_plainly_called_function(
    tmp_path,
):
    """Positive control: the same body, called plainly AFTER
    ``set -e`` — errexit is judged at the call site (where the
    body runs), not at the definition, so this proves."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "prove() {\n"
        "test -f /opt/beerus/beerus/live.py\n"
        "test -f /etc/beerus/live-image.json\n"
        "}\n"
        "set -e\n"
        "prove\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("hook" in problem for problem in problems)


def test_check_catches_an_exit_that_only_leaves_a_piped_group(
    tmp_path,
):
    """Round-14 regression: ``test || { exit 1; } | cat`` — the
    exit is a pipeline component inside a brace group; it
    leaves only that subshell, and the hook runs on."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "test -f /opt/beerus/beerus/live.py || { exit 1; } | cat\n"
        "test -f /etc/beerus/live-image.json || exit 1\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_catches_launcher_state_set_in_a_subshell(tmp_path):
    """Round-14 regression: assignments and exports inside
    ``( … )`` die with the child shell — the launcher's own
    PYTHONPATH is never set, whatever the subshell did."""
    _repo, image = _copy_definition_tree(tmp_path)
    launcher = image / "config/includes.chroot/usr/local/bin/beerus-live"
    launcher.write_text(
        "#!/bin/sh\n"
        "( PYTHONPATH=/opt/beerus; export PYTHONPATH )\n"
        "exec python3 -m beerus\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("launcher" in problem for problem in problems)


def test_check_catches_a_backgrounded_setup_call(tmp_path):
    """Round-14 regression: ``setup &`` runs the body in a
    background subshell — its exports never reach the exec."""
    _repo, image = _copy_definition_tree(tmp_path)
    launcher = image / "config/includes.chroot/usr/local/bin/beerus-live"
    launcher.write_text(
        "#!/bin/sh\n"
        "setup() {\n"
        "PYTHONPATH=/opt/beerus\n"
        "export PYTHONPATH\n"
        "}\n"
        "setup &\n"
        "exec python3 -m beerus\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("launcher" in problem for problem in problems)


def test_check_catches_a_backgrounded_lb_build(tmp_path):
    """Round-14 regression: ``lb build &`` races every step
    after it (the checksum above all) — a backgrounded build
    does not count as the build step being done in order."""
    _repo, image = _copy_definition_tree(tmp_path)
    build_sh = image / "build.sh"
    text = build_sh.read_text(encoding="utf-8")
    build_sh.write_text(
        text.replace("lb build", "lb build &"), encoding="utf-8"
    )
    problems = check_definition.check(image)
    assert any("lb build" in problem for problem in problems)


@pytest.mark.parametrize(
    "call",
    [
        "command prove",
        "env prove",
        "nice prove",
        "nohup prove",
        "time prove",
        "timeout 5 prove",
    ],
)
def test_check_catches_wrapper_prefixed_calls(tmp_path, call):
    """Round-16 regression: a wrapper prefix suppresses the
    shell's function lookup — ``command prove`` and friends
    run an external program (or nothing), never the body. The
    segmenter's effective tokens strip the wrapper, so the
    checker recognises a call only when the RAW head is the
    function name itself (or ``eval``, which really does run
    its argument as shell input)."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "set -e\n"
        "prove() {\n"
        "test -f /opt/beerus/beerus/live.py\n"
        "test -f /etc/beerus/live-image.json\n"
        "}\n"
        f"{call}\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_accepts_an_eval_call(tmp_path):
    """Positive control: ``eval prove`` executes its argument
    as shell input in the current shell — the body really
    runs, with its call-site context."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "set -e\n"
        "prove() {\n"
        "test -f /opt/beerus/beerus/live.py\n"
        "test -f /etc/beerus/live-image.json\n"
        "}\n"
        "eval prove\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("hook" in problem for problem in problems)


@pytest.mark.parametrize(
    "call", ["command setup", "env setup", "timeout 5 setup"]
)
def test_check_catches_wrapper_prefixed_launcher_calls(
    tmp_path, call
):
    """Round-16 regression, launcher form: the wrapper runs an
    external program while the exec still fires — with a real
    python3 the console would start WITHOUT the PYTHONPATH
    the body was supposed to set (a full bypass, verified
    against a logging python3 stand-in)."""
    _repo, image = _copy_definition_tree(tmp_path)
    launcher = image / "config/includes.chroot/usr/local/bin/beerus-live"
    launcher.write_text(
        "#!/bin/sh\n"
        "setup() {\n"
        "PYTHONPATH=/opt/beerus\n"
        "export PYTHONPATH\n"
        "}\n"
        f"{call}\n"
        "exec python3 -m beerus\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("launcher" in problem for problem in problems)


@pytest.mark.parametrize(
    "arming",
    ["{ set -e; } | cat", "{ set -e; } &", "{ arm; } | cat"],
)
def test_check_catches_errexit_armed_in_a_dead_group(
    tmp_path, arming
):
    """Round-16 regression: a ``set -e`` that runs inside a
    piped or backgrounded group runs in a child shell and
    dies with it — the tests after it are unenforced. (The
    paren form was already refused; the brace form was not.)
    """
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "arm() { set -e; }\n"
        f"{arming}\n"
        "test -f /opt/beerus/beerus/live.py\n"
        "test -f /etc/beerus/live-image.json\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


@pytest.mark.parametrize(
    "spelling",
    [
        "{ ( test -f /opt/beerus/beerus/live.py ); } &",
        "( test -f /opt/beerus/beerus/live.py; true ) | cat",
        "( test -f /opt/beerus/beerus/live.py; true ) && echo ok",
        "{ test -f /opt/beerus/beerus/live.py; true; } && echo ok",
    ],
)
def test_check_catches_nonfinal_tests_in_exempt_groups(
    tmp_path, spelling
):
    """Round-16 regression: a test that is not the last
    command in its group contributes nothing to the group's
    status — what governs it is the GROUP's position, and a
    group in an exempt position (piped, backgrounded, a
    non-final chain member) has errexit off inside. The same
    shapes standalone are genuine and stay accepted."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "set -e\n"
        f"{spelling}\n"
        "test -f /etc/beerus/live-image.json\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_catches_a_multiline_paren_group_in_a_chain(tmp_path):
    """Round-16 regression (the N1 family): a paren group
    spread across lines — ``(`` ⏎ ``test …`` ⏎ ``) && echo``.
    The close lands on an empty part of its own line; the
    segmenter now records it on the group's last command."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "set -e\n"
        "(\n"
        "test -f /opt/beerus/beerus/live.py\n"
        ") && echo ok\n"
        "test -f /etc/beerus/live-image.json\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_accepts_a_multiline_paren_group_standalone(tmp_path):
    """Positive control: the same multi-line group as a plain
    statement under ``set -e`` really is enforced."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "set -e\n"
        "(\n"
        "test -f /opt/beerus/beerus/live.py\n"
        ")\n"
        "test -f /etc/beerus/live-image.json\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("hook" in problem for problem in problems)


def test_check_catches_a_layered_negation(tmp_path):
    """Round-16 regression: ``! test ! -f …`` — the composed
    truth is beside the point; a leading ``!`` exempts the
    statement from ``set -e`` however the inner expression
    reads, so the statement can never stop the hook."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "set -e\n"
        "! test ! -f /opt/beerus/beerus/live.py\n"
        "test -f /etc/beerus/live-image.json\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


@pytest.mark.parametrize("header", ["for i in; do", "for i; do"])
def test_check_catches_proofs_in_an_empty_for_loop(tmp_path, header):
    """Round-16 regression: a for loop over an empty list
    runs its body zero times (the bare form iterates the
    positional parameters, and hooks run with none). Constant
    ``while``/``until`` conditions were already evaluated;
    the empty ``for`` list now is too."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        f"{header} test -f /opt/beerus/beerus/live.py || exit 1; done\n"
        "test -f /etc/beerus/live-image.json || exit 1\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_catches_a_redefined_functions_strong_body(tmp_path):
    """Round-16 regression: the shell runs the LAST
    definition — a strong body shadowed by a weak
    redefinition proves nothing."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "prove() {\n"
        "test -f /opt/beerus/beerus/live.py || exit 1\n"
        "test -f /etc/beerus/live-image.json || exit 1\n"
        "}\n"
        "prove() {\n"
        "true\n"
        "}\n"
        "prove\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_accepts_a_redefinition_that_ends_strong(tmp_path):
    """Positive control: weak first, strong last — the last
    definition is the one that runs, and it proves."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "prove() {\n"
        "true\n"
        "}\n"
        "prove() {\n"
        "test -f /opt/beerus/beerus/live.py || exit 1\n"
        "test -f /etc/beerus/live-image.json || exit 1\n"
        "}\n"
        "prove\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("hook" in problem for problem in problems)


def test_check_catches_errexit_switched_off_before_the_call(
    tmp_path,
):
    """Round-16 regression: errexit for a body is judged at
    the call — ``set -e`` before the definition does not
    rescue a call made after ``set +e`` (and the definition's
    own name segment is not a call)."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "set -e\n"
        "prove() {\n"
        "test -f /opt/beerus/beerus/live.py\n"
        "test -f /etc/beerus/live-image.json\n"
        "}\n"
        "set +e\n"
        "prove\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


@pytest.mark.parametrize(
    "mask_line",
    [
        "systemctl mask getty@tty1.service &",
        "{ systemctl mask getty@tty1.service; } &",
    ],
)
def test_check_catches_a_backgrounded_mask(tmp_path, mask_line):
    """Round-16 regression: a backgrounded mask's effect is
    unsequenced — the hook can finish before it lands. A PIPED
    mask still counts (it runs to completion synchronously);
    only the background form is refused, matching lb build."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "test -f /opt/beerus/beerus/live.py || exit 1\n"
        "test -f /etc/beerus/live-image.json || exit 1\n"
        f"{mask_line}\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("getty" in problem for problem in problems)


def test_check_catches_a_piped_lb_build(tmp_path):
    """Round-16 regression: ``lb build | cat`` discards the
    build's own status — the checksum step after it reads the
    pipeline's status and could bless a stale image."""
    _repo, image = _copy_definition_tree(tmp_path)
    build_sh = image / "build.sh"
    text = build_sh.read_text(encoding="utf-8")
    build_sh.write_text(
        text.replace("lb build", "lb build | cat"),
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("lb build" in problem for problem in problems)


@pytest.mark.parametrize(
    "group",
    [
        "{ test -f /opt/beerus/beerus/live.py; true; } && echo ok",
        "{ test -f /opt/beerus/beerus/live.py; true; } | cat",
        "{ test -f /opt/beerus/beerus/live.py; true; } &",
        "{ test -f /opt/beerus/beerus/live.py; true; } || exit 1",
    ],
)
def test_check_catches_brace_groups_in_function_bodies(
    tmp_path, group
):
    """Round-18 regression: brace groups inside a function
    body were invisible to the group-tail logic — the scan
    stopped at the body's own braces, so the B4 family lived
    on one composition deeper, inside the body. The scan is
    now bounded by the body's opening brace instead of
    refusing to look."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "prove() {\n"
        f"{group}\n"
        "test -f /etc/beerus/live-image.json\n"
        "}\n"
        "set -e\n"
        "prove\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_accepts_a_standalone_brace_group_in_a_body(tmp_path):
    """Positive control: the same group as a plain statement
    in the body really is enforced (shell-verified)."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "prove() {\n"
        "{ test -f /opt/beerus/beerus/live.py; true; }\n"
        "test -f /etc/beerus/live-image.json\n"
        "}\n"
        "set -e\n"
        "prove\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("hook" in problem for problem in problems)


def test_check_catches_an_eval_redefinition(tmp_path):
    """Round-18 regression: ``eval 'prove() { true; }'`` after
    the static definition replaces the body with text the
    checker cannot see — the static body's proofs count for
    nothing (the round-16 eval credit, turned on B8)."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "prove() {\n"
        "test -f /opt/beerus/beerus/live.py || exit 1\n"
        "test -f /etc/beerus/live-image.json || exit 1\n"
        "}\n"
        "eval 'prove() { true; }'\n"
        "prove\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_catches_an_eval_wrapped_wrapper_call(tmp_path):
    """Round-18 regression, launcher form: ``eval command
    setup`` — eval is transparent, so the wrapper INSIDE the
    eval suppresses the function exactly as it does outside;
    crediting the eval head without reading its argument
    reopened B1 (verified against a logging python3: the
    console started with PYTHONPATH empty)."""
    _repo, image = _copy_definition_tree(tmp_path)
    launcher = image / "config/includes.chroot/usr/local/bin/beerus-live"
    launcher.write_text(
        "#!/bin/sh\n"
        "setup() {\n"
        "PYTHONPATH=/opt/beerus\n"
        "export PYTHONPATH\n"
        "}\n"
        "eval command setup\n"
        "exec python3 -m beerus\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("launcher" in problem for problem in problems)


@pytest.mark.parametrize(
    "header",
    [
        "for i in $EMPTY; do",
        'for i in "$@"; do',
        'for i in "$EMPTY"; do',
    ],
)
def test_check_catches_proofs_in_an_unresolvable_for_loop(
    tmp_path, header
):
    """Round-18 regression: a for list of expansions only may
    be empty at runtime — the checker cannot know the values,
    so it does not credit the body (the same policy as a
    command-substitution list, which was already refused;
    literal and glob lists still prove, as dash always
    iterates those at least once). The quoted form is
    refused conservatively too, though a quoted empty
    expansion happens to iterate once — the checker does
    not grade expansions by their quoting."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        f"{header} test -f /opt/beerus/beerus/live.py || exit 1; done\n"
        "test -f /etc/beerus/live-image.json || exit 1\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_accepts_a_for_loop_with_one_literal_word(tmp_path):
    """Positive control (round-19 refinement): ``for i in
    1 $EXTRA`` — the literal word guarantees at least one
    iteration whatever the expansion does, so the body
    really is enforced (shell-verified)."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "for i in 1 $EXTRA; do "
        "test -f /opt/beerus/beerus/live.py || exit 1; done\n"
        "test -f /etc/beerus/live-image.json || exit 1\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("hook" in problem for problem in problems)


@pytest.mark.parametrize(
    "eval_line",
    [
        # Round-19 regressions: the round-18 eval kill was a
        # literal top-level text match; every indirection
        # around it evaded it. Now any eval that is not a
        # plain visible call poisons function-body credit at
        # its execution point.
        "E='prove() { true; }'\neval \"$E\"",
        "eval prove\\(\\) \\{ true\\; \\}",
        "eval $(echo \"prove() { true; }\")",
        "command eval 'prove() { true; }'",
    ],
)
def test_check_catches_indirect_eval_redefinitions(
    tmp_path, eval_line
):
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "prove() {\n"
        "test -f /opt/beerus/beerus/live.py || exit 1\n"
        "test -f /etc/beerus/live-image.json || exit 1\n"
        "}\n"
        f"{eval_line}\n"
        "prove\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_catches_an_eval_redefinition_inside_a_helper(
    tmp_path,
):
    """Round-19 regression: the eval sits in a called helper
    — it executes at the helper's call, and poisons the
    static bodies defined before that call just the same."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "prove() {\n"
        "test -f /opt/beerus/beerus/live.py || exit 1\n"
        "test -f /etc/beerus/live-image.json || exit 1\n"
        "}\n"
        "sneak() {\n"
        "eval 'prove() { true; }'\n"
        "}\n"
        "sneak\n"
        "prove\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_catches_an_eval_redefinition_of_the_launcher_setup(
    tmp_path,
):
    """Round-19 regression, launcher twin: a helper's eval
    redefines setup; the exec then starts the console with
    no PYTHONPATH (verified against a logging python3)."""
    _repo, image = _copy_definition_tree(tmp_path)
    launcher = image / "config/includes.chroot/usr/local/bin/beerus-live"
    launcher.write_text(
        "#!/bin/sh\n"
        "setup() {\n"
        "PYTHONPATH=/opt/beerus\n"
        "export PYTHONPATH\n"
        "}\n"
        "sneak() {\n"
        "eval 'setup() { true; }'\n"
        "}\n"
        "sneak\n"
        "setup\n"
        "exec python3 -m beerus\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("launcher" in problem for problem in problems)


def test_check_catches_proofs_in_a_while_read_loop(tmp_path):
    """Round-19 regression: ``while read x; do … done <
    /dev/null`` runs its body zero times — a loop body
    proves only when iteration is guaranteed (``while``
    on a constant-true condition, ``until`` on a
    constant-false one)."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "while read x; do "
        "test -f /opt/beerus/beerus/live.py || exit 1; done "
        "< /dev/null\n"
        "test -f /etc/beerus/live-image.json || exit 1\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("hook" in problem for problem in problems)


def test_check_accepts_proofs_in_a_guaranteed_loop(tmp_path):
    """Positive control: ``while true; do …; done`` whose
    body guards on the first pass is a real proof. (The
    guard is the ``|| exit 1`` form on purpose: an
    UNCONDITIONAL exit in the body would end the script
    before the proofs after the loop — the round-32
    termination model — so it cannot appear here.)"""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "while true; do "
        "test -f /opt/beerus/beerus/live.py || exit 1; "
        "break; done\n"
        "test -f /etc/beerus/live-image.json || exit 1\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("hook" in problem for problem in problems)


@pytest.mark.parametrize(
    "shadow",
    ["test() { :; }", "E='test() { :; }'\neval \"$E\""],
)
def test_check_catches_a_shadowed_test_command(tmp_path, shadow):
    """Round-21 regression: the checker reads command NAMES,
    the shell resolves them — a function named ``test``
    silently replaces the builtin (functions win in dash and
    bash alike, shell-verified), and every existence proof
    becomes a no-op. An unresolvable eval before the proofs
    could define the same shadow invisibly, so it voids the
    credit too."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        f"{shadow}\n"
        "test -f /opt/beerus/beerus/live.py || exit 1\n"
        "test -f /etc/beerus/live-image.json || exit 1\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("live.py" in problem for problem in problems)


def test_check_catches_a_shadowed_mask_command(tmp_path):
    """Round-21 regression: ``systemctl() { :; }`` before the
    mask — the mask line runs, and masks nothing."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "test -f /opt/beerus/beerus/live.py || exit 1\n"
        "test -f /etc/beerus/live-image.json || exit 1\n"
        "systemctl() { :; }\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("getty" in problem for problem in problems)


@pytest.mark.parametrize(
    "shadow",
    ["lb() { :; }", "E='lb() { :; }'\neval \"$E\""],
)
def test_check_catches_a_shadowed_lb(tmp_path, shadow):
    """Round-21 regression, build form: with ``lb`` shadowed,
    the config/build lines run a no-op and the checksum step
    would bless a stale image (shell-verified with a logging
    lb stand-in: zero calls)."""
    _repo, image = _copy_definition_tree(tmp_path)
    build_sh = image / "build.sh"
    text = build_sh.read_text(encoding="utf-8")
    build_sh.write_text(f"{shadow}\n{text}", encoding="utf-8")
    problems = check_definition.check(image)
    assert any("lb config" in problem for problem in problems)


def test_check_catches_an_eval_that_rewrites_launcher_state(
    tmp_path,
):
    """Round-21 regression: a good assignment and export,
    then an eval whose text cannot be seen rewrites
    PYTHONPATH — the exec starts the console with the wrong
    value (shell-verified against a logging python3). State
    after an unresolvable eval is unknown until plain
    statements re-establish it."""
    _repo, image = _copy_definition_tree(tmp_path)
    launcher = image / "config/includes.chroot/usr/local/bin/beerus-live"
    launcher.write_text(
        "#!/bin/sh\n"
        "PYTHONPATH=/opt/beerus\n"
        "export PYTHONPATH\n"
        "E='PYTHONPATH=/tmp/elsewhere'\n"
        "eval \"$E\"\n"
        "exec python3 -m beerus\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("PYTHONPATH" in problem for problem in problems)


def test_check_accepts_state_reestablished_after_an_eval(tmp_path):
    """Positive control: the same eval, but plain statements
    after it set and export the real values again — the
    state at the exec is known, and the launcher proves.
    (Round 26: PATH must be re-established too — the exec
    resolves python3 through it, and after an eval its
    value is exactly as unknown as PYTHONPATH's; the
    earlier form of this control, which re-established
    only PYTHONPATH, was shell-proved to run a planted
    python3 and is pinned as refused by the next test.)"""
    _repo, image = _copy_definition_tree(tmp_path)
    launcher = image / "config/includes.chroot/usr/local/bin/beerus-live"
    launcher.write_text(
        "#!/bin/sh\n"
        "E='PYTHONPATH=/tmp/elsewhere'\n"
        "eval \"$E\"\n"
        "PYTHONPATH=/opt/beerus\n"
        "export PYTHONPATH\n"
        "PATH=/usr/bin:/bin\n"
        "export PATH\n"
        "exec python3 -m beerus\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("launcher" in problem for problem in problems)


def test_check_accepts_oneline_nested_loops(tmp_path):
    """Positive control (round-21 frame fix): ``while true;
    do for i in 1; do …`` on one line — the ``do for`` header
    rides in the do segment; without its own frame the inner
    ``done`` popped the while frame and everything after the
    loop was misjudged dead (a fail-safe false negative)."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "while true; do for i in 1; do "
        "test -f /opt/beerus/beerus/live.py || exit 1; "
        "break 2; done; done\n"
        "test -f /etc/beerus/live-image.json || exit 1\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("hook" in problem for problem in problems)


@pytest.mark.parametrize(
    "line,needle",
    [
        ("alias test=':'", "live.py"),
        ("alias systemctl=':'", "getty"),
    ],
)
def test_check_catches_an_aliased_proof_command(tmp_path, line, needle):
    """Round-23 regression (F-22-1): dash expands aliases
    even in non-interactive scripts — ``alias test=':'``
    turns every existence proof into a no-op and the mask
    line runs an alias, while the file reads as genuine
    (shell-verified in dash; bash is immune, dash is the
    target)."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        f"{line}\n"
        "test -f /opt/beerus/beerus/live.py || exit 1\n"
        "test -f /etc/beerus/live-image.json || exit 1\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any(needle in problem for problem in problems)


def test_check_catches_a_sourced_shadow_file(tmp_path):
    """Round-23 regression (F-22-2): a dot-source runs text
    the checker cannot see, in the current shell — the
    eval-poison class with a different spelling. A sourced
    file could define any shadow, so sourcing before the
    proofs voids them."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        ". /usr/local/lib/defs.sh\n"
        "test -f /opt/beerus/beerus/live.py || exit 1\n"
        "test -f /etc/beerus/live-image.json || exit 1\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("live.py" in problem for problem in problems)


def test_check_catches_a_relative_path_assignment(tmp_path):
    """Round-23 regression (F-22-3): a literal PATH value
    with a relative component (``./evilbin``) can point
    INTO the tree at a planted stub — the definition
    would be supplying its own proof commands. Purely
    absolute values stay environment trust (positive
    control in the next test)."""
    _repo, image = _copy_definition_tree(tmp_path)
    build_sh = image / "build.sh"
    text = build_sh.read_text(encoding="utf-8")
    build_sh.write_text(
        "PATH=./evilbin:/usr/bin:/bin\n" + text, encoding="utf-8"
    )
    problems = check_definition.check(image)
    assert any("lb" in problem for problem in problems)


def test_check_accepts_an_absolute_path_assignment(tmp_path):
    """Positive control: an all-absolute PATH assignment
    is the build machine's own environment, stated in
    the open — the build still proves."""
    _repo, image = _copy_definition_tree(tmp_path)
    build_sh = image / "build.sh"
    text = build_sh.read_text(encoding="utf-8")
    build_sh.write_text(
        "PATH=/usr/local/bin:/usr/bin:/bin\n" + text,
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("lb" in problem for problem in problems)


def test_check_catches_a_unit_that_overrides_its_restart(tmp_path):
    """Round-23 regression (F-22-4a): systemd reads
    single-value settings LAST-wins — a ``Restart=no``
    after ``Restart=on-failure`` in [Service] quietly
    replaces it."""
    _repo, image = _copy_definition_tree(tmp_path)
    unit = (
        image
        / "config/includes.chroot/etc/systemd/system/beerus-live.service"
    )
    text = unit.read_text(encoding="utf-8")
    unit.write_text(
        text.replace(
            "Restart=on-failure", "Restart=on-failure\nRestart=no"
        ),
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("Restart=on-failure" in p for p in problems)


def test_check_catches_a_reset_restart_prevent_list(tmp_path):
    """Round-23 regression (F-22-4b): an empty
    ``RestartPreventExitStatus=`` assignment RESETS the
    list in systemd — the 2 above it is wiped, and a
    designed refusal would enter the restart loop."""
    _repo, image = _copy_definition_tree(tmp_path)
    unit = (
        image
        / "config/includes.chroot/etc/systemd/system/beerus-live.service"
    )
    text = unit.read_text(encoding="utf-8")
    unit.write_text(
        text.replace(
            "[Install]", "RestartPreventExitStatus=\n[Install]"
        ),
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("RestartPreventExitStatus=2" in p for p in problems)


def test_check_catches_a_second_exec_start(tmp_path):
    """Round-23 regression (F-22-4c): systemd REFUSES a
    service with more than one ExecStart (verified with
    systemd-analyze) — the unit would not load at all."""
    _repo, image = _copy_definition_tree(tmp_path)
    unit = (
        image
        / "config/includes.chroot/etc/systemd/system/beerus-live.service"
    )
    text = unit.read_text(encoding="utf-8")
    unit.write_text(
        text.replace("[Install]", "ExecStart=/bin/false\n[Install]"),
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("ExecStart" in problem for problem in problems)


def test_check_catches_a_quote_split_forbidden_colour(tmp_path):
    """Round-23 regression (F-22-6): the shell joins
    adjacent quoted fragments, so the forbidden word
    split across quotes is the word at runtime; the
    scan strips quotes before looking."""
    _repo, image = _copy_definition_tree(tmp_path)
    launcher = image / "config/includes.chroot/usr/local/bin/beerus-live"
    text = launcher.read_text(encoding="utf-8")
    launcher.write_text(text + "X='pur''ple'\n", encoding="utf-8")
    problems = check_definition.check(image)
    assert any("forbidden colour" in problem for problem in problems)


def test_check_catches_a_wrong_hostname(tmp_path):
    """Round-23 regression (scope note): the hostname
    file's CONTENT is checked against the boot
    parameters, not just its presence."""
    _repo, image = _copy_definition_tree(tmp_path)
    hostname = image / "config/includes.chroot/etc/hostname"
    hostname.write_text("wrong-name\n", encoding="utf-8")
    problems = check_definition.check(image)
    assert any("hostname" in problem for problem in problems)


def test_check_catches_a_gutted_stage_script(tmp_path):
    """Round-23 regression (scope note): stage_image.py
    must visibly do its two jobs — write the marker and
    stage into includes.chroot."""
    _repo, image = _copy_definition_tree(tmp_path)
    stage = image / "stage_image.py"
    stage.write_text("# nothing here\n", encoding="utf-8")
    problems = check_definition.check(image)
    assert any("stage_image.py" in problem for problem in problems)


@pytest.mark.parametrize(
    "body",
    [". ./defs.sh", 'eval "$X"'],
)
def test_check_catches_unseen_text_in_a_called_helper(tmp_path, body):
    """Round-25 regression (F-24-1, HIGH): unseen text
    inside a CALLED helper executes at the call — but the
    poison pass marks the helper's body dead (to void what
    it proves), and the guards used to skip dead segments,
    so the execution hid inside the corpse. Guards now
    ask where unseen text EXECUTES (its call point), not
    whether its segment is dead. Shell-verified in dash
    and bash: the sourced/eval'd shadow survives every
    proof after the call."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        f"sneak() {{\n{body}\n}}\n"
        "sneak\n"
        "test -f /opt/beerus/beerus/live.py || exit 1\n"
        "test -f /etc/beerus/live-image.json || exit 1\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("live.py" in problem for problem in problems)


def test_check_catches_unseen_text_after_the_checks(tmp_path):
    """Round-25 regression (F-24-4): unseen text that runs
    AFTER the last check can undo it — the round-24
    shell proof sourced a file that unmasked the login
    the hook had just masked."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "test -f /opt/beerus/beerus/live.py || exit 1\n"
        "test -f /etc/beerus/live-image.json || exit 1\n"
        "systemctl mask getty@tty1.service\n"
        ". ./late.sh\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("after its" in problem for problem in problems)


def test_check_catches_a_path_prefix_on_the_mask(tmp_path):
    """Round-25 regression (F-24-2): the taint can ride
    ON the credited command itself — a command-prefix
    PATH assignment governs the lookup of that very
    command (shell-verified in dash and bash)."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "test -f /opt/beerus/beerus/live.py || exit 1\n"
        "test -f /etc/beerus/live-image.json || exit 1\n"
        "PATH=./evilbin:/usr/bin:/bin "
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("getty" in problem for problem in problems)


def test_check_catches_an_env_path_on_the_build(tmp_path):
    """Round-25 regression (F-24-2, env form): assignments
    handed to ``env`` never reach the effective tokens —
    they are read from the raw tokens, before the command
    they modify."""
    _repo, image = _copy_definition_tree(tmp_path)
    build_sh = image / "build.sh"
    text = build_sh.read_text(encoding="utf-8")
    build_sh.write_text(
        text.replace(
            "\nlb config",
            "\nenv PATH=./evilbin:/usr/bin:/bin lb config",
            1,
        ),
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("lb config" in problem for problem in problems)


def test_check_catches_a_relative_path_in_the_launcher(tmp_path):
    """Round-25 regression (F-24-3): the launcher's exec
    resolves python3 through PATH in the working
    directory systemd gives it — the image's root, which
    the definition itself can plant into. A relative
    PATH before the exec voids the exec credit."""
    _repo, image = _copy_definition_tree(tmp_path)
    launcher = image / "config/includes.chroot/usr/local/bin/beerus-live"
    launcher.write_text(
        "#!/bin/sh\n"
        "PATH=.:/usr/bin:/bin\n"
        "PYTHONPATH=/opt/beerus\n"
        "export PYTHONPATH\n"
        "exec python3 -m beerus\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("boot console" in problem for problem in problems)


def test_check_accepts_a_partial_second_lb_config(tmp_path):
    """Positive control (round-25, N4): lb config MERGES —
    a second call stating only other options leaves the
    flags the first call set. Each flag's effective value
    is the last value stated for it."""
    _repo, image = _copy_definition_tree(tmp_path)
    build_sh = image / "build.sh"
    text = build_sh.read_text(encoding="utf-8")
    build_sh.write_text(
        text + 'lb config --bootappend-live "boot=live"\n',
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("volume label" in problem for problem in problems)


def test_check_accepts_an_exec_start_reset(tmp_path):
    """Positive control (round-25, N3): an empty
    ``ExecStart=`` resets the list in systemd — a reset
    followed by exactly one real ExecStart is a valid
    unit (systemd-analyze verified); two real entries
    are still refused (pinned by the round-23 test)."""
    _repo, image = _copy_definition_tree(tmp_path)
    unit = (
        image
        / "config/includes.chroot/etc/systemd/system/beerus-live.service"
    )
    text = unit.read_text(encoding="utf-8")
    unit.write_text(
        text.replace(
            "ExecStart=/usr/local/bin/beerus-live",
            "ExecStart=\nExecStart=/usr/local/bin/beerus-live",
        ),
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("ExecStart" in problem for problem in problems)


def test_check_catches_the_colour_split_by_an_expansion(tmp_path):
    """Round-25 regression (F-24-5): the scan reads the
    value the shell would build — an empty expansion
    inside the word contributes nothing, so the word
    split by one is the word at runtime (dash-verified)."""
    _repo, image = _copy_definition_tree(tmp_path)
    launcher = image / "config/includes.chroot/usr/local/bin/beerus-live"
    text = launcher.read_text(encoding="utf-8")
    launcher.write_text(text + "X=pur${EMPTY}ple\n", encoding="utf-8")
    problems = check_definition.check(image)
    assert any("forbidden colour" in problem for problem in problems)


def test_check_catches_unseen_text_two_helpers_deep(tmp_path):
    """Round-27 regression (F-26-1, HIGH): the call graph
    is walked transitively — unseen text two helpers deep
    executes at the outermost helper's top-level call
    (shell-verified in dash and bash)."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "inner() {\n. ./defs.sh\n}\n"
        "outer() {\ninner\n}\n"
        "outer\n"
        "test -f /opt/beerus/beerus/live.py || exit 1\n"
        "test -f /etc/beerus/live-image.json || exit 1\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("live.py" in problem for problem in problems)


def test_check_catches_unseen_text_called_as_a_condition(tmp_path):
    """Round-27 regression (F-26-1): a helper called as an
    if-condition still EXECUTES in the current shell —
    the execution recogniser strips condition keywords
    the errexit recogniser keeps."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "sneak() {\n. ./defs.sh\n}\n"
        "if sneak; then :; fi\n"
        "test -f /opt/beerus/beerus/live.py || exit 1\n"
        "test -f /etc/beerus/live-image.json || exit 1\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("live.py" in problem for problem in problems)


def test_check_catches_a_dynamic_dispatch_before_the_checks(
    tmp_path,
):
    """Round-27 regression (F-26-1): a command head that is
    itself an expansion (``$FN``) is a dispatch the
    checker cannot see — an execution point on its own."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "FN=sneak\n"
        "$FN\n"
        "test -f /opt/beerus/beerus/live.py || exit 1\n"
        "test -f /etc/beerus/live-image.json || exit 1\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("live.py" in problem for problem in problems)


def test_check_catches_unseen_text_voiding_the_launcher_path(
    tmp_path,
):
    """Round-27 regression (F-26-2): after unseen text,
    the launcher's PATH is unknown — and the exec credit
    asserts it is not plantable, so unknown refuses,
    exactly as PYTHONPATH's state does. Re-establishing
    PYTHONPATH alone is not enough (shell-proven: a
    planted python3 ran); the updated round-21 control
    re-establishes both and passes."""
    _repo, image = _copy_definition_tree(tmp_path)
    launcher = image / "config/includes.chroot/usr/local/bin/beerus-live"
    launcher.write_text(
        "#!/bin/sh\n"
        "eval \"$E\"\n"
        "PYTHONPATH=/opt/beerus\n"
        "export PYTHONPATH\n"
        "exec python3 -m beerus\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("boot console" in problem for problem in problems)


def test_check_catches_an_env_option_path_on_the_build(tmp_path):
    """Round-27 regression (F-26-3): ``env -i PATH=…`` —
    the own-segment scan walks the whole wrapper chain
    up to the command, skipping options (shell-verified:
    the planted stub ran under env -i, sudo env, and
    timeout env alike)."""
    _repo, image = _copy_definition_tree(tmp_path)
    build_sh = image / "build.sh"
    text = build_sh.read_text(encoding="utf-8")
    build_sh.write_text(
        text.replace(
            "\nlb config",
            "\nenv -i PATH=./evilbin:/usr/bin:/bin lb config",
            1,
        ),
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("lb config" in problem for problem in problems)


def test_check_accepts_a_transient_prefix_path_assignment(
    tmp_path,
):
    """Positive control (round-27 note): a command-prefix
    PATH assignment governs only its own command — it
    does not persist, so it cannot taint LATER credits
    (its own command is judged by the own-segment
    check)."""
    _repo, image = _copy_definition_tree(tmp_path)
    build_sh = image / "build.sh"
    text = build_sh.read_text(encoding="utf-8")
    build_sh.write_text(
        "PATH=./evilbin:/usr/bin:/bin echo starting\n" + text,
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("lb" in problem for problem in problems)


def test_check_catches_concatenated_colour_in_python_code(
    tmp_path,
):
    """Round-27 regression (F-26-4): Python assembles
    adjacent and +-joined string literals at runtime —
    for .py files the scan joins those seams first."""
    _repo, image = _copy_definition_tree(tmp_path)
    stage = image / "stage_image.py"
    text = stage.read_text(encoding="utf-8")
    stage.write_text(text + '\nX = "pur" + "ple"\n', encoding="utf-8")
    problems = check_definition.check(image)
    assert any("forbidden colour" in problem for problem in problems)


def test_check_catches_unseen_text_called_through_eval(tmp_path):
    """Round-29 regression (F-28-1, HIGH): a plain-call
    ``eval sneak`` IS a call — the execution graph takes
    the edge from the eval-transparent name recogniser,
    the way the other passes always have. Shell-verified:
    after ``eval sneak``, a sourced ``test() { :; }``
    shadow voids the proofs exactly as after a plain
    call."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "sneak() {\n. ./defs.sh\n}\n"
        "eval sneak\n"
        "test -f /opt/beerus/beerus/live.py || exit 1\n"
        "test -f /etc/beerus/live-image.json || exit 1\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("live.py" in problem for problem in problems)


def test_check_catches_unset_path_before_the_mask(tmp_path):
    """Round-29 regression (F-28-2): ``unset PATH`` after
    startup leaves NO search path at all (shell-proved
    in dash and bash — not the compiled default an
    absent-at-startup PATH gets), so the mask's
    systemctl cannot resolve. The test proofs are
    builtins and correctly survive."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "test -f /opt/beerus/beerus/live.py || exit 1\n"
        "test -f /etc/beerus/live-image.json || exit 1\n"
        "unset PATH\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("getty" in problem for problem in problems)


def test_check_catches_unset_pythonpath_in_the_launcher(tmp_path):
    """Round-29 regression (F-28-2, launcher form): an
    unset PYTHONPATH is simply gone — the console would
    start without the staged package."""
    _repo, image = _copy_definition_tree(tmp_path)
    launcher = image / "config/includes.chroot/usr/local/bin/beerus-live"
    launcher.write_text(
        "#!/bin/sh\n"
        "PYTHONPATH=/opt/beerus\n"
        "export PYTHONPATH\n"
        "unset PYTHONPATH\n"
        "exec python3 -m beerus\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("PYTHONPATH" in problem for problem in problems)


def test_check_catches_an_exec_before_the_checks(tmp_path):
    """Round-29 regression (F-28-3): a bare exec replaces
    the shell — and a failed exec ends a non-interactive
    shell too — so nothing after a live top-level exec
    ever runs. It terminates exactly like exit."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "exec true\n"
        "test -f /opt/beerus/beerus/live.py || exit 1\n"
        "test -f /etc/beerus/live-image.json || exit 1\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("live.py" in problem for problem in problems)


def test_check_catches_colour_assembled_by_join(tmp_path):
    """Round-29 regression (F-28-4): a join() over a
    literal list assembles the word at runtime; the
    scan reads the literal arguments as written."""
    _repo, image = _copy_definition_tree(tmp_path)
    stage = image / "stage_image.py"
    text = stage.read_text(encoding="utf-8")
    stage.write_text(
        text + "\nX = ''.join(['pur', 'ple'])\n", encoding="utf-8"
    )
    problems = check_definition.check(image)
    assert any("forbidden colour" in problem for problem in problems)


def test_check_catches_colour_assembled_by_chr_codes(tmp_path):
    """Round-29 regression (F-28-4, chr form): a run of
    chr() codes is decoded as written and judged."""
    _repo, image = _copy_definition_tree(tmp_path)
    stage = image / "stage_image.py"
    text = stage.read_text(encoding="utf-8")
    stage.write_text(
        text
        + "\nX = chr(112) + chr(117) + chr(114)"
        " + chr(112) + chr(108) + chr(101)\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("forbidden colour" in problem for problem in problems)


def test_check_accepts_an_uncalled_unseen_helper_in_the_launcher(
    tmp_path,
):
    """Positive control (round-28 note): merely DEFINING
    a helper whose body runs unseen text — never called —
    executes nothing; the launcher loop's call check no
    longer fires on the definition's own name segment."""
    _repo, image = _copy_definition_tree(tmp_path)
    launcher = image / "config/includes.chroot/usr/local/bin/beerus-live"
    launcher.write_text(
        "#!/bin/sh\n"
        "sneak() { eval \"$X\"; }\n"
        "PYTHONPATH=/opt/beerus\n"
        "export PYTHONPATH\n"
        "exec python3 -m beerus\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("launcher" in problem for problem in problems)


def test_check_catches_unseen_text_called_through_a_wrapped_eval(
    tmp_path,
):
    """Round-31 regression (F-30-1, HIGH): ``command eval
    sneak`` executes sneak exactly like a plain call
    (shell-verified in dash and bash) — the execution
    graph's eval edge is read through the wrapper, the
    way the eval detector walks. ``eval command sneak``
    correctly does NOT call the function and stays
    accepted (pinned by the suite's controls)."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "sneak() {\n. ./defs.sh\n}\n"
        "command eval sneak\n"
        "test -f /opt/beerus/beerus/live.py || exit 1\n"
        "test -f /etc/beerus/live-image.json || exit 1\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("live.py" in problem for problem in problems)


def test_check_catches_an_exec_on_a_proven_chain(tmp_path):
    """Round-31 regression (F-30-2, HIGH): termination
    is an execution-point question — an exec the chain
    provably reaches (``true && exec …``) ends the
    script just as a bare one does."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "true && exec true\n"
        "test -f /opt/beerus/beerus/live.py || exit 1\n"
        "test -f /etc/beerus/live-image.json || exit 1\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("live.py" in problem for problem in problems)


def test_check_catches_an_exec_in_a_called_helper(tmp_path):
    """Round-31 regression (F-30-2, helper form): an
    exec inside a helper ends the script at the CALL —
    the direct analogue of the two-deep unseen-text
    hole, closed the same way."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "h() {\nexec true\n}\n"
        "h\n"
        "test -f /opt/beerus/beerus/live.py || exit 1\n"
        "test -f /etc/beerus/live-image.json || exit 1\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("live.py" in problem for problem in problems)


def test_check_accepts_an_exec_in_a_subshell(tmp_path):
    """Positive control (round-30 note N1, now precise):
    an exec inside a subshell replaces only the child —
    the parent script, and its proofs, run on."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "( exec true )\n"
        "test -f /opt/beerus/beerus/live.py || exit 1\n"
        "test -f /etc/beerus/live-image.json || exit 1\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("hook" in problem for problem in problems)


def test_check_accepts_a_redirection_only_exec(tmp_path):
    """Positive control (round-30 note N2, now precise):
    an exec with no command — only redirections —
    reassigns file descriptors and terminates nothing."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "exec >/dev/null\n"
        "test -f /opt/beerus/beerus/live.py || exit 1\n"
        "test -f /etc/beerus/live-image.json || exit 1\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("hook" in problem for problem in problems)


def test_check_catches_colour_assembled_by_join_over_a_tuple(
    tmp_path,
):
    """Round-31 regression (F-30-3): the join reading
    covers tuples as well as lists, hex chr() codes,
    and runs split by comment seams."""
    _repo, image = _copy_definition_tree(tmp_path)
    stage = image / "stage_image.py"
    text = stage.read_text(encoding="utf-8")
    stage.write_text(
        text + "\nX = ''.join(('pur', 'ple'))\n", encoding="utf-8"
    )
    problems = check_definition.check(image)
    assert any("forbidden colour" in problem for problem in problems)


def test_check_catches_a_missing_copy_off_tool(tmp_path):
    _repo, image = _copy_definition_tree(tmp_path)
    package_list = image / "config/package-lists/beerus.list.chroot"
    text = package_list.read_text(encoding="utf-8")
    package_list.write_text(
        text.replace("openssh-client\n", ""), encoding="utf-8"
    )
    problems = check_definition.check(image)
    assert any("openssh-client" in problem for problem in problems)


def test_check_ignores_words_in_the_staged_tree(tmp_path):
    """L4 regression: the staged package copy is build output,
    not definition content — a source comment naming the
    forbidden colour (as the brand docs do) must not fail the
    check once staging has run."""
    repo, image = _copy_definition_tree(tmp_path)
    stage_image.stage(repo, image, built="2026-10-20")
    staged_live = (
        image / "config/includes.chroot/opt/beerus/beerus/live.py"
    )
    with open(staged_live, "a", encoding="utf-8") as fh:
        fh.write("\n# Brand rule: no purple anywhere.\n")
    assert check_definition.check(image) == []


# ------------------------------------------------------------------ staging


def test_stage_produces_a_complete_image_tree(tmp_path):
    repo, image = _copy_definition_tree(tmp_path)
    marker = stage_image.stage(repo, image, built="2026-10-20")

    includes = image / "config" / "includes.chroot"
    staged_marker = includes / "etc" / "beerus" / "live-image.json"
    assert staged_marker.is_file()
    loaded = load_image_marker(str(staged_marker))
    assert loaded is not None
    assert loaded["image"] == IMAGE_MARKER_FORMAT
    assert loaded["version"] == beerus.__version__
    assert loaded["volume_label"] == LIVE_VOLUME_LABEL
    assert loaded["documents_dir"] == DEFAULT_DOCUMENTS_DIR
    assert marker == loaded

    staged_package = includes / "opt" / "beerus" / "beerus"
    assert (staged_package / "live.py").is_file()
    assert (staged_package / "bootmedium.py").is_file()
    assert not list(staged_package.rglob("__pycache__"))

    # And the definition, staged tree and all, still checks clean.
    assert check_definition.check(image) == []


def test_stage_is_rebuilt_fresh_each_time(tmp_path):
    repo, image = _copy_definition_tree(tmp_path)
    stage_image.stage(repo, image, built="2026-10-20")
    stale = (
        image
        / "config/includes.chroot/opt/beerus/beerus/stale-leftover.py"
    )
    stale.write_text("# leftover from an older build\n", encoding="utf-8")
    stage_image.stage(repo, image, built="2026-10-20")
    assert not stale.exists()


def test_stage_refuses_a_tree_without_the_console(tmp_path):
    repo, image = _copy_definition_tree(tmp_path)
    (repo / "beerus" / "live.py").unlink()
    with pytest.raises(stage_image.StageError):
        stage_image.stage(repo, image, built="2026-10-20")


def test_stage_refuses_a_missing_package(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    image = repo / "image"
    image.mkdir()
    with pytest.raises(stage_image.StageError):
        stage_image.stage(repo, image, built="2026-10-20")


def test_stage_refuses_a_symlink_inside_the_package(tmp_path):
    """L3 regression: staging must never follow a symlink in the
    package into the image — whatever it points at was never
    reviewed as part of the package."""
    import os

    repo, image = _copy_definition_tree(tmp_path)
    secret = tmp_path / "outside-the-package.txt"
    secret.write_text("not package content", encoding="utf-8")
    os.symlink(secret, repo / "beerus" / "link.txt")
    with pytest.raises(stage_image.StageError):
        stage_image.stage(repo, image, built="2026-10-20")


# ------------------------------------------------- definition/code agreement


def test_build_script_and_code_agree_on_the_label():
    build_text = (IMAGE_DIR / "build.sh").read_text(encoding="utf-8")
    assert f'--iso-volume "{LIVE_VOLUME_LABEL}"' in build_text


def test_version_is_consistent_across_the_release_surfaces():
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{beerus.__version__}"' in pyproject
    spec = (REPO_ROOT / "packaging" / "Beerus.spec").read_text(encoding="utf-8")
    assert f'"{beerus.__version__}"' in spec
    assert f"Version {beerus.__version__} matches" in spec


def test_check_catches_an_exec_called_through_eval(tmp_path):
    """Round-33 regression (F-32-1a): the termination pass
    looked callers up by plain call heads only, so a helper
    holding an exec, invoked through ``eval h`` or
    ``command eval h``, terminated nothing in the model —
    while the shell replaced itself at the eval. The
    termination graph now shares the unseen-text graph's
    eval-transparent edges."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "h() { exec true; }\n"
        "eval h\n" + _hook_text(),
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("live.py" in problem for problem in problems)


@pytest.mark.parametrize(
    "call", ["if h; then :; fi", "! h"]
)
def test_check_catches_an_exec_in_a_condition_called_helper(
    tmp_path, call
):
    """Round-33 regression (F-32-1b): a helper called as a
    condition or under negation still EXECUTES in the
    current shell — its exec replaces the shell — but the
    errexit-oriented pass had marked its body dead for
    those calls, and the termination pass believed the
    flag. Termination is computed from execution."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "h() { exec true; }\n" + call + "\n" + _hook_text(),
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("live.py" in problem for problem in problems)


def test_check_catches_an_exec_two_helpers_deep(tmp_path):
    """Round-33 regression (F-32-1c): termination now walks
    the call graph transitively, like unseen text has
    since round 26 — an exec two helpers deep ends the
    script at the outer call."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "a() { exec true; }\n"
        "b() { a; }\n"
        "b\n" + _hook_text(),
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("live.py" in problem for problem in problems)


@pytest.mark.parametrize(
    "prefix",
    [
        "h() { exit 1; }\nh",
        "a() { exit 1; }\nb() { a; }\nb",
    ],
)
def test_check_catches_an_exit_in_a_called_helper(tmp_path, prefix):
    """Round-33 regression (F-32-2): ``exit`` never got the
    execution-point treatment ``exec`` got in round 31 —
    an exit inside a plainly called helper (or two deep)
    ends the script exactly as a bare one does, and the
    proofs after the call never run."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n" + prefix + "\n" + _hook_text(),
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("live.py" in problem for problem in problems)


@pytest.mark.parametrize(
    "line",
    [
        "true && exit 1",
        "false || exit 1",
        "if true; then exit 1; fi",
        "while true; do exit 1; done",
    ],
)
def test_check_catches_an_exit_on_a_proven_path(tmp_path, line):
    """Round-33 regression (F-32-2): an exit on a PROVEN
    chain or in a proven branch/loop body terminates the
    script before the proofs that follow it."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n" + line + "\n" + _hook_text(),
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("live.py" in problem for problem in problems)


@pytest.mark.parametrize(
    "line",
    [
        'if [ -n "$X" ]; then exit 1; fi',
        'if [ -n "$X" ]; then exec true; fi',
    ],
)
def test_check_accepts_a_terminator_behind_an_unevaluable_guard(
    tmp_path, line
):
    """Positive control (round 33): a terminator behind an
    UNEVALUABLE condition ends only that branch's path —
    the script goes on down the other, so the proofs after
    it still run (the pristine scripts' guard blocks are
    exactly this shape). Termination is universal — every
    path must end — where a proof is existential."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n" + line + "\n" + _hook_text(),
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("hook" in problem for problem in problems)


def test_check_accepts_a_return_in_a_called_helper(tmp_path):
    """Positive control (round 33): ``return`` ends only
    the function — the script goes on — and is not a
    terminator (shell-proved in the round-32 audit)."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "h() { return 1; }\n"
        "h\n" + _hook_text(),
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("hook" in problem for problem in problems)


def test_check_catches_unseen_text_through_nested_evals(tmp_path):
    """Round-33 regression (F-32-3): ``eval eval sneak``
    executes sneak — each eval evaluates the next — but
    the invoked-name reader stopped at the first eval and
    reported a callee named "eval", which has no body, so
    no edge existed. Nested evals now unwrap."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "sneak() { . ./defs.sh; }\n"
        "eval eval sneak\n" + _hook_text(),
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("live.py" in problem for problem in problems)


def test_check_catches_colour_assembled_by_chr_codes_split_by_a_literal(
    tmp_path,
):
    """Round-33 regression (F-32-4): a chr() run interleaved
    with a string literal assembles the word at runtime —
    ``chr(112)+chr(117)+chr(114) + 'p' + …`` — and the
    literal used to reset the run. The scan now reads the
    expression's concatenated value."""
    _repo, image = _copy_definition_tree(tmp_path)
    stage = image / "stage_image.py"
    text = stage.read_text(encoding="utf-8")
    stage.write_text(
        text
        + "\nX = chr(112)+chr(117)+chr(114) + 'p' + chr(108)+chr(101)\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("forbidden colour" in problem for problem in problems)


def test_check_catches_colour_assembled_by_join_over_a_star_unpacked_list(
    tmp_path,
):
    """Round-33 regression (F-32-4): ``''.join([*['pur'],
    'ple'])`` — the flat join pattern stopped at the inner
    bracket and read only part of the list. The join
    argument is now read with a balanced scan."""
    _repo, image = _copy_definition_tree(tmp_path)
    stage = image / "stage_image.py"
    text = stage.read_text(encoding="utf-8")
    stage.write_text(
        text + "\nX = ''.join([*['pur'], 'ple'])\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("forbidden colour" in problem for problem in problems)


@pytest.mark.parametrize(
    "line",
    [
        "if false; then :; else exit 1; fi",
        "if false; then :; else exec true; fi",
        "if false; then :; elif false; then :; else exit 1; fi",
    ],
)
def test_check_catches_a_terminator_riding_the_else_segment(
    tmp_path, line
):
    """Round-35 regression (F-34-1): a terminator sharing a
    segment with ``else`` was judged with the THEN branch's
    frame state — the state in force when the segment
    started — so a proven else-branch terminator was
    credited as harmless and a dead one voided real proofs.
    The segmenter and the termination replay both switch
    the frame before judging an else segment now."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n" + line + "\n" + _hook_text(),
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("live.py" in problem for problem in problems)


def test_check_accepts_a_terminator_riding_a_dead_else_segment(
    tmp_path,
):
    """Positive control (round 35): the mirror image — with
    the then branch proven taken, the else branch is dead,
    its terminator never runs, and the proofs after it are
    genuine."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "if true; then :; else exit 1; fi\n" + _hook_text(),
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("hook" in problem for problem in problems)


def test_check_credits_a_proof_riding_the_else_segment(tmp_path):
    """Positive control (round 35, the proof-side mirror of
    F-34-1): a genuine proof test riding the else segment
    of a proven-false condition runs on every path that
    reaches it and is credited."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "if false; then :; else "
        "test -f /opt/beerus/beerus/live.py || exit 1; fi\n"
        "test -f /etc/beerus/live-image.json || exit 1\n"
        "systemctl mask getty@tty1.service\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("hook" in problem for problem in problems)


@pytest.mark.parametrize(
    "lead,codes",
    [
        ("'p'", "chr(117)+chr(114)+chr(112)+chr(108)+chr(101)"),
        ("'pu'", "chr(114)+chr(112)+chr(108)+chr(101)"),
    ],
)
def test_check_catches_colour_assembled_by_a_literal_before_the_first_chr_code(
    tmp_path, lead, codes
):
    """Round-35 regression (F-34-2): the chr reading
    absorbed literals between codes and after the last
    one, but never the literal BEFORE the first code —
    ``'p' + chr(117)+…`` assembles the word exactly like
    the interleaved forms. The leading literal now counts
    when only joiners separate it from the run."""
    _repo, image = _copy_definition_tree(tmp_path)
    stage = image / "stage_image.py"
    text = stage.read_text(encoding="utf-8")
    stage.write_text(
        text + f"\nX = {lead} + {codes}\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("forbidden colour" in problem for problem in problems)


@pytest.mark.parametrize("word", ["exit 1", "exec true"])
def test_check_catches_a_terminator_as_an_elif_condition(
    tmp_path, word
):
    """Round-37 regression (F-36-1): an ``elif`` segment
    carries its condition command in the segment itself,
    but it was stamped with the PREVIOUS branch's frame
    state — dead behind a proven-false first branch — so
    ``elif exit`` / ``elif exec`` escaped the termination
    model while ending the script on every path in both
    shells. The elif segment is now judged in the state
    it executes in: exactly when no earlier branch was
    taken."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        f"if false; then :; elif {word}; then :; fi\n"
        + _hook_text(),
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("live.py" in problem for problem in problems)


def test_check_catches_a_terminator_in_an_elif_condition_helper(
    tmp_path,
):
    """Round-37 regression (F-36-1): the same slot through a
    helper — ``elif h`` with an exit in h's body executes
    h on every path that reaches the elif."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "h() { exit 1; }\n"
        "if false; then :; elif h; then :; fi\n" + _hook_text(),
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("live.py" in problem for problem in problems)


@pytest.mark.parametrize(
    "prefix,body",
    [
        (
            "sneak() { . ./defs.sh; }\n",
            "if false; then :; elif sneak; then :; fi\n",
        ),
        (
            "",
            "if false; then :; elif . ./defs.sh; then :; fi\n",
        ),
    ],
)
def test_check_catches_unseen_text_as_an_elif_condition(
    tmp_path, prefix, body
):
    """Round-37 regression (F-36-1): unseen text in the
    elif condition — a sourcing helper called there, or a
    direct source — executed on the same paths and was
    discarded with the segment's wrong dead stamp, before
    the proofs and (second form) after them alike."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n" + prefix + body + _hook_text(),
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("live.py" in problem for problem in problems)


def test_check_accepts_a_terminator_as_a_dead_elif_condition(
    tmp_path,
):
    """Positive control (round 37): behind a proven-taken
    first branch the elif condition is never evaluated —
    its terminator never runs and the proofs stand."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "if true; then :; elif exit 1; then :; fi\n"
        + _hook_text(),
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("hook" in problem for problem in problems)


def test_check_accepts_a_terminator_behind_an_unknown_elif(
    tmp_path,
):
    """Positive control (round 37): behind an unevaluable
    elif, a body terminator ends only that branch's path;
    the proofs after the chain still run on the others."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        'if false; then :; elif [ -n "$X" ]; then exit 1; fi\n'
        + _hook_text(),
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("hook" in problem for problem in problems)


@pytest.mark.parametrize(
    "line",
    [
        "X = 'p' + ''.join(['ur','ple'])",
        "X = ''.join(['purp']) + 'le'",
        "X = chr(112) + ''.join(['ur','ple'])",
    ],
)
def test_check_catches_colour_assembled_around_a_join_result(
    tmp_path, line
):
    """Round-37 regression (F-36-2): a join() over literals
    has a literal VALUE, but the scan read only the parts
    inside the call — concatenation AROUND the result (a
    leading literal, a trailing one, a chr() run beside
    it) never combined with it. Literal joins are now
    read as the literals they assemble before the seam
    and chr readings run."""
    _repo, image = _copy_definition_tree(tmp_path)
    stage = image / "stage_image.py"
    text = stage.read_text(encoding="utf-8")
    stage.write_text(text + "\n" + line + "\n", encoding="utf-8")
    problems = check_definition.check(image)
    assert any("forbidden colour" in problem for problem in problems)


@pytest.mark.parametrize(
    "line",
    [
        "if true; then if false; then :; elif exit 1; then :; fi; fi",
        "if true; then if false; then :; else exit 1; fi; fi",
        "while true; do if false; then :; else exit 1; fi; done",
    ],
)
def test_check_catches_a_terminator_in_a_riding_nested_chain(
    tmp_path, line
):
    """Round-39 regression (F-38-1): a nested ``if`` riding
    the ``then``/``do`` segment (``then if …``) opened no
    frame — the opener keyword is the segment's second
    word — so the inner chain's elif/else slots were
    judged against the OUTER frame: the branch-switch
    inversion, one nesting level down. The riding opener
    now opens its frame in the segmenter and in the
    termination replay alike."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n" + line + "\n" + _hook_text(),
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("live.py" in problem for problem in problems)


def test_check_catches_unseen_text_in_a_riding_nested_chain(tmp_path):
    """Round-39 regression (F-38-1): the same slot held
    unseen text — a source riding the inner chain's elif
    condition executed on every path and was discarded
    with the outer frame's state."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "if true; then if false; then :; "
        "elif . ./defs.sh; then :; fi; fi\n" + _hook_text(),
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("live.py" in problem for problem in problems)


def test_check_accepts_a_riding_nested_chain_in_a_dead_branch(
    tmp_path,
):
    """Positive control (round 39): the mirror — a riding
    nested chain inside a branch that never runs is dead
    as a whole; its inner terminators never execute and
    the proofs after the outer chain stand."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "if false; then if false; then :; elif exit 1; "
        "then :; fi; fi\n" + _hook_text(),
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("hook" in problem for problem in problems)


def test_check_accepts_a_riding_nested_chain_behind_an_unknown_branch(
    tmp_path,
):
    """Positive control (round 39): behind an unevaluable
    outer branch the riding chain runs on some paths
    only; its terminators are not proven on every path
    and the proofs stand."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        'if [ -n "$X" ]; then if false; then :; '
        "elif exit 1; then :; fi; fi\n" + _hook_text(),
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("hook" in problem for problem in problems)


@pytest.mark.parametrize(
    "line",
    [
        'if [ -n "$X" ]; then exit 1; else exit 1; fi',
        'if [ -n "$X" ]; then exit 1; else exec true; fi',
        'if false; then :; elif [ -n "$X" ]; then exit 1; else exit 1; fi',
    ],
)
def test_check_catches_an_exhaustive_branch_cover(tmp_path, line):
    """Round-41 regression (F-40-1): each terminator ends
    only its own branch's path, so the per-segment model
    credited the proofs after the chain — but when every
    reachable branch terminates and the chain cannot
    fall through, EVERY path ends. The branch-cover pass
    joins the branches at ``fi``."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n" + line + "\n" + _hook_text(),
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("live.py" in problem for problem in problems)


def test_check_catches_a_helper_branch_cover(tmp_path):
    """Round-41 regression (F-40-1): the cover through
    calls — a body that always terminates, invoked in
    every reachable branch, covers the chain the same
    way a direct terminator in each branch does."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "h() { exit 1; }\n"
        'if [ -n "$X" ]; then h; else h; fi\n' + _hook_text(),
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("live.py" in problem for problem in problems)


def test_check_accepts_a_chain_with_a_surviving_branch(tmp_path):
    """Positive control (round 41): one branch that falls
    through to the code after the chain is enough — the
    cover needs every reachable branch, not any."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        'if [ -n "$X" ]; then exit 1; else :; fi\n'
        + _hook_text(),
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("hook" in problem for problem in problems)


def test_check_accepts_a_terminator_in_an_unproven_case_arm(
    tmp_path,
):
    """Positive control (round 41): an exit in a case arm
    the checker cannot prove is taken is not a
    termination point — the segmenter's own latch now
    honours the unproven stamp it always gave the arm,
    so the proofs after ``esac`` stand when the arm
    does not match."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    hook.write_text(
        "#!/bin/sh\n"
        "case $X in (x) exit 1;; esac\n" + _hook_text(),
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert not any("hook" in problem for problem in problems)


# ------------------------------------------------------------------
# Phase 4: the graphical session in the definition
# ------------------------------------------------------------------

_LAUNCHER = "config/includes.chroot/usr/local/bin/beerus-live"
_UI_CLIENT = "config/includes.chroot/usr/local/bin/beerus-ui"


def _mutate_launcher(image, old, new):
    launcher = image / _LAUNCHER
    text = launcher.read_text(encoding="utf-8")
    assert old in text
    launcher.write_text(text.replace(old, new), encoding="utf-8")
    return launcher


def test_check_catches_a_missing_gui_package(tmp_path):
    """Phase 4 regression: the graphical session's packages are
    required as listed package tokens, one by one."""
    _repo, image = _copy_definition_tree(tmp_path)
    package_list = image / "config/package-lists/beerus.list.chroot"
    text = package_list.read_text(encoding="utf-8")
    package_list.write_text(text.replace("xinit\n", ""), encoding="utf-8")
    problems = check_definition.check(image)
    assert any("xinit" in problem for problem in problems)


def test_check_catches_a_missing_ui_client(tmp_path):
    _repo, image = _copy_definition_tree(tmp_path)
    (image / _UI_CLIENT).unlink()
    problems = check_definition.check(image)
    assert any("beerus-ui" in problem for problem in problems)


def test_check_catches_a_non_executable_ui_client(tmp_path):
    import os

    _repo, image = _copy_definition_tree(tmp_path)
    os.chmod(image / _UI_CLIENT, 0o644)
    problems = check_definition.check(image)
    assert any(
        "beerus-ui is not executable" in problem for problem in problems
    )


def test_check_catches_a_ui_client_that_starts_the_console_module(
    tmp_path,
):
    """The graphical client must exec the boot UI module; the
    text console's module in its place would start the wrong
    session under X."""
    _repo, image = _copy_definition_tree(tmp_path)
    client = image / _UI_CLIENT
    text = client.read_text(encoding="utf-8")
    client.write_text(
        text.replace("python3 -m beerus.bootui", "python3 -m beerus"),
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any("boot UI" in problem for problem in problems)


def test_check_catches_a_launcher_without_startx(tmp_path):
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_launcher(
        image, "  startx /usr/local/bin/beerus-ui -- :0 vt1 -keeptty\n", ""
    )
    problems = check_definition.check(image)
    assert any(
        "never tries the graphical session" in problem
        for problem in problems
    )


def test_check_catches_a_launcher_that_falls_back_first(tmp_path):
    """The text fallback's exec in front of the graphical
    attempt ends the script before graphics are ever tried."""
    _repo, image = _copy_definition_tree(tmp_path)
    launcher = image / _LAUNCHER
    launcher.write_text(
        "#!/bin/sh\n"
        'PYTHONPATH="/opt/beerus${PYTHONPATH:+:$PYTHONPATH}"\n'
        "export PYTHONPATH\n"
        'exec python3 -m beerus "$@"\n'
        "startx /usr/local/bin/beerus-ui -- :0 vt1 -keeptty\n"
        "beerus_status=$?\n",
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert any(
        "never tries the graphical session" in problem
        or "no text fallback after the graphical" in problem
        for problem in problems
    )


def test_check_catches_a_launcher_that_swallows_the_startx_status(
    tmp_path,
):
    """``startx ... || true`` throws the graphical session's
    verdict away: a designed refusal (exit 2) would be lost and
    the fallback would start as if nothing had been decided."""
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_launcher(
        image,
        "startx /usr/local/bin/beerus-ui -- :0 vt1 -keeptty\n",
        "startx /usr/local/bin/beerus-ui -- :0 vt1 -keeptty || true\n",
    )
    problems = check_definition.check(image)
    assert any("swallows" in problem for problem in problems)


def test_check_catches_a_launcher_without_a_status_capture(tmp_path):
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_launcher(image, "  beerus_status=$?\n", "")
    problems = check_definition.check(image)
    assert any(
        "does not capture" in problem for problem in problems
    )


def test_check_catches_a_launcher_that_lets_a_refusal_fall_through(
    tmp_path,
):
    """Only exit 0 is honored: a graphical refusal (exit 2)
    would fall into the text fallback, repeat itself, and put
    the service on a restart-worthy path."""
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_launcher(
        image,
        '  if [ "$beerus_status" -eq 2 ]; then\n    exit 2\n  fi\n',
        "",
    )
    problems = check_definition.check(image)
    assert any("does not exit 2" in problem for problem in problems)


def test_check_catches_a_launcher_whose_startx_client_is_wrong(
    tmp_path,
):
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_launcher(
        image,
        "startx /usr/local/bin/beerus-ui",
        "startx /usr/local/bin/beerus-console",
    )
    problems = check_definition.check(image)
    assert any("client" in problem for problem in problems)


def _mutate_ui_client(image, old, new):
    client = image / _UI_CLIENT
    text = client.read_text(encoding="utf-8")
    assert old in text
    client.write_text(text.replace(old, new), encoding="utf-8")
    return client


def test_check_catches_an_exit_zero_parked_in_the_else_branch(tmp_path):
    """Phase 4 fix regression (F-02, auditor mutation m09): the
    ``exit 0`` moved into the ELSE of the status==0 test. It
    fires on every non-zero graphical outcome (a success lie)
    and lets a zero outcome fall through to the text session
    (a double run) — the exit is bound only in the branch
    whose condition it matches, and the else is not it."""
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_launcher(
        image,
        '  if [ "$beerus_status" -eq 0 ]; then\n    exit 0\n  fi\n',
        '  if [ "$beerus_status" -eq 0 ]; then\n'
        '    echo "graphical session finished"\n'
        "  else\n"
        "    exit 0\n"
        "  fi\n",
    )
    problems = check_definition.check(image)
    assert any("does not exit 0" in problem for problem in problems)


def test_check_catches_an_exit_two_parked_in_the_else_branch(tmp_path):
    """F-02, the other polarity: ``exit 2`` in the ELSE of the
    status==2 test claims a refusal for every outcome that is
    not a refusal, and lets a real refusal fall through."""
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_launcher(
        image,
        '  if [ "$beerus_status" -eq 2 ]; then\n    exit 2\n  fi\n',
        '  if [ "$beerus_status" -eq 2 ]; then\n'
        '    echo "not a refusal"\n'
        "  else\n"
        "    exit 2\n"
        "  fi\n",
    )
    problems = check_definition.check(image)
    assert any("does not exit 2" in problem for problem in problems)


def test_check_accepts_the_bound_exits_in_their_then_branches(tmp_path):
    """F-02 positive control: the shipped form — each exit in
    the THEN branch of its own status test — still proves."""
    _repo, image = _copy_definition_tree(tmp_path)
    problems = check_definition.check(image)
    assert not any("does not exit" in problem for problem in problems)


def test_check_catches_a_backgrounded_startx(tmp_path):
    """Phase 4 fix regression (F-03, auditor mutation m10):
    ``startx ... &`` backgrounds the session — the captured
    $? is the launch result (0 when the job started), never
    the session's exit code, so a graphical failure would be
    reported as success with no fallback."""
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_launcher(
        image,
        "startx /usr/local/bin/beerus-ui -- :0 vt1 -keeptty\n",
        "startx /usr/local/bin/beerus-ui -- :0 vt1 -keeptty &\n",
    )
    problems = check_definition.check(image)
    assert any("background" in problem for problem in problems)


def test_check_catches_a_pipelined_startx(tmp_path):
    """F-03, pipeline form: a piped startx reports the
    pipeline's last command, not the session's exit code."""
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_launcher(
        image,
        "startx /usr/local/bin/beerus-ui -- :0 vt1 -keeptty\n",
        "startx /usr/local/bin/beerus-ui -- :0 vt1 -keeptty | cat\n",
    )
    problems = check_definition.check(image)
    assert any(
        "background" in problem or "pipeline" in problem
        for problem in problems
    )


_NOTICE_1 = 'echo "The graphical session could not start on this machine."\n'
_NOTICE_2 = (
    'echo "Beerus is continuing in its text console — the same '
    "confirmation steps, the same engine, the same job documents.\"\n"
)


def test_check_catches_a_launcher_without_the_fallback_notice(tmp_path):
    """Phase 4 fix regression (F-04, auditor mutation m06):
    both notice lines deleted — the fallback would run
    silently, with the operator never told the graphical
    session could not start."""
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_launcher(image, _NOTICE_1 + _NOTICE_2, "")
    problems = check_definition.check(image)
    assert any("notice" in problem for problem in problems)


def test_check_catches_a_lying_fallback_notice(tmp_path):
    """F-04 (auditor mutation m06b): the second notice line
    rewritten to claim the graphical session while the text
    session is what actually runs."""
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_launcher(
        image,
        _NOTICE_2,
        'echo "Beerus is continuing in its graphical session — '
        "the same confirmation steps, the same engine, the same "
        'job documents."\n',
    )
    problems = check_definition.check(image)
    assert any("notice" in problem for problem in problems)


def test_check_catches_a_ui_client_without_a_python_path(tmp_path):
    """Phase 4 fix regression (F-05, auditor mutation m07):
    beerus-ui loses the lines that put the staged package on
    its path — the graphical session could not import the
    staged package and would never start."""
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_ui_client(
        image,
        'PYTHONPATH="/opt/beerus${PYTHONPATH:+:$PYTHONPATH}"\n'
        "export PYTHONPATH\n",
        "",
    )
    problems = check_definition.check(image)
    assert any(
        "beerus-ui" in problem and "/opt/beerus" in problem
        for problem in problems
    )


def test_check_catches_a_ui_client_with_a_wrong_python_path(tmp_path):
    """F-05, wrong-path form: the path is set and exported,
    but it does not contain the baked app root /opt/beerus."""
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_ui_client(
        image, 'PYTHONPATH="/opt/beerus', 'PYTHONPATH="/opt/elsewhere'
    )
    problems = check_definition.check(image)
    assert any(
        "beerus-ui" in problem and "/opt/beerus" in problem
        for problem in problems
    )


def test_check_catches_a_ui_client_that_never_exports_its_path(tmp_path):
    """F-05, unexported form: the path is assigned but never
    exported, so the python3 it execs would not see it."""
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_ui_client(image, "export PYTHONPATH\n", "")
    problems = check_definition.check(image)
    assert any(
        "beerus-ui" in problem and "never exports" in problem
        for problem in problems
    )


def test_check_catches_a_ui_client_that_runs_the_text_module_first(
    tmp_path,
):
    """Phase 4 fix regression (F-06, auditor mutation m08):
    the text session's module run first, in a subshell, then
    the boot UI — the client must do exactly one thing."""
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_ui_client(
        image,
        'exec python3 -m beerus.bootui "$@"',
        '( exec python3 -m beerus "$@" )\n'
        'exec python3 -m beerus.bootui "$@"',
    )
    problems = check_definition.check(image)
    assert any("exactly one" in problem for problem in problems)


def test_check_catches_a_ui_client_with_a_command_before_the_exec(
    tmp_path,
):
    """F-06: a plain extra command in front of the boot UI
    exec is a second effective command."""
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_ui_client(
        image,
        'exec python3 -m beerus.bootui "$@"',
        'python3 -m beerus "$@"\n'
        'exec python3 -m beerus.bootui "$@"',
    )
    problems = check_definition.check(image)
    assert any("exactly one" in problem for problem in problems)


def test_check_catches_a_ui_client_with_a_command_after_the_exec(
    tmp_path,
):
    """F-06: a command parked after the boot UI exec is still
    a second effective command in the client — its shape is
    exactly one command, as written."""
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_ui_client(
        image,
        'exec python3 -m beerus.bootui "$@"\n',
        'exec python3 -m beerus.bootui "$@"\necho done\n',
    )
    problems = check_definition.check(image)
    assert any("exactly one" in problem for problem in problems)


def test_check_catches_a_hook_without_the_bootui_proof(tmp_path):
    """The build must prove the staged boot UI is in the image,
    in the hook's established form — a missing proof means an
    image could ship with no graphical session in it."""
    _repo, image = _copy_definition_tree(tmp_path)
    hook = image / "config/hooks/live/0900-beerus.hook.chroot"
    text = hook.read_text(encoding="utf-8")
    block = (
        "if [ ! -f /opt/beerus/beerus/bootui/app.py ]; then\n"
        '  echo "beerus hook: /opt/beerus/beerus/bootui/app.py is missing." >&2\n'
        '  echo "beerus hook: staging did not run — build through image/build.sh, which stages first." >&2\n'
        "  exit 1\n"
        "fi\n\n"
    )
    assert block in text
    hook.write_text(text.replace(block, ""), encoding="utf-8")
    problems = check_definition.check(image)
    assert any("boot UI" in problem for problem in problems)


# ------------------------------------------------------------------
# Phase 4 re-audit: capture integrity, path-condition crediting,
# and the strict start-script grammar (R-01..R-05)
# ------------------------------------------------------------------

_STATUS_GUARDS = (
    '  if [ "$beerus_status" -eq 0 ]; then\n'
    "    exit 0\n"
    "  fi\n"
    '  if [ "$beerus_status" -eq 2 ]; then\n'
    "    exit 2\n"
    "  fi\n"
)


def test_check_catches_a_status_variable_reassigned_after_capture(
    tmp_path,
):
    """Re-audit R-01 (mutation v04c): a second assignment to
    the captured variable (``beerus_status=0``) after the
    capture replaces the graphical session's real outcome
    with a made-up one — refusal and failure alike would read
    as success. The variable must be assigned exactly once."""
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_launcher(
        image,
        "  beerus_status=$?\n",
        "  beerus_status=$?\n  beerus_status=0\n",
    )
    problems = check_definition.check(image)
    assert any("more than once" in problem for problem in problems)


def test_check_catches_a_capture_separated_from_startx(tmp_path):
    """Re-audit R-01: the capture must be the very next
    statement after the startx run — anything in between
    leaves open whether $? still holds the session's own
    outcome when it is read."""
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_launcher(
        image,
        "startx /usr/local/bin/beerus-ui -- :0 vt1 -keeptty\n"
        "  beerus_status=$?\n",
        "startx /usr/local/bin/beerus-ui -- :0 vt1 -keeptty\n"
        "  echo starting\n"
        "  beerus_status=$?\n",
    )
    problems = check_definition.check(image)
    assert any(
        "immediately after startx" in problem for problem in problems
    )


def test_check_catches_a_literal_status_assignment(tmp_path):
    """Re-audit R-01: a "capture" assigned a literal instead
    of $? holds a made-up outcome from the start."""
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_launcher(image, "beerus_status=$?", "beerus_status=0")
    problems = check_definition.check(image)
    assert any("does not capture" in problem for problem in problems)


def test_check_catches_a_function_wrapped_startx(tmp_path):
    """Re-audit R-01 (mutation v08b): startx wrapped in a
    function whose last command is an echo — the capture after
    the call reads the function's status (echo's 0), so a
    refusal or failure reads as success. Function definitions
    are outside the start scripts' strict grammar entirely."""
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_launcher(
        image,
        "  startx /usr/local/bin/beerus-ui -- :0 vt1 -keeptty\n",
        "  run_gui() {\n"
        "    startx /usr/local/bin/beerus-ui -- :0 vt1 -keeptty\n"
        "    echo started\n"
        "  }\n"
        "  run_gui\n",
    )
    problems = check_definition.check(image)
    assert any(
        "function definition" in problem for problem in problems
    )


def test_check_catches_a_subshell_startx(tmp_path):
    """Strict grammar: a subshell around the startx run is a
    construct the checker does not model — refused, never
    silently skipped."""
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_launcher(
        image,
        "  startx /usr/local/bin/beerus-ui -- :0 vt1 -keeptty\n",
        "  ( startx /usr/local/bin/beerus-ui -- :0 vt1 -keeptty )\n",
    )
    problems = check_definition.check(image)
    assert any("subshell" in problem for problem in problems)


def test_check_catches_a_backquote_substitution_in_the_launcher(
    tmp_path,
):
    """Strict grammar (R-05's class, in beerus-live): command
    substitution anywhere in a start script is refused,
    backquote spelling included."""
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_launcher(
        image,
        "  beerus_status=$?\n",
        "  beerus_status=$?\n  STAMP=`date`\n",
    )
    problems = check_definition.check(image)
    assert any(
        "command substitution" in problem for problem in problems
    )


def test_check_catches_a_case_statement_in_the_launcher(tmp_path):
    """Strict grammar: a ``case`` dispatch on the captured
    status is unmodelled — refused, however correct it looks."""
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_launcher(
        image,
        _STATUS_GUARDS,
        '  case "$beerus_status" in\n'
        "    0) exit 0 ;;\n"
        "    2) exit 2 ;;\n"
        "  esac\n",
    )
    problems = check_definition.check(image)
    assert any("case statement" in problem for problem in problems)


def test_check_catches_an_exit_two_in_a_dead_inner_branch(tmp_path):
    """Re-audit R-02 (mutation v02d): ``exit 2`` in the inner
    then-branch nested in the ELSE of the very test it must
    satisfy — its path condition (status not 2, and status 2)
    is empty, so it can never fire when a refusal happens.
    Crediting judges every enclosing branch, not the
    innermost one alone."""
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_launcher(
        image,
        _STATUS_GUARDS,
        '  if [ "$beerus_status" -eq 0 ]; then\n'
        "    exit 0\n"
        "  fi\n"
        '  if [ "$beerus_status" -eq 2 ]; then\n'
        "    echo refused\n"
        "  else\n"
        '    if [ "$beerus_status" -eq 2 ]; then\n'
        "      exit 2\n"
        "    fi\n"
        "  fi\n",
    )
    problems = check_definition.check(image)
    assert any("does not exit 2" in problem for problem in problems)


def test_check_catches_an_exit_two_bound_to_the_wrong_elif_code(
    tmp_path,
):
    """Re-audit F-02 family (mutation v01b): an elif binding
    ``exit 2`` to status 1 — the exit's path condition is
    exactly {1}, never {2}."""
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_launcher(
        image,
        _STATUS_GUARDS,
        '  if [ "$beerus_status" -eq 0 ]; then\n'
        "    exit 0\n"
        '  elif [ "$beerus_status" -eq 1 ]; then\n'
        "    exit 2\n"
        "  fi\n",
    )
    problems = check_definition.check(image)
    assert any("does not exit 2" in problem for problem in problems)


def test_check_catches_an_exit_two_in_the_else_of_an_elif_chain(
    tmp_path,
):
    """Mutation v01c: ``exit 2`` parked in the else of an elif
    chain fires on every outcome that is neither 0 nor 2 —
    its path condition is the fallback set, not {2}."""
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_launcher(
        image,
        _STATUS_GUARDS,
        '  if [ "$beerus_status" -eq 0 ]; then\n'
        "    exit 0\n"
        '  elif [ "$beerus_status" -eq 2 ]; then\n'
        "    echo refused\n"
        "  else\n"
        "    exit 2\n"
        "  fi\n",
    )
    problems = check_definition.check(image)
    assert any("does not exit 2" in problem for problem in problems)


def test_check_catches_a_chained_exit_bound_to_the_wrong_code(
    tmp_path,
):
    """Mutation v03c: the ``&&``-chained form binds by the
    test in front of it — ``[ status -eq 0 ] && exit 2``
    honors 2 exactly when the outcome is 0, which is the lie
    in chained spelling."""
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_launcher(
        image,
        _STATUS_GUARDS,
        '  [ "$beerus_status" -eq 0 ] && exit 2\n'
        '  if [ "$beerus_status" -eq 0 ]; then\n'
        "    exit 0\n"
        "  fi\n",
    )
    problems = check_definition.check(image)
    assert any("does not exit 2" in problem for problem in problems)


def test_check_catches_a_chained_exit_behind_an_unrelated_command(
    tmp_path,
):
    """Mutation v03b: a test on its own line binds nothing —
    the ``exit 0`` chained behind an unrelated echo runs
    whenever the echo succeeds, whatever the status was."""
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_launcher(
        image,
        _STATUS_GUARDS,
        '  [ "$beerus_status" -eq 0 ]\n'
        "  echo hi && exit 0\n"
        '  if [ "$beerus_status" -eq 2 ]; then\n'
        "    exit 2\n"
        "  fi\n",
    )
    problems = check_definition.check(image)
    assert any("does not exit 0" in problem for problem in problems)


def test_check_catches_exits_bound_to_a_different_variable(tmp_path):
    """Mutation v04b: the guards test a variable that was
    never captured — they bind nothing about the graphical
    session's outcome."""
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_launcher(
        image, '"$beerus_status" -eq', '"$other_status" -eq'
    )
    problems = check_definition.check(image)
    assert any("does not exit 0" in problem for problem in problems)


def test_check_catches_a_fallback_exec_in_the_else_branch(tmp_path):
    """Re-audit R-03 (mutation v05): the text form's exec in
    the ELSE of the status==0 test, with the notices moved in
    front of the graphical attempt. Status 2 reaches the exec
    before its own guard (the refusal is lost), and the
    notices print unconditionally — program order alone does
    not make an exec the fallback."""
    _repo, image = _copy_definition_tree(tmp_path)
    launcher = image / _LAUNCHER
    text = launcher.read_text(encoding="utf-8")
    text = text.replace(_NOTICE_1 + _NOTICE_2, "")
    text = text.replace(
        "if command -v startx",
        _NOTICE_1 + _NOTICE_2 + "if command -v startx",
    )
    text = text.replace(
        _STATUS_GUARDS,
        '  if [ "$beerus_status" -eq 0 ]; then\n'
        "    exit 0\n"
        "  else\n"
        '    exec python3 -m beerus "$@"\n'
        "  fi\n"
        '  if [ "$beerus_status" -eq 2 ]; then\n'
        "    exit 2\n"
        "  fi\n",
    )
    launcher.write_text(text, encoding="utf-8")
    problems = check_definition.check(image)
    assert any("notice" in problem for problem in problems)
    assert any("does not exit 2" in problem for problem in problems)


def test_check_catches_a_fallback_exec_that_preempts_the_refusal_guard(
    tmp_path,
):
    """Re-audit R-03 (mutation v05b): the else-branch exec
    with the notices left in place. The exec still pre-empts
    the status==2 guard, and after it no outcome reaches the
    notices at all — neither the exec nor the notices stand
    on the path every other outcome takes."""
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_launcher(
        image,
        _STATUS_GUARDS,
        '  if [ "$beerus_status" -eq 0 ]; then\n'
        "    exit 0\n"
        "  else\n"
        '    exec python3 -m beerus "$@"\n'
        "  fi\n"
        '  if [ "$beerus_status" -eq 2 ]; then\n'
        "    exit 2\n"
        "  fi\n",
    )
    problems = check_definition.check(image)
    assert any("notice" in problem for problem in problems)
    assert any("text form" in problem for problem in problems)


def test_check_catches_notices_inside_a_status_branch(tmp_path):
    """Re-audit R-04 (mutation w02b): the notices wrapped in
    ``if [ status -eq 1 ]`` print only for status 1 — every
    other fallback outcome (status 3, startx absent) would
    run the text form in silence."""
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_launcher(
        image,
        _NOTICE_1 + _NOTICE_2,
        'if [ "$beerus_status" -eq 1 ]; then\n'
        + _NOTICE_1
        + _NOTICE_2
        + "fi\n",
    )
    problems = check_definition.check(image)
    assert any("notice" in problem for problem in problems)


def test_check_catches_notices_inside_an_unrelated_branch(tmp_path):
    """Re-audit R-04 (mutation w02c): the notices inside a
    branch on an unrelated variable are conditional by
    construction — the fallback path itself is unconditional,
    and only notices standing on it count."""
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_launcher(
        image,
        _NOTICE_1 + _NOTICE_2,
        'if [ -n "$HOME" ]; then\n' + _NOTICE_1 + _NOTICE_2 + "fi\n",
    )
    problems = check_definition.check(image)
    assert any("notice" in problem for problem in problems)


def test_check_catches_a_ui_pythonpath_built_by_substitution(tmp_path):
    """Re-audit R-05 (mutation w04d): a command substitution
    appended to a valid PYTHONPATH component in beerus-ui —
    inside the quoted value, where the segmenter never splits
    it. It would run a second, arbitrary command inside the
    client whose whole checked shape is one command."""
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_ui_client(
        image,
        'PYTHONPATH="/opt/beerus${PYTHONPATH:+:$PYTHONPATH}"',
        'PYTHONPATH="/opt/beerus:$(touch /tmp/beerus-marker)"',
    )
    problems = check_definition.check(image)
    assert any(
        "command substitution" in problem
        or "not written out in full" in problem
        for problem in problems
    )


def test_check_catches_a_ui_pythonpath_from_another_variable(tmp_path):
    """Re-audit R-05, variable form: the path value pulls in
    another variable — the path the check proves would not
    be the path that runs."""
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_ui_client(
        image,
        'PYTHONPATH="/opt/beerus${PYTHONPATH:+:$PYTHONPATH}"',
        'PYTHONPATH="/opt/beerus:$HOME/beerus"',
    )
    problems = check_definition.check(image)
    assert any(
        "not written out in full" in problem for problem in problems
    )


def test_check_accepts_guards_written_as_and_chains(tmp_path):
    """Re-audit R-07(a), resolved: the ``&&``-chained guard
    form is credited under the same path model — the test in
    front of the exit contributes its then-set to the chained
    statement."""
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_launcher(
        image,
        _STATUS_GUARDS,
        '  [ "$beerus_status" -eq 0 ] && exit 0\n'
        '  [ "$beerus_status" -eq 2 ] && exit 2\n',
    )
    problems = check_definition.check(image)
    assert problems == []


def test_check_accepts_guards_written_as_an_elif_chain(tmp_path):
    """Mutation v01a, a correct launcher: one if/elif chain
    binding each exit to its own code."""
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_launcher(
        image,
        _STATUS_GUARDS,
        '  if [ "$beerus_status" -eq 0 ]; then\n'
        "    exit 0\n"
        '  elif [ "$beerus_status" -eq 2 ]; then\n'
        "    exit 2\n"
        "  fi\n",
    )
    problems = check_definition.check(image)
    assert problems == []


def test_check_accepts_a_guard_nested_in_the_other_codes_else(
    tmp_path,
):
    """Mutation v02a, a correct launcher: ``exit 0`` tested
    inside the else of the status==2 test — its path
    condition (not 2, and 0) entails exactly {0}."""
    _repo, image = _copy_definition_tree(tmp_path)
    _mutate_launcher(
        image,
        _STATUS_GUARDS,
        '  if [ "$beerus_status" -eq 2 ]; then\n'
        "    exit 2\n"
        "  else\n"
        '    if [ "$beerus_status" -eq 0 ]; then\n'
        "      exit 0\n"
        "    fi\n"
        "  fi\n",
    )
    problems = check_definition.check(image)
    assert problems == []


def test_check_accepts_a_bare_and_chain_launcher(tmp_path):
    """Positive control for the whole strict form: startx run
    unconditionally (a missing startx is itself an outcome —
    127 — and lands in the fallback set), a short variable
    name, ``&&``-chained guards, then the notices and the
    exec on the one path every other outcome takes."""
    _repo, image = _copy_definition_tree(tmp_path)
    launcher = image / _LAUNCHER
    launcher.write_text(
        "#!/bin/sh\n"
        'PYTHONPATH="/opt/beerus${PYTHONPATH:+:$PYTHONPATH}"\n'
        "export PYTHONPATH\n"
        "startx /usr/local/bin/beerus-ui -- :0 vt1 -keeptty\n"
        "s=$?\n"
        '[ "$s" -eq 0 ] && exit 0\n'
        '[ "$s" -eq 2 ] && exit 2\n'
        + _NOTICE_1
        + _NOTICE_2
        + 'exec python3 -m beerus "$@"\n',
        encoding="utf-8",
    )
    problems = check_definition.check(image)
    assert problems == []
