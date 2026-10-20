"""Check the Live Boot image definition for completeness.

This is the furthest validation that can run without a build
machine: it verifies that the definition in ``image/`` is whole
and internally consistent — every file the build and the running
image rely on is present, the volume label the build script
passes to the image builder is the same label the protection
code looks for (``beerus.bootmedium.LIVE_VOLUME_LABEL``), the
package list carries the tools the engine shells out to, the
boot service starts the launcher, the launcher starts the
console, and no file in the definition uses the one colour the
brand forbids.

It does NOT build the image and does not prove the image boots;
that is Phase 5's hardware work, and ``docs/IMAGE-BUILD.md``
says so. Run it any time the definition changes:

    python3 image/check_definition.py
"""

from __future__ import annotations

import ast
import fnmatch
import re
import shlex
import sys
from pathlib import Path
from typing import NamedTuple

_REQUIRED_FILES = (
    "build.sh",
    "stage_image.py",
    "check_definition.py",
    "README.md",
    "config/package-lists/beerus.list.chroot",
    "config/includes.chroot/etc/hostname",
    "config/includes.chroot/etc/systemd/system/beerus-live.service",
    "config/includes.chroot/usr/local/bin/beerus-live",
    "config/includes.chroot/usr/local/bin/beerus-ui",
    "config/hooks/live/0900-beerus.hook.chroot",
)

_WANTS_LINK = (
    "config/includes.chroot/etc/systemd/system/"
    "multi-user.target.wants/beerus-live.service"
)
_WANTS_TARGET = "../beerus-live.service"

_REQUIRED_PACKAGES = (
    "python3",
    "util-linux",
    "hdparm",
    "nvme-cli",
    "openssh-client",
    # The graphical session (Phase 4): the X server and its
    # generic drivers, the startx way in, the Qt libraries the
    # boot UI runs on, and its font.
    "xserver-xorg-core",
    "xserver-xorg-input-libinput",
    "xserver-xorg-video-fbdev",
    "xserver-xorg-video-vesa",
    "xinit",
    "python3-pyside6.qtcore",
    "python3-pyside6.qtgui",
    "python3-pyside6.qtwidgets",
    "fonts-dejavu-core",
)

# Every package the image is allowed to carry — the definition's
# own intent, nothing more. Extending the image means extending
# this list in the same change, on purpose: the list is how
# "nothing the destruction environment does not need" — above
# all, no network server — stays true as the definition evolves.
_ALLOWED_PACKAGES = frozenset(
    {
        "live-boot",
        "live-config",
        "systemd",
        "systemd-sysv",
        "python3",
        "util-linux",
        "hdparm",
        "nvme-cli",
        "coreutils",
        "less",
        "openssh-client",
        "xserver-xorg-core",
        "xserver-xorg-input-libinput",
        "xserver-xorg-video-fbdev",
        "xserver-xorg-video-vesa",
        "xinit",
        "python3-pyside6.qtcore",
        "python3-pyside6.qtgui",
        "python3-pyside6.qtwidgets",
        "fonts-dejavu-core",
    }
)

# Directives the boot service must carry, per section — checked
# as real assignments inside the right section of the unit, not
# as substrings that a comment (or the wrong section) could
# satisfy. systemd would refuse a unit whose directives sit in
# the wrong section, so the checker refuses it too.
_REQUIRED_SERVICE_DIRECTIVES = {
    "Unit": (
        ("After", "systemd-user-sessions.service"),
        ("Conflicts", "getty@tty1.service"),
    ),
    "Service": (
        ("ExecStart", "/usr/local/bin/beerus-live"),
        ("User", "root"),
        ("StandardInput", "tty"),
        ("StandardOutput", "tty"),
        ("StandardError", "tty"),
        ("TTYPath", "/dev/tty1"),
        ("Restart", "on-failure"),
        ("RestartPreventExitStatus", "2"),
    ),
    "Install": (("WantedBy", "multi-user.target"),),
}

# Paths the build generates inside the definition tree. They are
# staged copies and build output, not definition content, and
# the content scans below skip them.
_STAGED_PREFIX = ("config", "includes.chroot", "opt")
_STAGED_MARKER = "config/includes.chroot/etc/beerus/live-image.json"

# The one colour the brand forbids — spelled in pieces so this
# checker does not flag its own source when it scans the tree.
_FORBIDDEN_WORD = "pur" + "ple"


def _code_lines(text: str) -> list[str]:
    """Non-comment, non-blank lines, stripped.

    Content checks run against these, so a requirement can never
    be satisfied by a comment that merely mentions it.
    """
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


def _is_executable(path: Path) -> bool:
    import os

    return bool(os.stat(path).st_mode & 0o111)


def _strip_shell_comment(line: str) -> str:
    """Remove a trailing shell comment from *line*.

    A ``#`` starts a comment when it begins a word (start of
    line, or after whitespace or a shell separator) and is not
    inside quotes. Quote state is tracked so a ``#`` inside a
    quoted string survives. If the scan is ever unsure it errs
    toward truncating — which makes the checks below stricter,
    never looser.
    """
    in_single = False
    in_double = False
    escaped = False
    for index, ch in enumerate(line):
        if escaped:
            escaped = False
            continue
        if ch == "\\" and not in_single:
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            continue
        if ch == "#" and not in_single and not in_double:
            if index == 0 or line[index - 1] in " \t;|&()":
                return line[:index].rstrip()
    return line


# Commands whose output (or silence) is never the action a check
# looks for, matched by basename so /bin/echo and /bin/true are
# covered too.
_NOOP_COMMANDS = frozenset({"echo", "printf", ":", "true", "false"})

# Words that run the words after them as the real command:
# ``command echo x``, ``env FOO=1 echo x``, ``sudo echo x``,
# ``xargs echo``, ``timeout 5 echo`` and ``eval echo x`` are
# all, in the end, an echo.
_WRAPPER_COMMANDS = frozenset(
    {
        "command",
        "builtin",
        "env",
        "sudo",
        "doas",
        "eval",
        "nice",
        "nohup",
        "xargs",
        "timeout",
        "stdbuf",
        "setsid",
    }
)

# Commands that take options (and, for timeout, a duration)
# before the command they wrap.
_WRAPPER_WITH_ARGS = frozenset(
    {"sudo", "doas", "nice", "xargs", "timeout", "stdbuf", "setsid"}
)

# Shells whose -c string is a script of its own; the string is
# analysed as one, so what it runs is what gets checked.
_SHELL_COMMANDS = frozenset({"sh", "bash", "dash", "zsh"})

# Shell keywords that can precede the real command in a segment.
_KEYWORDS = frozenset(
    {
        "if",
        "then",
        "else",
        "elif",
        "do",
        "while",
        "until",
        "!",
        "time",
        "{",
        "}",
    }
)

_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_HEREDOC = re.compile(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?")


def _logical_lines(lines: list[str]) -> list[str]:
    """Join backslash-continued shell lines into logical lines."""
    logical: list[str] = []
    buffer = ""
    for line in lines:
        buffer = f"{buffer} {line}".strip() if buffer else line
        if buffer.endswith("\\"):
            buffer = buffer[:-1].rstrip()
            continue
        logical.append(buffer)
        buffer = ""
    if buffer:
        logical.append(buffer)
    return logical


def _drop_heredoc_bodies(lines: list[str]) -> list[str]:
    """Remove heredoc bodies: lines a script feeds to a command
    as input are data, never commands, however they are spelled."""
    kept: list[str] = []
    pending: list[str] = []
    for line in lines:
        if pending:
            if line.strip() == pending[0]:
                pending.pop(0)
            continue
        kept.append(line)
        pending.extend(m.group(1) for m in _HEREDOC.finditer(line))
    return kept


def _split_segments(line: str) -> list[tuple[str, str | None]]:
    """Split one logical line into its individual commands.

    Returns (segment, separator-after) pairs. Shell command
    separators (``;``, ``&``, ``|`` and parentheses) outside
    quotes end one command and start the next, so a requirement
    can never be satisfied by a second command hiding behind the
    first one's ``;``. The separator is kept: ``&&`` and ``||``
    decide whether the next command runs at all, and the checks
    below reason about that.
    """
    parts: list[tuple[str, str | None]] = []
    buffer: list[str] = []

    def flush(separator: str | None) -> None:
        parts.append(("".join(buffer).strip(), separator))
        buffer.clear()

    in_single = False
    in_double = False
    escaped = False
    index = 0
    while index < len(line):
        ch = line[index]
        if escaped:
            buffer.append(ch)
            escaped = False
            index += 1
            continue
        if ch == "\\" and not in_single:
            buffer.append(ch)
            escaped = True
            index += 1
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            buffer.append(ch)
            index += 1
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            buffer.append(ch)
            index += 1
            continue
        if not in_single and not in_double and ch in ";|&()":
            following = line[index + 1] if index + 1 < len(line) else ""
            if ch == "&" and (
                (buffer and buffer[-1] == ">")
                or following.isdigit()
                or following in ("-", ">")
            ):
                # A redirection (2>&1, >&2, &>file), not a
                # command separator.
                buffer.append(ch)
                index += 1
                continue
            if ch in "|&" and following == ch:
                flush(ch * 2)
                index += 2
                continue
            flush(ch)
            index += 1
            continue
        buffer.append(ch)
        index += 1
    flush(None)
    return parts


def _tokens_of(segment: str) -> list[str]:
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return segment.split()


def _basename(token: str) -> str:
    return token.rsplit("/", 1)[-1]


class _Segment(NamedTuple):
    """One shell command, analysed.

    ``tokens`` are the effective command words with leading
    keywords, variable assignments and wrapper commands
    (``command``, ``env``, ``sudo``, ``eval`` …) stripped, so
    ``tokens[0]`` is the program that actually runs. ``kind`` is
    "command" for a real command, "noop" for an output/no-op
    command (echo, printf, true …), "assignment" for a statement
    that only sets variables, and "empty". ``raw_tokens`` are
    the words as written, before stripping. ``dead`` marks a
    command that can never run (a constant-false branch, the
    far side of a short-circuit, anything after an unconditional
    exit). ``depth`` is 0 for the script itself and grows inside
    nested ``sh -c`` strings. ``sep_after`` is the shell
    separator that followed the command.
    """

    text: str
    kind: str
    tokens: list
    assignments: list
    raw_tokens: list
    dead: bool = False
    depth: int = 0
    sep_after: str | None = None
    sep_before: str | None = None
    subshell: int = 0
    in_pipeline: bool = False
    func: str | None = None
    unproven: bool = False
    stmt_bg: bool = False


def _effective(tokens: list[str]):
    """Strip keywords, assignments and wrappers from a token
    list; return (kind, effective tokens, assignments)."""
    tokens = list(tokens)
    assignments: list[str] = []
    while tokens:
        head = tokens[0]
        base = _basename(head)
        if head in _KEYWORDS:
            tokens.pop(0)
            continue
        if _ASSIGNMENT.match(head):
            assignments.append(tokens.pop(0))
            continue
        if base in _WRAPPER_COMMANDS:
            tokens.pop(0)
            if base == "env":
                # env's own assignments and options belong to the
                # wrapped command, not to the shell.
                while tokens and (
                    _ASSIGNMENT.match(tokens[0])
                    or tokens[0].startswith("-")
                ):
                    tokens.pop(0)
            elif base in _WRAPPER_WITH_ARGS:
                while tokens and tokens[0].startswith("-"):
                    tokens.pop(0)
                if base == "timeout" and tokens and tokens[
                    0
                ][:1].isdigit():
                    tokens.pop(0)  # the duration
            continue
        break
    if not tokens:
        kind = "assignment" if assignments else "empty"
        return kind, [], assignments
    kind = "noop" if _basename(tokens[0]) in _NOOP_COMMANDS else "command"
    return kind, tokens, assignments


def _analyse(segment: str) -> _Segment:
    raw = _tokens_of(segment)
    kind, tokens, assignments = _effective(raw)
    return _Segment(segment, kind, tokens, assignments, raw)


def _condition_base(segment: _Segment) -> str:
    """The program a condition segment actually runs."""
    if segment.tokens:
        return _basename(segment.tokens[0])
    return ""


_FILE_TEST_OPS = frozenset(
    {"-f", "-e", "-d", "-s", "-r", "-w", "-x", "-p", "-h", "-L", "-b", "-c", "-S"}
)


def _eval_test(inner: list[str]):
    """Evaluate a constant ``[ … ]``/``test`` expression.

    Returns True/False when every operand is a literal and the
    answer is fixed, None when the answer depends on the world
    (file tests, variables, command output). Only simple shapes
    are evaluated — anything richer is "unknown", never guessed.
    """
    while inner and inner[0] == "!":
        flipped = _eval_test(inner[1:])
        return None if flipped is None else not flipped
    if any("$" in token or "`" in token for token in inner):
        return None
    if any(token in _FILE_TEST_OPS for token in inner):
        return None
    if not inner:
        return False
    if len(inner) == 1:
        return inner[0] != ""
    if len(inner) == 2:
        op, operand = inner
        if op == "-z":
            return operand == ""
        if op == "-n":
            return operand != ""
        return None
    if len(inner) == 3:
        left, op, right = inner
        if op in ("-eq", "-ne", "-lt", "-le", "-gt", "-ge"):
            try:
                a, b = int(left), int(right)
            except ValueError:
                return None
            return {
                "-eq": a == b,
                "-ne": a != b,
                "-lt": a < b,
                "-le": a <= b,
                "-gt": a > b,
                "-ge": a >= b,
            }[op]
        if op in ("=", "=="):
            return left == right
        if op == "!=":
            return left != right
    return None


def _condition_value(segment: _Segment):
    """The fixed truth of a condition segment, or None.

    ``false``/``true``/``:`` are fixed; a leading ``!`` inverts
    a fixed condition; a ``[ … ]``/``test`` condition is fixed
    when ``_eval_test`` can decide it from literals alone.
    Anything else — above all a file test, whose answer is the
    whole point of running it — is unknown, and an unknown
    condition's branch is treated as reachable.
    """
    raw = segment.raw_tokens
    start = (
        1
        if raw and raw[0] in ("if", "elif", "while", "until")
        else 0
    )
    negated = False
    for token in raw[start:]:
        if token == "!":
            negated = not negated
        else:
            break
    base = _condition_base(segment)
    if base == "false":
        value: bool | None = False
    elif base in ("true", ":"):
        value = True
    elif base in ("[", "[[", "test"):
        inner = list(segment.tokens[1:])
        if inner and inner[-1] == "]":
            inner.pop()
        value = _eval_test(inner)
    else:
        value = None
    if value is not None and negated:
        value = not value
    return value


def _eval_arithmetic(text: str):
    """Evaluate a literal arithmetic expression, or None.

    Used for ``(( … ))`` conditions: only integer/boolean
    literals and operators are evaluated (via the ast module —
    names, calls and anything else make the expression
    "unknown", never guessed).
    """
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError:
        return None

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, bool)):
                return node.value
            return None
        if isinstance(node, ast.UnaryOp) and isinstance(
            node.op, (ast.USub, ast.UAdd, ast.Not)
        ):
            operand = walk(node.operand)
            if operand is None:
                return None
            if isinstance(node.op, ast.Not):
                return not operand
            return -operand if isinstance(node.op, ast.USub) else +operand
        if isinstance(node, ast.BinOp) and isinstance(
            node.op,
            (ast.Add, ast.Sub, ast.Mult, ast.FloorDiv, ast.Mod),
        ):
            left, right = walk(node.left), walk(node.right)
            if left is None or right is None:
                return None
            if isinstance(node.op, (ast.FloorDiv, ast.Mod)) and right == 0:
                return None
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.FloorDiv):
                return left // right
            return left % right
        if isinstance(node, ast.BoolOp) and isinstance(
            node.op, (ast.And, ast.Or)
        ):
            values = [walk(item) for item in node.values]
            if any(item is None for item in values):
                return None
            if isinstance(node.op, ast.And):
                return all(values)
            return any(values)
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            left = walk(node.left)
            right = walk(node.comparators[0])
            if left is None or right is None:
                return None
            op = node.ops[0]
            if isinstance(op, ast.Eq):
                return left == right
            if isinstance(op, ast.NotEq):
                return left != right
            if isinstance(op, ast.Lt):
                return left < right
            if isinstance(op, ast.LtE):
                return left <= right
            if isinstance(op, ast.Gt):
                return left > right
            if isinstance(op, ast.GtE):
                return left >= right
        return None

    result = walk(tree)
    return None if result is None else bool(result)


def _command_segments(
    text: str, _depth: int = 0, _inherited_dead: bool = False
) -> list[_Segment]:
    """Every command in a shell script, analysed, in order.

    Heredoc bodies are dropped, comments stripped, continuations
    joined, and each logical line split into its individual
    commands. Commands that can never run are marked dead: the
    branch of a constant-false ``if``/``while`` (including
    negated and literal-test conditions), the far side of a
    short-circuited ``&&``/``||``, anything after an
    unconditional ``exit``, and the body of a function the
    script never calls in this scope. A shell run with ``-c``
    executes its string as a script, so the string's commands
    are analysed too, one depth down. A requirement must be met
    by a live command that actually runs — never by text a line
    merely prints, stores in a variable, or trails behind
    a ``#``.
    """
    code = []
    for raw in _drop_heredoc_bodies(text.splitlines()):
        line = raw.strip()
        if line and not line.startswith("#"):
            code.append(_strip_shell_comment(line))
    segments: list[_Segment] = []
    frames: list[dict] = []
    terminated = False
    pending_sep: str | None = None
    subshell_depth = 0
    pending_def: str | None = None
    in_function: str | None = None
    brace_depth = 0
    region: dict | None = None
    case_stack: list[dict] = []
    chain_status: bool | None = None

    def apply_sep(sep: str | None) -> None:
        """Apply a separator's structural effects."""
        nonlocal subshell_depth, pending_sep, region, chain_status
        if sep == "(":
            subshell_depth += 1
            return
        if sep == ")":
            subshell_depth = max(0, subshell_depth - 1)
            if region is not None and subshell_depth == region["base"]:
                frame = region["frame"]
                value = _eval_arithmetic(" ".join(region["texts"]))
                chain_status = value
                if value is not None:
                    frame["untrusted"] = False
                    kind = region["kind"]
                    if kind == "if":
                        if value:
                            frame["dead"], frame["taken"] = False, True
                        else:
                            frame["dead"], frame["taken"] = True, False
                    elif kind == "elif":
                        if frame["taken"] or not value:
                            frame["dead"] = True
                        else:
                            frame["dead"] = False
                            frame["taken"] = True
                    elif kind == "while":
                        frame["dead"] = not value
                    elif kind == "until":
                        frame["dead"] = bool(value)
                region = None
            return
        if sep is not None:
            pending_sep = sep

    carry_operator = False
    for logical in _logical_lines(code):
        parts = _split_segments(logical)
        # A line that ends in &&, || or | continues on the next
        # line in shell — the pending operator (and with it the
        # chain) carries over instead of resetting.
        if not carry_operator:
            pending_sep = None
        for part_index, (seg_text, sep) in enumerate(parts):
            if not seg_text:
                if (
                    sep == ")"
                    and segments
                    and segments[-1].depth == _depth
                    and segments[-1].sep_after is None
                ):
                    # A group close written on its own line
                    # (``(`` ⏎ ``test …`` ⏎ ``) && …``): the
                    # ``)`` lands on an empty part and no segment
                    # would carry it — record it on the group's
                    # last command, where tail reasoning looks.
                    segments[-1] = segments[-1]._replace(
                        sep_after=")"
                    )
                apply_sep(sep)
                continue
            segment = _analyse(seg_text)
            first = segment.raw_tokens[0] if segment.raw_tokens else ""
            # Function definitions: ``name() { … }`` — the name
            # segment is followed, past the ``)``, by a segment
            # opening with ``{``.
            if (
                pending_def is None
                and in_function is None
                and sep == "("
                and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", seg_text)
                and part_index + 1 < len(parts)
                and parts[part_index + 1] == ("", ")")
            ):
                pending_def = seg_text
            if first == "function" and len(segment.raw_tokens) > 1:
                pending_def = segment.raw_tokens[1]
                if "{" in segment.raw_tokens:
                    in_function = pending_def
                    brace_depth = 1
                    pending_def = None
            func = in_function
            if pending_def is not None and first == "{":
                in_function = pending_def
                brace_depth = 1
                func = in_function
                pending_def = None
            elif in_function is not None:
                if first == "{":
                    brace_depth += 1
                elif first == "}":
                    brace_depth -= 1
            # Case statements: track which arm a segment sits
            # in and whether that arm can run.
            case_dead = False
            case_unproven = False
            if first == "esac" and case_stack:
                case_stack.pop()
            elif first == "case":
                raw = segment.raw_tokens
                subject = None
                if len(raw) >= 2:
                    candidate = raw[1]
                    if "$" not in candidate and len(raw) <= 3:
                        subject = candidate
                case_stack.append(
                    {
                        "subject": subject,
                        "matched": False,
                        "expect_pattern": True,
                        "patterns": [],
                        "in_arm": False,
                        "arm_dead": False,
                        "arm_unproven": True,
                    }
                )
                case_unproven = True
            elif case_stack:
                ctx = case_stack[-1]
                if ctx["expect_pattern"] and first != "case":
                    ctx["patterns"].append(
                        segment.tokens[0]
                        if len(segment.tokens) == 1
                        else seg_text
                    )
                    case_unproven = True
                    if sep == ")":
                        patterns = ctx["patterns"]
                        ctx["patterns"] = []
                        ctx["expect_pattern"] = False
                        ctx["in_arm"] = True
                        if ctx["subject"] is None or any(
                            "$" in p for p in patterns
                        ):
                            ctx["arm_dead"] = False
                            ctx["arm_unproven"] = True
                        else:
                            hit = any(
                                p == "*"
                                or fnmatch.fnmatchcase(ctx["subject"], p)
                                for p in patterns
                            )
                            if hit and not ctx["matched"]:
                                ctx["arm_dead"] = False
                                ctx["arm_unproven"] = False
                                ctx["matched"] = True
                            else:
                                ctx["arm_dead"] = True
                                ctx["arm_unproven"] = False
                elif ctx["in_arm"]:
                    case_dead = ctx["arm_dead"]
                    case_unproven = ctx["arm_unproven"]
                    if (
                        sep == ";"
                        and part_index + 1 < len(parts)
                        and parts[part_index + 1] == ("", ";")
                    ):
                        # ``;;`` — the arm ends here.
                        ctx["in_arm"] = False
                        ctx["expect_pattern"] = True
            # Chain semantics for ``&&``/``||``: this command
            # runs only if the chain status so far lets it, and
            # a command that is skipped leaves the chain status
            # exactly as it was — a dead ``true`` in the middle
            # of a chain resurrects nothing.
            chain_dead = False
            chain_unproven = False
            if pending_sep == "&&":
                if chain_status is True:
                    chain_ran: bool | None = True
                elif chain_status is False:
                    chain_ran = False
                    chain_dead = True
                else:
                    chain_ran = None
                    chain_unproven = True
            elif pending_sep == "||":
                if chain_status is False:
                    chain_ran = True
                elif chain_status is True:
                    chain_ran = False
                    chain_dead = True
                else:
                    chain_ran = None
                    chain_unproven = True
            else:
                chain_ran = True
            frame_dead = any(frame["dead"] for frame in frames)
            if first in ("else", "elif") and frames:
                # An ``else`` segment belongs to the ELSE
                # branch, and an ``elif`` segment carries
                # its condition command in the segment
                # itself: both execute exactly when no
                # earlier branch was taken, so their frame
                # state is the post-branch-switch one — dead
                # exactly when an earlier branch was taken —
                # not the previous branch's state in force
                # when the segment started. Reading the
                # pre-switch state inverts the segment both
                # ways (rounds 34/36): a terminator or an
                # unseen execution riding an else was
                # credited or voided backwards, and the
                # elif condition — a direct ``elif exit``,
                # a helper call, an eval, a source — escaped
                # the termination and unseen models entirely
                # behind the dead stamp of a proven-false
                # first branch.
                frame_dead = (
                    any(
                        frame["dead"] for frame in frames[:-1]
                    )
                    or frames[-1]["taken"]
                )
            dead = (
                _inherited_dead
                or terminated
                or chain_dead
                or case_dead
                or frame_dead
            )
            unproven = (
                chain_unproven
                or case_unproven
                or any(frame.get("untrusted") for frame in frames)
            )
            segment = segment._replace(
                dead=dead,
                depth=_depth,
                sep_after=sep,
                sep_before=pending_sep,
                subshell=subshell_depth,
                in_pipeline=(pending_sep == "|" or sep == "|"),
                func=func,
                unproven=unproven,
            )
            pending_sep = None
            segments.append(segment)
            if region is not None:
                region["texts"].append(seg_text)
            if in_function is not None and first == "}" and brace_depth == 0:
                in_function = None
            if (
                segment.kind == "command"
                and segment.tokens
                and _basename(segment.tokens[0]) in _SHELL_COMMANDS
                and "-c" in segment.tokens
            ):
                index = segment.tokens.index("-c")
                if index + 1 < len(segment.tokens):
                    segments.extend(
                        _command_segments(
                            segment.tokens[index + 1],
                            _depth + 1,
                            dead,
                        )
                    )
            effective = (
                _basename(segment.tokens[0]) if segment.tokens else ""
            )
            if first in ("if", "while", "until", "elif"):
                if not segment.tokens and sep == "(":
                    # A parenthesised condition — (( … )) or a
                    # subshell command. Its parts arrive as their
                    # own segments; until they are all in and the
                    # expression proves constant, the branch is
                    # untrusted: reachable, but nothing inside it
                    # can prove anything.
                    if first == "elif" and frames:
                        frame = frames[-1]
                    else:
                        frame = {"dead": False, "taken": False}
                        frames.append(frame)
                    frame["untrusted"] = True
                    region = {
                        "frame": frame,
                        "kind": first,
                        "base": subshell_depth,
                        "texts": [],
                    }
                elif first == "if":
                    value = _condition_value(segment)
                    if value is True:
                        frames.append({"dead": False, "taken": True})
                    elif value is False:
                        frames.append({"dead": True, "taken": False})
                    else:
                        frames.append({"dead": False, "taken": False})
                elif first == "elif" and frames:
                    frame = frames[-1]
                    value = _condition_value(segment)
                    if frame["taken"] or value is False:
                        frame["dead"] = True
                    elif value is True:
                        frame["dead"] = False
                        frame["taken"] = True
                    else:
                        frame["dead"] = False
                elif first in ("while", "until"):
                    # A loop body proves something only when
                    # the loop is KNOWN to run at least once:
                    # ``while`` on a constant-true condition,
                    # ``until`` on a constant-false one. Any
                    # other condition — ``while read x; do …
                    # done < /dev/null`` above all — may run
                    # the body zero times, so the body is not
                    # credited, exactly as with ``for`` lists
                    # the checker cannot resolve.
                    value = _condition_value(segment)
                    if first == "while":
                        branch_dead = value is not True
                    else:
                        branch_dead = value is not False
                    frames.append({"dead": branch_dead, "taken": False})
            elif (
                first in ("then", "do")
                and segment.raw_tokens[1:2]
                and segment.raw_tokens[1] in ("if", "while", "until")
            ):
                # A construct riding the then/do segment
                # (round 38): ``then if …``, ``do while …`` —
                # the opener keyword is the segment's SECOND
                # word, so the branches above never see it
                # and no frame opens; the inner chain's
                # elif/else segments are then judged against
                # the OUTER frame — the branch-switch
                # inversion, one nesting level down. Open
                # the frame here, with the same state the
                # standalone opener computes (the condition
                # reads the same either way: it is judged
                # from the effective tokens).
                kind = segment.raw_tokens[1]
                value = _condition_value(segment)
                if kind == "if":
                    if value is True:
                        frames.append({"dead": False, "taken": True})
                    elif value is False:
                        frames.append({"dead": True, "taken": False})
                    else:
                        frames.append({"dead": False, "taken": False})
                else:
                    if kind == "while":
                        branch_dead = value is not True
                    else:
                        branch_dead = value is not False
                    frames.append({"dead": branch_dead, "taken": False})
            elif first == "do" and segment.raw_tokens[1:2] == ["for"]:
                # ``do for …`` written on one line: the loop
                # header rides in the do segment, but it is a
                # for loop all the same — without its frame,
                # the inner ``done`` pops the OUTER loop's
                # frame and everything after it is misjudged.
                words = segment.raw_tokens[2:]
                if len(words) == 1 or (
                    len(words) == 2 and words[1] == "in"
                ):
                    unproven = True
                elif len(words) >= 2 and words[1] == "in":
                    unproven = not any(
                        "$" not in word and "`" not in word
                        for word in words[2:]
                    )
                else:
                    unproven = False
                frames.append({"dead": unproven, "taken": False})
            elif first == "for":
                # A for loop's body proves something only when
                # the loop is KNOWN to run: an empty list
                # (``for i in; do …; done``, or bare ``for i``
                # iterating positional parameters the hook is
                # run without) runs it zero times, and a list
                # of expansions only (``for i in $EMPTY``) may
                # — the checker cannot know the values, so it
                # does not credit the body. One fully literal
                # word is enough to know better (see below).
                words = segment.raw_tokens[1:]
                if len(words) == 1 or (
                    len(words) == 2 and words[1] == "in"
                ):
                    unproven = True
                elif len(words) >= 2 and words[1] == "in":
                    # Provable when at least one list word is
                    # fully literal: word expansion of a
                    # literal always yields a word in POSIX
                    # sh, so the body runs at least once —
                    # ``for i in 1 $EXTRA`` really does run.
                    unproven = not any(
                        "$" not in word and "`" not in word
                        for word in words[2:]
                    )
                else:
                    unproven = False
                frames.append({"dead": unproven, "taken": False})
            elif first == "else" and frames:
                frames[-1]["dead"] = frames[-1]["taken"]
            elif first == "fi" and frames:
                frames.pop()
            elif first == "done" and frames:
                frames.pop()
            if region is None and first not in (
                "if",
                "elif",
                "while",
                "until",
                "then",
                "else",
                "fi",
                "do",
                "done",
                "case",
                "esac",
                "{",
                "}",
                "function",
            ):
                # The chain status after this command: its own
                # status if it ran, unknown if it may have run,
                # and unchanged if it was skipped.
                if chain_ran is True:
                    chain_status = _condition_value(segment)
                elif chain_ran is None:
                    chain_status = None
            if (
                not frames
                and func is None
                and not dead
                and not unproven
                and segment.sep_before not in ("&&", "||")
                and effective in ("exit", "return")
            ):
                # The ``unproven`` guard matters for case
                # arms (round 40): case arms open no frame,
                # so an exit in an arm the checker cannot
                # prove is taken latched termination for
                # everything after ``esac`` — while the
                # termination model states an unproven
                # terminator is never a point. A proven
                # arm's exit still latches, as it should.
                terminated = True
            apply_sep(sep)
        if pending_sep == "&" and segments and segments[-1].depth == _depth:
            # The line ended in ``&`` with no command after it:
            # the separator landed on an empty part (the case
            # that matters is a paren group — ``( … ) &`` — whose
            # inner last command carries only ``)``). The whole
            # statement is a background job; remember it on the
            # line's last segment, where tail reasoning looks.
            segments[-1] = segments[-1]._replace(stmt_bg=True)
        carry_operator = next(
            (
                sep
                for _text, sep in reversed(parts)
                if sep is not None
            ),
            None,
        ) in ("&&", "||", "|")
    # A function body counts in full only if the function is
    # actually called, later in the same scope, by a live
    # top-level command outside any function body. A body called
    # only from a subshell still runs, but its exits and
    # assignments die with that subshell, so it is marked
    # unproven; a body never called is dead outright.
    own = [i for i, s in enumerate(segments) if s.depth == _depth]
    defined: dict[str, int] = {}
    for i in own:
        if segments[i].func is not None and segments[i].func not in defined:
            defined[segments[i].func] = i
    for name in defined:
        # A redefined function runs its LAST body: every
        # earlier body under the same name is unreachable, so
        # its proofs count for nothing.
        starts = [
            i
            for i in own
            if segments[i].func is None
            and segments[i].tokens == [name]
            and segments[i].sep_after == "("
        ]
        if len(starts) > 1:
            last = starts[-1]
            for i in own:
                if segments[i].func == name and i < last:
                    segments[i] = segments[i]._replace(dead=True)
            defined[name] = last
    # An eval whose argument is not a plain, visible call
    # evaluates text the checker cannot see — it could
    # redefine any function, from any indirection: a variable
    # (``eval "$E"``), a substitution (``eval $(…)``), an
    # escaped spelling, a wrapper prefix (``command eval``),
    # or a helper function's body. From the point such an
    # eval EXECUTES (its own position at the top level; a
    # counting call's position when it sits in a body), every
    # function body defined before that point stops proving:
    # what runs after it is no longer knowable from the file.
    poison_points: list[int] = []
    for i in own:
        segment = segments[i]
        if segment.dead or not _executes_unseen(segment):
            continue
        if segment.func is None:
            poison_points.append(i)
            continue
        calls = [
            j
            for j in own
            if j > i
            and segments[j].func is None
            and not segments[j].dead
            and segments[j].tokens
            and segments[j].tokens[0] == segment.func
            and _raw_call_name(segments[j])
            in (segment.func, "eval")
        ]
        if calls:
            poison_points.append(min(calls))
    for point in poison_points:
        for name, def_index in defined.items():
            if def_index < point:
                for j in own:
                    if segments[j].func == name:
                        segments[j] = segments[j]._replace(dead=True)
    for name, def_index in defined.items():
        calls = [
            (i, segments[i])
            for i in own
            if i > def_index
            and segments[i].func is None
            and not segments[i].dead
            and segments[i].tokens
            and segments[i].tokens[0] == name
            and _raw_call_name(segments[i]) in (name, "eval")
        ]
        if any(
            _call_counts(segments, i, call) for i, call in calls
        ):
            continue
        for i in own:
            if segments[i].func == name:
                if calls:
                    segments[i] = segments[i]._replace(unproven=True)
                else:
                    segments[i] = segments[i]._replace(dead=True)
    _apply_termination(segments)
    return segments


def _apply_termination(segments: list[_Segment]) -> None:
    """Mark everything after an executing terminator dead.

    Two commands end the script: ``exec`` (it replaces
    the shell — and a FAILED exec ends a non-interactive
    shell too) and ``exit``. The segmenter's in-loop rule
    caught only their bare sequential top-level spellings;
    termination is really an execution-point question,
    like unseen text, so both run through the same call
    graph (round 32 — before that, exec reached only one
    helper deep through plain calls, and exit had no
    execution-point treatment at all):

    * a terminator at the top level counts at its own
      position when it is live in the main shell (not
      dead, not in a subshell or a pipeline — those end
      only the child);
    * a terminator inside a helper counts at the call
      points the shared graph anchors for that helper —
      computed from execution, not the body's dead flag,
      so condition calls (``if h; then``), negations
      (``! h``), eval calls, and chains two helpers deep
      all count, exactly as they do for unseen text.

    A terminator counts only where it is PROVEN reached:
    behind an unevaluable condition it ends one path and
    the script goes on down the others, so an unproven
    terminator — or a body terminator whose call chain
    anchors only at unproven calls — is never a point.
    One in a provably dead branch is already dead and
    never a point either. An exec with no command at all
    (``exec``, ``exec >/dev/null``) terminates nothing —
    it reassigns file descriptors and the script goes
    on. ``return`` is NOT a terminator: it ends only the
    function, and the script goes on (shell-proved,
    round 32).
    """
    redirection = re.compile(r"^(\d*[<>]{1,2}|&>{1,2}|\d+>&\d+)")

    def is_terminator(segment: _Segment) -> bool:
        if not (
            segment.kind == "command"
            and segment.tokens
            and segment.subshell == 0
            and not segment.in_pipeline
        ):
            return False
        head = _basename(segment.tokens[0])
        if head == "exit":
            return True
        if head != "exec":
            return False
        # An exec whose remaining words are all
        # redirections runs no command: it reassigns
        # file descriptors and the script goes on.
        return any(
            not redirection.match(token)
            for token in segment.tokens[1:]
        )

    # A termination point must be PROVEN reached on
    # every path: a terminator behind an unevaluable
    # condition ends only that branch's path — the
    # script goes on down the others (the pristine
    # scripts' guard blocks are exactly this shape).
    # The segment flags alone cannot say this — the
    # frame model deliberately leaves unknown-branch
    # segments live, because a PROOF there is
    # existential (one path suffices) while termination
    # is universal (every path must end). So the branch
    # structure is replayed here, with the same
    # provability rules the segmenter uses: an if/elif
    # branch is proven on a constant-true condition
    # after only constant-false ones; an else is proven
    # when every condition before it is constant-false;
    # a loop body is proven for while-on-true,
    # until-on-false, and a for list with a fully
    # literal word. Case arms need no replay — the
    # segmenter already marks unproven arms on the
    # segments themselves. A second replay
    # (``_branch_cover_points``) joins the branches:
    # a chain whose every reachable branch terminates
    # and which cannot fall through is a termination
    # point at its ``fi`` (round 40).
    blocked: set = set()
    frames: list = []
    for index, segment in enumerate(segments):
        first = segment.raw_tokens[0] if segment.raw_tokens else ""
        if first == "else" and frames:
            # The else segment is judged in the else
            # branch (round 34): switch the frame before
            # reading it, as the segmenter now does.
            frames[-1]["unproven"] = not frames[-1]["all_false"]
        elif first == "elif" and frames:
            # The elif segment carries its condition
            # (round 36): the condition is proven reached
            # exactly when every earlier condition is
            # constant-false — only then does it run on
            # every path. (The branch-state transition
            # for the segments after it happens in the
            # elif handling below, unchanged.)
            frames[-1]["unproven"] = not frames[-1]["all_false"]
        if any(frame["unproven"] for frame in frames):
            blocked.add(index)
        if first == "if":
            value = _condition_value(segment)
            frames.append(
                {
                    "unproven": value is not True,
                    "all_false": value is False,
                }
            )
        elif (
            first in ("then", "do")
            and segment.raw_tokens[1:2]
            and segment.raw_tokens[1] in ("if", "while", "until")
        ):
            # A construct riding the then/do segment
            # (round 38): open its frame here too, or the
            # inner chain's switches are replayed against
            # the outer frame — the same inversion the
            # segmenter fixed.
            kind = segment.raw_tokens[1]
            value = _condition_value(segment)
            if kind == "if":
                frames.append(
                    {
                        "unproven": value is not True,
                        "all_false": value is False,
                    }
                )
            else:
                proven = (
                    value is True
                    if kind == "while"
                    else value is False
                )
                frames.append(
                    {"unproven": not proven, "all_false": False}
                )
        elif first == "elif" and frames:
            frame = frames[-1]
            value = _condition_value(segment)
            frame["unproven"] = not (
                frame["all_false"] and value is True
            )
            frame["all_false"] = (
                frame["all_false"] and value is False
            )
        elif first == "else" and frames:
            frames[-1]["unproven"] = not frames[-1]["all_false"]
        elif first in ("while", "until"):
            value = _condition_value(segment)
            proven = (
                value is True
                if first == "while"
                else value is False
            )
            frames.append(
                {"unproven": not proven, "all_false": False}
            )
        elif first == "for" or (
            first == "do" and segment.raw_tokens[1:2] == ["for"]
        ):
            words = (
                segment.raw_tokens[1:]
                if first == "for"
                else segment.raw_tokens[2:]
            )
            if len(words) == 1 or (
                len(words) == 2 and words[1] == "in"
            ):
                proven = False
            elif len(words) >= 2 and words[1] == "in":
                proven = any(
                    "$" not in word and "`" not in word
                    for word in words[2:]
                )
            else:
                proven = True
            frames.append(
                {"unproven": not proven, "all_false": False}
            )
        elif first in ("fi", "done") and frames:
            frames.pop()

    points: list[int] = []
    bodies: set = set()
    for index, segment in enumerate(segments):
        if not is_terminator(segment):
            continue
        if segment.unproven or index in blocked:
            continue
        if segment.func is None:
            if not segment.dead:
                points.append(index)
        else:
            bodies.add(segment.func)
    if bodies:
        anchors = _execution_anchors(
            segments, blocked=frozenset(blocked)
        )
        for name in bodies:
            points += anchors(
                name, frozenset({name}), proven_only=True
            )
    points += _branch_cover_points(segments, bodies, is_terminator)
    if not points:
        return
    first = min(points)
    for index in range(first + 1, len(segments)):
        if not segments[index].dead:
            segments[index] = segments[index]._replace(dead=True)


def _branch_cover_points(
    segments: list[_Segment], bodies: set, is_terminator
) -> list[int]:
    """Termination points earned by BRANCH COVER (round 40).

    A terminator behind an unevaluable condition ends
    only its branch's path — but when EVERY reachable
    branch of an if-chain ends that way and the chain
    cannot fall through (a covering ``else``, or a
    proven-true condition after only proven-false or
    covered ones), every path through the chain ends,
    and the chain is a termination point at its ``fi``.
    The per-segment model never joins the branches;
    this pass replays the branch structure a second
    time and joins them:

    * a terminator (or a call to a body that always
      terminates) inside a branch marks that branch
      terminated — branch-relative: the mark is about
      what happens WHEN the branch runs;
    * the event propagates out through a PROVEN loop
      (the loop, when entered, runs the terminator)
      and stops at an unproven loop, a case, or any
      other frame;
    * at ``fi`` the chain covers when fall-through is
      impossible and every reachable branch is marked;
      a covering chain inside a branch fires the same
      event one level out, and a covering chain inside
      a function body registers the body as always
      terminating (definitions precede their calls, so
      later call sites see it).

    Condition segments themselves never fire events —
    a terminator AS a condition (``elif exit``) is the
    points model's business, already handled there.
    """
    points: list[int] = []
    terminating = set(bodies)
    frames: list = []

    def fire(index: int, func) -> None:
        for frame in reversed(frames):
            if frame["kind"] == "if":
                if frame["func"] == func:
                    frame["branches"][-1]["terminated"] = True
                return
            if frame["kind"] == "loop":
                if frame["proven"] and frame["func"] == func:
                    continue
                return
            return
        if func is None:
            points.append(index)

    def invoked_name(segment: _Segment):
        name = _call_head_name(segment)
        if name is None and _eval_is_plain_call(segment):
            name = _eval_invoked_name(segment)
        return name

    for index, segment in enumerate(segments):
        first = segment.raw_tokens[0] if segment.raw_tokens else ""
        top = frames[-1] if frames else None
        if first == "else" and top is not None and top["kind"] == "if":
            # The else switch comes FIRST (as in the main
            # replay): a terminator riding the else segment
            # belongs to the else branch, and its event
            # must mark that branch, not the previous one.
            top["branches"].append(
                {"terminated": False, "reachable": top["ft"]}
            )
            top["ft"] = False
        # Events (before this segment's frame transitions).
        if first not in ("if", "elif", "while", "until", "fi", "done"):
            if (
                is_terminator(segment)
                and not segment.unproven
            ):
                fire(index, segment.func)
            elif (
                segment.kind == "command"
                and segment.tokens
                and invoked_name(segment) in terminating
            ):
                fire(index, segment.func)
        # Frame transitions.
        if first == "if" or (
            first in ("then", "do")
            and segment.raw_tokens[1:2] == ["if"]
        ):
            value = _condition_value(segment)
            frames.append(
                {
                    "kind": "if",
                    "func": segment.func,
                    "branches": [
                        {
                            "terminated": False,
                            "reachable": value is not False,
                        }
                    ],
                    "ft": value is not True,
                }
            )
        elif first == "elif" and top is not None and top["kind"] == "if":
            value = _condition_value(segment)
            top["branches"].append(
                {
                    "terminated": False,
                    "reachable": top["ft"] and value is not False,
                }
            )
            top["ft"] = top["ft"] and value is not True
        elif first in ("while", "until") or (
            first in ("then", "do")
            and segment.raw_tokens[1:2]
            and segment.raw_tokens[1] in ("while", "until")
        ):
            kind = (
                first
                if first in ("while", "until")
                else segment.raw_tokens[1]
            )
            value = _condition_value(segment)
            proven = (
                value is True if kind == "while" else value is False
            )
            frames.append(
                {"kind": "loop", "func": segment.func, "proven": proven}
            )
        elif first == "for" or (
            first == "do" and segment.raw_tokens[1:2] == ["for"]
        ):
            words = (
                segment.raw_tokens[1:]
                if first == "for"
                else segment.raw_tokens[2:]
            )
            if len(words) == 1 or (
                len(words) == 2 and words[1] == "in"
            ):
                proven = False
            elif len(words) >= 2 and words[1] == "in":
                proven = any(
                    "$" not in word and "`" not in word
                    for word in words[2:]
                )
            else:
                proven = True
            frames.append(
                {"kind": "loop", "func": segment.func, "proven": proven}
            )
        elif first == "fi" and frames:
            frame = frames.pop()
            if frame["kind"] != "if":
                continue
            reachable = [
                branch
                for branch in frame["branches"]
                if branch["reachable"]
            ]
            if (
                reachable
                and not frame["ft"]
                and all(
                    branch["terminated"] for branch in reachable
                )
            ):
                if frame["func"] is not None:
                    terminating.add(frame["func"])
                else:
                    fire(index, None)
        elif first == "done" and frames:
            frames.pop()
    return points


def _command_lines(text: str) -> list[str]:
    """The command lines of a script (see ``_command_segments``),
    as text, with output/no-op commands dropped."""
    return [
        segment.text
        for segment in _command_segments(text)
        if segment.kind not in ("noop", "empty")
    ]


def _flag_value(tokens: list[str], flag: str):
    """The value a flag carries in a token list, or None.

    Matches the flag as its own token (``--iso-volume X``) or in
    ``--iso-volume=X`` form — never as a substring of some other
    word.
    """
    for index, token in enumerate(tokens):
        if token == flag and index + 1 < len(tokens):
            return tokens[index + 1]
        if token.startswith(flag + "="):
            return token[len(flag) + 1 :]
    return None


def _path_components(value: str) -> list[str]:
    """The colon-separated components of a path-list value.

    Shell expansions (``${PYTHONPATH:+:$PYTHONPATH}`` and
    friends) are removed first: what remains is the literal
    directories the value puts on the list, so a component must
    match exactly — ``/opt/beerus_evil`` and ``/tmp/opt/beerus``
    are not ``/opt/beerus``.
    """
    value = re.sub(r"\$\{[^}]*\}", "", value)
    value = re.sub(r"\$[A-Za-z_][A-Za-z0-9_]*", "", value)
    return value.split(":")


def _segment_tests_path(segment: _Segment, path: str) -> bool:
    """True when a segment actually tests that a file exists:
    a ``[``/``test`` command with an -f or -e flag naming the
    file by its exact full path. Testing a same-named file
    somewhere else proves nothing about this one, and a needle
    riding as an argument to some other command (mkdir, tee, an
    echo behind a wrapper) tests nothing at all."""
    tokens = segment.tokens
    if not tokens or _basename(tokens[0]) not in ("[", "[[", "test"):
        return False
    if not any(token in ("-f", "-e") for token in tokens):
        return False
    return any(token == path for token in tokens)


def _leading_negation(segment: _Segment) -> bool:
    """True when an odd number of ``!`` lead the segment's
    command — possibly sitting behind the keywords the
    segmenter strips (``then ! test …``, ``{ ! test …; }``).
    A ``!`` in argument position (``echo !``) negates nothing.
    """
    negated = False
    for token in segment.raw_tokens:
        if token in _KEYWORDS:
            if token == "!":
                negated = not negated
            continue
        break
    return negated


def _inner_test_negation(segment: _Segment) -> bool:
    """True when a test's own expression opens with ``!``
    (``test ! -f …``, ``[ ! -f … ]``): the test then returns
    success exactly when the file is missing."""
    return bool(
        segment.tokens
        and _basename(segment.tokens[0]) in ("[", "[[", "test")
        and len(segment.tokens) > 1
        and segment.tokens[1] == "!"
    )


def _segment_negated(segment: _Segment) -> bool:
    """True when the segment's result is the inverse of the
    plain test's — the composed truth, for branch logic."""
    return _leading_negation(segment) != _inner_test_negation(segment)


def _segment_exempt_negated(segment: _Segment) -> bool:
    """True when ``set -e`` cannot act on the segment's result:
    a leading ``!`` exempts it outright (however the inner
    expression reads — ``! test ! -f …`` never stops a hook,
    present or absent), and an inner test negation inverts
    the result just as surely when there is no leading ``!``.
    """
    return _leading_negation(segment) or _inner_test_negation(segment)


def _is_definition_segment(segment: _Segment) -> bool:
    """True for the name segment of a ``name() { … }``
    definition — it declares the function; it is not a call,
    although its raw name matches one."""
    return (
        segment.sep_after == "("
        and len(segment.tokens) == 1
        and segment.raw_tokens == segment.tokens
    )


def _raw_call_name(segment: _Segment) -> str | None:
    """The name a segment actually invokes, read from its RAW
    tokens: the first word after structural keywords and
    command-prefix assignments. The segmenter's effective
    tokens strip wrappers (``command``, ``env``, ``nice``,
    ``timeout`` …), but the shell's function lookup does not
    survive them — ``command prove`` never runs a function
    named prove — so a call is recognised only when the raw
    head IS the name.

    ``eval`` is transparent, not a credit of its own: it
    executes its argument as shell input, so the invoked name
    is the argument's first word AS WRITTEN — ``eval prove``
    calls prove, ``eval command prove`` calls an external
    program (the wrapper inside the eval suppresses the
    function just as it does outside), and ``eval 'prove'``
    with a multi-word string is not a plain call at all.
    """
    raw = segment.raw_tokens
    index = 0
    while index < len(raw) and (
        raw[index] in ("{", "then", "else", "do")
        or _ASSIGNMENT.match(raw[index])
    ):
        index += 1
    if index >= len(raw):
        return None
    head = raw[index]
    if head == "eval":
        return raw[index + 1] if index + 1 < len(raw) else None
    return head


def _raw_head(segment: _Segment) -> str | None:
    """The segment's first word as written, after structural
    keywords and command-prefix assignments — before any
    ``eval`` transparency is applied."""
    for token in segment.raw_tokens:
        if token in ("{", "then", "else", "do") or _ASSIGNMENT.match(
            token
        ):
            continue
        return token
    return None


_PLAIN_WORD = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.,/:=+@-]*$")


def _invokes_eval(segment: _Segment) -> bool:
    """True when the segment runs ``eval`` — as its head, or
    behind a wrapper (``command eval …``): the leading words
    the segmenter strips are walked as written, and any of
    them being ``eval`` means the segment's real behaviour is
    whatever the evaluated text says."""
    for token in segment.raw_tokens:
        base = _basename(token)
        if (
            token in _KEYWORDS
            or base in _WRAPPER_COMMANDS
            or _ASSIGNMENT.match(token)
        ):
            if base == "eval":
                return True
            continue
        return False
    return False


def _executes_unseen(segment: _Segment) -> bool:
    """True when a segment runs text the checker cannot
    see, in the current shell: an ``eval`` that is not a
    provably plain call, or a dot-source (``. file`` /
    ``source file``) — the sourced file's contents are
    exactly as invisible as eval's argument (round 22)."""
    if _invokes_eval(segment) and not _eval_is_plain_call(segment):
        return True
    return bool(segment.tokens) and _basename(segment.tokens[0]) in (
        ".",
        "source",
    )


def _eval_is_plain_call(segment: _Segment) -> bool:
    """True when an eval segment is one of the forms proven
    genuine: ``eval name`` (optionally with plain literal
    arguments) — the evaluated text is exactly a call to a
    visible name. Anything else — a variable, a command
    substitution, a quoted program, an escaped spelling —
    evaluates text the checker cannot see, and is treated
    as unresolvable."""
    raw = segment.raw_tokens
    index = 0
    while index < len(raw) and _basename(raw[index]) != "eval":
        index += 1
    args = raw[index + 1 :] if index < len(raw) else []
    if not args:
        return False
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args[0]):
        return False
    return all(_PLAIN_WORD.match(arg) for arg in args[1:])


def _is_group_closer(segment: _Segment) -> bool:
    """True for a segment that only closes a brace group."""
    return bool(segment.raw_tokens) and all(
        token == "}" for token in segment.raw_tokens
    )


def _effective_tail(segments: list[_Segment], index: int) -> str | None:
    """The separator that ends this segment's statement value.

    A command inside a group contributes the GROUP's status,
    so the operator that governs it is the one after the
    group's close, not the separator written beside it:
    ``( test -f … ) && echo`` is governed by ``&&`` although
    the test's own separator is ``)``, and ``{ test -f …; } &``
    by ``&`` although its own is ``;``. Follows group closes
    outward until a real statement separator appears.
    """
    tail = segments[index].sep_after
    if tail is None and segments[index].stmt_bg:
        return "&"
    j = index
    for _ in range(16):
        if tail == ")":
            nxt = segments[j + 1] if j + 1 < len(segments) else None
            if nxt is None:
                return "&" if segments[j].stmt_bg else None
            j += 1
            if _is_group_closer(nxt):
                # The close we crossed lands inside an outer
                # brace group (``{ ( test … ); } &``): the
                # governing operator is the closer's own tail.
                tail = nxt.sep_after
                if tail is None and nxt.stmt_bg:
                    tail = "&"
                continue
            tail = nxt.sep_before
            if tail is None and segments[j - 1].stmt_bg:
                tail = "&"
            continue
        if tail == ";":
            nxt = segments[j + 1] if j + 1 < len(segments) else None
            if nxt is not None and _is_group_closer(nxt):
                tail = nxt.sep_after
                if tail is None and nxt.stmt_bg:
                    tail = "&"
                j += 1
                continue
            return tail
        return tail
    return tail


def _enclosing_group_tail(
    segments: list[_Segment], index: int
) -> str | None:
    """The tail of the group that ENCLOSES this segment, or
    None when it sits in no group (or in no group whose tail
    can be determined). A test that is not the last command
    in its group contributes nothing to the group's status —
    but the group's POSITION still decides whether ``set -e``
    is in force inside it: a group in an exempt position
    (piped, backgrounded, a non-final chain member) switches
    errexit off for everything inside, first command or last.
    Paren groups are located exactly, through the subshell
    depth the segmenter records; brace groups by scanning
    for the unmatched opener, bounded inside a function
    body by the body's own opening brace."""
    segment = segments[index]
    if segment.subshell > 0:
        level = segment.subshell
        for k in range(index + 1, len(segments)):
            other = segments[k]
            if other.depth != segment.depth:
                continue
            if other.sep_after == "(":
                level += 1
            elif other.sep_after == ")":
                level -= 1
                if level < segment.subshell:
                    return _effective_tail(segments, k)
        return None
    # The brace scan's lower bound: at the top level there is
    # none; inside a function body the scan must not mistake
    # the body's own opening brace for a group's — a body IS
    # braces, which is exactly why group tails inside bodies
    # stayed invisible while the paren form (located through
    # the subshell depth) worked there all along.
    bound = -1
    if segment.func is not None:
        defs = [
            k
            for k in range(index)
            if segments[k].func is None
            and segments[k].tokens == [segment.func]
            and segments[k].sep_after == "("
        ]
        if defs:
            for k in range(defs[-1] + 1, index):
                if (
                    segments[k].func == segment.func
                    and segments[k].raw_tokens == ["{"]
                ):
                    bound = k
                    break
    if (
        segment.raw_tokens
        and segment.raw_tokens[0] == "{"
        and index != bound
    ):
        opener = index
    else:
        depth = 0
        opener = None
        for k in range(index - 1, bound, -1):
            other = segments[k]
            if (
                other.func != segment.func
                or other.depth != segment.depth
            ):
                break
            if _is_group_closer(other):
                depth += 1
                continue
            if other.raw_tokens and other.raw_tokens[0] == "{":
                if depth == 0:
                    opener = k
                    break
                depth -= 1
        if opener is None:
            return None
    depth = 0
    for k in range(opener + 1, len(segments)):
        other = segments[k]
        if other.func != segment.func:
            break
        if _is_group_closer(other):
            if depth == 0:
                return _effective_tail(segments, k)
            depth -= 1
        elif other.raw_tokens and other.raw_tokens[0] == "{":
            depth += 1
    return None


def _statement_negated(segments: list[_Segment], index: int) -> bool:
    """True when this segment's statement is inverted by ``!``,
    wherever the ``!`` sits: on the segment itself, on the head
    of its pipeline (``! true | test …``), or on the group it
    opens the body of (``! ( test … )``). The exemption notion
    is used, not the composed truth: a leading ``!`` exempts
    the statement from ``set -e`` however the inner expression
    reads (``! test ! -f …`` never stops a hook)."""
    if _segment_exempt_negated(segments[index]):
        return True
    head = index
    while head > 0 and segments[head].sep_before == "|":
        head -= 1
    if _segment_exempt_negated(segments[head]):
        return True
    if head > 0:
        prev = segments[head - 1]
        if (
            prev.sep_after == "("
            and prev.kind == "empty"
            and prev.raw_tokens
            and all(token == "!" for token in prev.raw_tokens)
            and len(prev.raw_tokens) % 2 == 1
        ):
            return True
    return False


def _command_substitution_opener(
    segments: list[_Segment], index: int
) -> _Segment | None:
    """The segment that opened the ``$( … )`` group this
    segment sits in, or None. The nearest unmatched ``(``
    before it belongs to a substitution when the segment that
    carries it ends in ``$`` (``echo $(…``, ``X=$(…``)."""
    balance = 0
    for k in range(index - 1, -1, -1):
        other = segments[k]
        if other.sep_after == ")":
            balance += 1
        elif other.sep_after == "(":
            if balance == 0:
                if other.text.rstrip().endswith("$"):
                    return other
                return None
            balance -= 1
    return None


def _call_counts(
    segments: list[_Segment], index: int, segment: _Segment
) -> bool:
    """True when a function call runs its body with ``set -e``
    actually in force. In every exempt context — the call is
    negated, is itself a condition, sits in a subshell or a
    pipeline, or is a non-final or backgrounded chain member —
    the shell switches ``set -e`` OFF for the whole body, so
    enforcement proofs inside the body would not hold there.
    A body counts only if at least one call counts."""
    if segment.unproven or segment.subshell != 0 or segment.in_pipeline:
        return False
    if segment.tokens and _raw_call_name(segment) not in (
        segment.tokens[0],
        "eval",
    ):
        # A wrapper-prefixed segment never invokes the function
        # at all (``command prove``, ``env prove``, ``timeout 5
        # prove``) — the raw head is the wrapper, not the name.
        return False
    if segment.raw_tokens and segment.raw_tokens[0] in (
        "if",
        "elif",
        "while",
        "until",
    ):
        return False
    if _segment_exempt_negated(segment):
        return False
    return _effective_tail(segments, index) not in ("&&", "||", "&")


def _test_is_enforced(
    segments: list[_Segment], index: int, segment: _Segment
) -> bool:
    """True when a file test's failure actually stops the hook.

    A test that is performed and discarded — ``|| true``, a
    condition nobody acts on — proves nothing. The enforcing
    shapes: a condition whose failing branch exits (negated
    test with an exit before the else, plain test with an exit
    in the else), ``test || exit`` (including an ``|| exit``
    later in the same ``&&``/``||`` chain, where the chain's
    failure still reaches it), and a standalone test while
    ``set -e`` is in effect at that point in the same scope.
    An exit inside a subshell stops only the subshell, never
    the hook, so enforcing exits must sit at the top level.

    ``set -e`` has exemptions that matter here, all verified
    against real shells: it does not act on a command that is
    a non-final member of an ``&&``/``||`` list, on a command
    running in the background (``&``), or on a command whose
    result is inverted with ``!``. A test in any of those
    positions is not enforced by errexit, however it is
    spelled — including a pipeline's last component (whose
    status errexit would otherwise see) followed by ``&&``.
    """
    first = segment.raw_tokens[0] if segment.raw_tokens else ""
    if first in ("if", "while", "until"):
        # Negation is read from the RAW tokens: the segmenter
        # strips a leading ``!`` from the effective tokens, so
        # looking for it there misreads ``if ! test …`` — the
        # branch that holds the exit is the opposite one.
        negated = _segment_negated(segment)
        depth = 0
        seen_else = False
        for later_index, later in enumerate(
            segments[index + 1 :], start=index + 1
        ):
            later_first = later.raw_tokens[0] if later.raw_tokens else ""
            if later_first in ("if", "while", "until"):
                depth += 1
                continue
            if later_first in ("fi", "done"):
                if depth == 0:
                    return False
                depth -= 1
                continue
            if depth == 0 and later_first == "else":
                seen_else = True
                # No continue: ``else exit 1`` written on one
                # line is both the else and the exit.
            if (
                depth == 0
                and not later.dead
                and later.subshell == 0
                and not later.in_pipeline
                and later.tokens
                and _basename(later.tokens[0]) == "exit"
                and _effective_tail(segments, later_index)
                not in ("|", "&")
                and seen_else != negated
            ):
                return True
        return False
    if _statement_negated(segments, index):
        # A negated test turns "file missing" into success,
        # wherever the ``!`` sits — on the test, on its
        # pipeline's head, or on the group whose body it opens:
        # no standalone shape below can enforce it (the
        # if-condition shape above is the only place negation
        # is accounted for, by which branch holds the exit).
        return False
    # The ``|| exit`` shape: an exit chained to this test by
    # ``||`` anywhere later in the same ``&&``/``||`` chain —
    # ``test || exit``, and equally ``test && echo || exit``,
    # where the test's failure skips the middle and still
    # reaches the exit. The test itself may sit in a subshell —
    # its status still reaches the chain. An exit that only
    # leaves a subshell enforces nothing: inside a pipeline,
    # or in a group whose own tail is a pipe or a background
    # ``&`` (``test || { exit 1; } | cat``).
    for later_index, later in enumerate(
        segments[index + 1 :], start=index + 1
    ):
        if later.kind == "empty":
            continue
        if later.sep_before not in ("&&", "||"):
            break
        if (
            later.sep_before == "||"
            and later.tokens
            and _basename(later.tokens[0]) == "exit"
            and not later.dead
            and later.subshell == 0
            and not later.in_pipeline
            and later.sep_after != "&"
            and _effective_tail(segments, later_index)
            not in ("|", "&")
        ):
            return True
    if _effective_tail(segments, index) in ("|", "&&", "||", "&"):
        # The test's statement is a pipeline (its status is not
        # the pipeline's unless it is the last component — and
        # the pipeline's own tail is what governs then), a
        # non-final member of an ``&&``/``||`` list, or a
        # background job: in every case ``set -e`` exempts it,
        # so its failure is invisible to the hook. The tail is
        # the EFFECTIVE one — ``( test … ) && echo`` and
        # ``{ test …; } &`` carry the operator on the group's
        # close, not beside the test.
        return False
    if _enclosing_group_tail(segments, index) in (
        "|",
        "&&",
        "||",
        "&",
    ):
        # The test is not the last command in its group, so
        # its own tail says nothing — but the GROUP's position
        # still governs: a group in an exempt position (piped,
        # backgrounded, a non-final chain member) has set -e
        # switched off for everything inside it.
        return False
    opener = _command_substitution_opener(segments, index)
    if opener is not None and opener.kind != "assignment":
        # Inside ``$( … )`` used as an argument, the test's
        # failure is data to the outer command, not a status
        # the hook's ``set -e`` ever sees. The one exception is
        # a plain assignment — ``X=$(test …)`` — whose own
        # status IS the substitution's, so errexit sees the
        # test's result through the assignment.
        return False
    if segment.sep_before not in (";", None, "|"):
        # (A test entering as a pipeline's last component is
        # fine: its status becomes the pipeline's status.)
        return False
    # Standalone test: enforced only while errexit is in effect
    # at this point — set -e switched on earlier in the same
    # scope (or in a function actually called earlier) and not
    # switched back off with set +e since. Errexit is inherited
    # into subshells, so a set at a shallower level counts.
    # For a test inside a function body, "this point" is the
    # CALL, not the definition: the state that matters is the
    # one in effect where a counting call runs, with any set
    # inside the body before the test applied on top of it.
    if segment.func is not None:
        for call_index, call in enumerate(segments):
            if (
                call.func is not None
                or call.dead
                or _is_definition_segment(call)
                or not call.tokens
                or call.tokens[0] != segment.func
                or _raw_call_name(call) not in (segment.func, "eval")
                or not _call_counts(segments, call_index, call)
            ):
                continue
            state = _errexit_in_effect(segments, call_index, call)
            for inner in segments[:index]:
                if (
                    inner.func != segment.func
                    or inner.dead
                    or inner.unproven
                    or not inner.tokens
                ):
                    continue
                if _basename(inner.tokens[0]) == "set":
                    state = _apply_set_words(state, inner.tokens)
            if state:
                return True
        return False
    return _errexit_in_effect(segments, index, segment)


def _apply_set_words(errexit: bool, tokens: list) -> bool:
    """The errexit state after a ``set`` command's words."""
    words = tokens[1:]
    for position, token in enumerate(words):
        if re.fullmatch(r"-[a-zA-Z]*e[a-zA-Z]*", token):
            errexit = True
        elif re.fullmatch(r"\+[a-zA-Z]*e[a-zA-Z]*", token):
            errexit = False
        elif token in ("-o", "+o") and words[position + 1 :][:1] == [
            "errexit"
        ]:
            errexit = token == "-o"
    return errexit


def _errexit_in_effect(
    segments: list[_Segment], index: int, segment: _Segment
) -> bool:
    """Whether ``set -e`` is in effect at a vantage segment:
    switched on earlier in the same scope (or in a function
    actually called earlier) and not switched back off with
    ``set +e`` since."""
    called_earlier = {
        name
        for j, other in enumerate(segments[:index])
        if other.func is None
        and not other.dead
        and not other.unproven
        and other.subshell == 0
        and not other.in_pipeline
        and not _is_definition_segment(other)
        and _effective_tail(segments, j) not in ("|", "&")
        and (name := _raw_call_name(other)) is not None
    }
    errexit = False
    for j, other in enumerate(segments[:index]):
        if (
            other.depth != segment.depth
            or other.subshell > segment.subshell
            or other.dead
            or other.unproven
            or (other.func is not None and other.func not in called_earlier)
            or not other.tokens
            or _basename(other.tokens[0]) != "set"
            # A set inside a piped or backgrounded group runs
            # in a child shell and dies with it — it arms
            # nothing here (``{ set -e; } | cat``).
            or _effective_tail(segments, j) in ("|", "&")
        ):
            continue
        errexit = _apply_set_words(errexit, other.tokens)
    return errexit


def _first_proven_index(
    segments: list[_Segment], path: str
) -> int | None:
    """The index of the first segment that proves a staged
    file is in the image (a live command existence-tests the
    exact path, and the test's failure stops the hook), or
    None."""
    for index, segment in enumerate(segments):
        if (
            segment.kind == "command"
            and not segment.dead
            and not segment.unproven
            and _segment_tests_path(segment, path)
            and _test_is_enforced(segments, index, segment)
        ):
            return index
    return None


def _hook_file_proven(segments: list[_Segment], path: str) -> bool:
    """True when the hook proves a staged file is in the image:
    a live command existence-tests the exact path, and the
    test's failure stops the hook."""
    return _first_proven_index(segments, path) is not None


# ------------------------------------------------------------------
# The graphical-first launcher (Phase 4)
#
# beerus-live tries the graphical session first and falls back
# to the text form when it cannot start. That design has four
# load-bearing facts, and they are checked the way the shell
# runs them — from the analysed command segments, in program
# order — never as substrings a comment or an echo could
# satisfy:
#
# 1. a startx run whose client is /usr/local/bin/beerus-ui,
#    run in the foreground — a backgrounded or pipelined
#    startx reports its launch result, never the session's
#    own exit code;
# 2. the run's exit status captured ($? into a variable) by
#    the very next statement after the startx run, and that
#    variable assigned nowhere else in the script — without
#    the capture there is nothing to honor, a ``||`` straight
#    after startx swallows the status outright, and a second
#    assignment would replace the session's outcome with a
#    made-up one before any test reads it;
# 3. both terminal codes honored, each credited only when
#    every status outcome that can reach the matching exit
#    is that code — judged on path conditions (the conjunction
#    of the branch conditions enclosing the exit, in the
#    set model of ``_launcher_walk``), so an exit parked in an
#    else branch, a dead inner branch, or behind a test of
#    something else honors nothing;
# 4. the text form's exec present AFTER the graphical attempt
#    and still the final command, so the fallback is the
#    designed end of the script — its exit code is the
#    service's exit code;
# 5. the two designed notice lines printed, in order, before
#    that exec — the operator is told the graphical session
#    could not start and what runs instead.
# ------------------------------------------------------------------

_STATUS_CAPTURE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=\$\?$")

_GUI_CLIENT = "/usr/local/bin/beerus-ui"

# The two notice lines beerus-live prints before it falls back
# to the text session — exact wording, in this order. They are
# a designed honesty element: the operator is told, in plain
# words, that the graphical session could not start and what
# runs instead. A missing line, or one rewritten to claim the
# graphical session, leaves the fallback silent or lying.
_NOTICE_LINES = (
    "The graphical session could not start on this machine.",
    "Beerus is continuing in its text console — the same "
    "confirmation steps, the same engine, the same job documents.",
)


def _launcher_grammar_problems(text: str, name: str) -> list[str]:
    """Problems with a start script's grammar.

    ``beerus-live`` and ``beerus-ui`` are short, fixed-form,
    Sentinel-owned scripts, and the checks below model them
    exactly — so any construct the model cannot prove is
    refused outright, never silently skipped: function
    definitions, subshells, command substitution (``$(...)``
    or backquotes, anywhere, assignments included — a quoted
    value hides one from the segmenter), pipelines,
    backgrounding, and ``case`` statements. ``&&`` chains are
    modelled and stay allowed; redirections (``2>&1`` and
    friends) are not backgrounding.
    """
    found: list[str] = []

    def note(label: str) -> None:
        if label not in found:
            found.append(label)

    code = []
    for raw in _drop_heredoc_bodies(text.splitlines()):
        line = raw.strip()
        if line and not line.startswith("#"):
            code.append(_strip_shell_comment(line))
    for line in _logical_lines(code):
        if re.search(
            r"(?:^|[;|&]\s*)(?:function\s+)?"
            r"[A-Za-z_][A-Za-z0-9_]*\s*\(\s*\)",
            line,
        ) or re.search(r"(?:^|[;|&]\s*)function\s+[A-Za-z_]", line):
            note("a function definition")
        if re.search(r"(?:^|[;|&]\s*)case\s", line):
            note("a case statement")
        in_single = False
        in_double = False
        escaped = False
        index = 0
        while index < len(line):
            ch = line[index]
            if escaped:
                escaped = False
            elif ch == "\\" and not in_single:
                escaped = True
            elif ch == "'" and not in_double:
                in_single = not in_single
            elif ch == '"' and not in_single:
                in_double = not in_double
            elif not in_single:
                if ch == "`" or (
                    ch == "$" and line[index + 1 : index + 2] == "("
                ):
                    note("command substitution")
                elif not in_double:
                    if ch in "()":
                        note("a subshell (a parenthesised command group)")
                    elif ch == "|":
                        note("a pipeline")
                    elif ch == "&":
                        following = line[index + 1 : index + 2]
                        previous = line[index - 1] if index else ""
                        if following == "&":
                            index += 1  # a && chain is modelled
                        elif (
                            previous != ">"
                            and following not in (">", "-")
                            and not following.isdigit()
                        ):
                            note("backgrounding (a bare &)")
            index += 1
    return [
        f"{name} uses {label}, which is outside the strict form "
        "the definition check can prove for the start scripts — "
        "a construct the check cannot model exactly is refused, "
        "never silently skipped."
        for label in found
    ]


# The path-condition model for the captured status. A statement
# is credited only from the set of status outcomes that can
# actually reach it: the conjunction of the branch conditions
# enclosing it, narrowed by the guards already passed on the
# way. Outcomes are tracked over the integers plus one extra
# outcome — the variable unset (startx missing entirely) — as
# a set that is either finite (these codes) or cofinite (every
# code except these).


class _StatusSet(NamedTuple):
    finite: bool  # True: vals are included; False: vals are excluded
    vals: frozenset
    absent: bool  # the variable-unset outcome is in the set


_SS_EMPTY = _StatusSet(True, frozenset(), False)
_SS_ABSENT = _StatusSet(True, frozenset(), True)
_SS_ALL_CODES = _StatusSet(False, frozenset(), False)
_SS_UNIVERSE = _StatusSet(False, frozenset(), True)


def _ss_singleton(code: int) -> _StatusSet:
    return _StatusSet(True, frozenset({code}), False)


def _ss_intersect(a: _StatusSet, b: _StatusSet) -> _StatusSet:
    if a.finite and b.finite:
        return _StatusSet(True, a.vals & b.vals, a.absent and b.absent)
    if a.finite:
        return _StatusSet(True, a.vals - b.vals, a.absent and b.absent)
    if b.finite:
        return _StatusSet(True, b.vals - a.vals, a.absent and b.absent)
    return _StatusSet(False, a.vals | b.vals, a.absent and b.absent)


def _ss_union(a: _StatusSet, b: _StatusSet) -> _StatusSet:
    absent = a.absent or b.absent
    if a.finite and b.finite:
        return _StatusSet(True, a.vals | b.vals, absent)
    if a.finite:
        return _StatusSet(False, b.vals - a.vals, absent)
    if b.finite:
        return _StatusSet(False, a.vals - b.vals, absent)
    return _StatusSet(False, a.vals & b.vals, absent)


def _ss_complement(a: _StatusSet) -> _StatusSet:
    return _StatusSet(not a.finite, a.vals, not a.absent)


def _ss_is_empty(a: _StatusSet) -> bool:
    return a.finite and not a.vals and not a.absent


def _is_fallback_set(a: _StatusSet) -> bool:
    """True when *a* is exactly "every outcome except the two
    honored codes" — the full set of outcomes that must reach
    the text form (the variable-unset outcome may or may not
    be among them, depending on how the attempt was guarded)."""
    return not a.finite and a.vals == frozenset({0, 2})


def _test_mentions_variable(tokens: list[str], variable: str) -> bool:
    pattern = re.compile(
        r"\$(?:\{"
        + re.escape(variable)
        + r"\}|"
        + re.escape(variable)
        + r"(?![A-Za-z0-9_]))"
    )
    return any(pattern.search(token) for token in tokens)


def _status_atom_then(atom: list[str], variable: str):
    """The then-set of one ``[ ... ]`` atom that tests only the
    captured variable in a modelled comparison (``-eq``, ``-ne``,
    ``=``, ``==``, ``!=`` against a literal, or the variable's
    mere presence), or None when the atom is anything else."""
    negated = False
    while atom and atom[0] == "!":
        negated = not negated
        atom = atom[1:]
    refs = (f"${variable}", f"${{{variable}}}")
    result = None
    if len(atom) == 1 and atom[0] in refs:
        result = _SS_ALL_CODES
    elif len(atom) == 2 and atom[1] in refs and atom[0] in ("-n", "-z"):
        result = _SS_ALL_CODES if atom[0] == "-n" else _SS_ABSENT
    elif len(atom) == 3:
        left, op, right = atom
        literal = None
        if left in refs:
            literal = right
        elif right in refs:
            literal = left
        if literal is not None and op in ("-eq", "-ne", "=", "==", "!="):
            try:
                number = int(literal)
            except ValueError:
                number = None
            if op in ("-eq", "-ne"):
                if number is None:
                    return None
                result = (
                    _ss_singleton(number)
                    if op == "-eq"
                    else _StatusSet(False, frozenset({number}), False)
                )
            elif number is not None:
                result = (
                    _ss_singleton(number)
                    if op in ("=", "==")
                    else _StatusSet(False, frozenset({number}), True)
                )
            else:
                result = _SS_EMPTY if op in ("=", "==") else _SS_UNIVERSE
    if result is None:
        return None
    return _ss_complement(result) if negated else result


def _status_test_then(tokens: list[str], variable: str):
    """The then-set of a ``[ ... ]``/``test`` condition whose
    every atom tests only the captured variable (atoms combined
    with ``-a``/``-o`` included), or None when it is not such a
    test. The else-set is the complement of the then-set."""
    if not tokens:
        return None
    head = _basename(tokens[0])
    if head in ("[", "[["):
        inner = list(tokens[1:])
        if inner and inner[-1] in ("]", "]]"):
            inner.pop()
    elif head == "test":
        inner = list(tokens[1:])
    else:
        return None
    if not inner:
        return None
    negated = False
    while inner and inner[0] == "!":
        negated = not negated
        inner = inner[1:]
    or_groups: list[list[str]] = [[]]
    for token in inner:
        if token == "-o":
            or_groups.append([])
        else:
            or_groups[-1].append(token)
    then = _SS_EMPTY
    for group in or_groups:
        atoms: list[list[str]] = [[]]
        for token in group:
            if token == "-a":
                atoms.append([])
            else:
                atoms[-1].append(token)
        group_then = _SS_UNIVERSE
        for atom in atoms:
            atom_then = _status_atom_then(atom, variable)
            if atom_then is None:
                return None
            group_then = _ss_intersect(group_then, atom_then)
        then = _ss_union(then, group_then)
    return _ss_complement(then) if negated else then


def _condition_model(tokens: list[str], variable: str):
    """Classify one condition command against the captured
    variable: ("status", then-set) for a pure modelled test of
    it, ("unknown", None) when it touches the variable in any
    other way, ("other", None) when it does not touch the
    variable at all."""
    then = _status_test_then(tokens, variable)
    if then is not None:
        return "status", then
    if _test_mentions_variable(tokens, variable):
        return "unknown", None
    return "other", None


def _fold_condition(parts: list, variable: str):
    """Fold the parts of a (possibly ``&&``/``||``-chained)
    condition: parts are (tokens, join-in-front) pairs. Any
    unmodelled or mixed part makes the whole condition
    "unknown" — the conservative verdict."""
    kind, then = _condition_model(parts[0][0], variable)
    for tokens, join in parts[1:]:
        kind2, then2 = _condition_model(tokens, variable)
        if kind == "unknown" or kind2 == "unknown":
            kind, then = "unknown", None
        elif kind == "status" and kind2 == "status":
            then = (
                _ss_intersect(then, then2)
                if join == "&&"
                else _ss_union(then, then2)
            )
        elif kind == "other" and kind2 == "other":
            kind = "other"
        else:
            kind, then = "unknown", None
    return kind, then


def _is_fallback_exec(segment: _Segment) -> bool:
    """True for the text form's exec: exec python3 -m beerus."""
    return (
        segment.kind == "command"
        and len(segment.tokens) >= 4
        and _basename(segment.tokens[0]) == "exec"
        and _basename(segment.tokens[1]) == "python3"
        and segment.tokens[2] == "-m"
        and segment.tokens[3] == "beerus"
    )


def _launcher_walk(segs: list[_Segment], variable: str, capture_pos: int):
    """Walk beerus-live's top-level segments tracking, for every
    statement, the set of captured-status outcomes that can
    reach it — the path condition.

    Branch conditions narrow the set (a then-branch intersects
    with the condition's then-set, an else-branch with its
    complement, an ``elif`` with the complement of every earlier
    condition plus its own), a guard whose statement terminates
    removes its outcomes from what flows past it, and a
    ``&&``-chained test contributes its then-set to the chained
    statement. A condition that does not test the variable
    narrows nothing but marks the region conditional (the
    fallback and its notices may not live in one); a condition
    that touches the variable in an unmodelled way, and loop /
    case bodies, are opaque — nothing inside them is credited
    at all. Dead segments never credit anything.

    Returns {"exits": [(code, sset, eligible, narrowed)],
    "fallbacks": [(pos, sset, eligible, narrowed)],
    "notices": [(line index, pos, sset, eligible, narrowed)]},
    where eligible is False for anything the segment data marks
    dead/unproven-outside-a-chain, subshell-bound, inside a
    function body, or behind an opaque or chain-poisoned path.
    """
    exits: list = []
    fallbacks: list = []
    notices: list = []

    def new_frame(kind, current, narrowed=False, opaque=False):
        return {
            "kind": kind,
            "current": current,
            "entry": current,
            "narrowed": narrowed,
            "opaque": opaque,
            "conds": [],
            "known": True,
            "branches": [],
            "has_else": False,
        }

    stack: list[dict] = [new_frame("root", _SS_ABSENT)]
    chain: dict | None = None

    def flags():
        return (
            any(frame["narrowed"] for frame in stack),
            any(frame["opaque"] for frame in stack),
        )

    def seg_eligible(segment: _Segment) -> bool:
        return (
            not segment.dead
            and segment.subshell == 0
            and segment.func is None
            and (
                not segment.unproven
                or segment.sep_before in ("&&", "||")
                or segment.sep_after in ("&&", "||")
            )
        )

    def flush_chain() -> None:
        nonlocal chain
        if chain is None:
            return
        parts = [element["fail"] for element in chain["elements"][:-1]]
        if chain["elements"] and not chain["elements"][-1]["terminating"]:
            parts.append(chain["elements"][-1]["context"])
        total = _SS_EMPTY
        for part in parts:
            total = _ss_union(total, part)
        stack[-1]["current"] = total
        chain = None

    def covered(frame: dict) -> _StatusSet:
        total = _SS_EMPTY
        for cond in frame["conds"]:
            total = _ss_union(total, cond)
        return total

    def process_simple(pos: int, segment: _Segment) -> None:
        nonlocal chain
        if chain is not None and segment.sep_before == chain["op"]:
            context = chain["next"]
            poisoned = chain["poisoned"]
        else:
            flush_chain()
            context = stack[-1]["current"]
            poisoned = False
        narrowed, opaque = flags()
        eligible = seg_eligible(segment) and not opaque and not poisoned
        tokens = segment.tokens
        terminating = False
        if pos == capture_pos:
            context = _SS_ALL_CODES
            stack[-1]["current"] = _SS_ALL_CODES
        if tokens and _basename(tokens[0]) == "exit":
            code = tokens[1] if len(tokens) > 1 else None
            exits.append((code, context, eligible, narrowed))
            terminating = True
        elif _is_fallback_exec(segment):
            fallbacks.append((pos, context, eligible, narrowed))
            terminating = True
        elif tokens and _basename(tokens[0]) == "exec":
            terminating = True
        if tokens and _basename(tokens[0]) in ("echo", "printf"):
            line = " ".join(tokens[1:])
            if line in _NOTICE_LINES:
                notices.append(
                    (
                        _NOTICE_LINES.index(line),
                        pos,
                        context,
                        eligible,
                        narrowed,
                    )
                )
        # This element's contribution to what flows past the
        # chain it sits in, and the context its chain successor
        # inherits.
        then = None
        poison_next = False
        if (
            segment.kind == "command"
            and tokens
            and _basename(tokens[0]) in ("[", "[[", "test")
        ):
            kind, then = _condition_model(tokens, variable)
            if kind != "status":
                then = None
                if kind == "unknown":
                    poison_next = True
        join = segment.sep_after or segment.sep_before
        if then is not None and join == "||":
            fail = _ss_intersect(context, then)
            following = _ss_intersect(context, _ss_complement(then))
        elif then is not None:
            fail = _ss_intersect(context, _ss_complement(then))
            following = _ss_intersect(context, then)
        else:
            fail = _SS_EMPTY if terminating else context
            following = context
        if pos == capture_pos:
            following = _SS_ALL_CODES
        element = {
            "context": context,
            "fail": fail,
            "terminating": terminating,
        }
        if chain is not None and segment.sep_before == chain["op"]:
            chain["elements"].append(element)
            chain["next"] = following
            chain["poisoned"] = poisoned or poison_next
            if segment.sep_after != chain["op"]:
                flush_chain()
        elif segment.sep_after in ("&&", "||"):
            chain = {
                "op": segment.sep_after,
                "elements": [element],
                "next": following,
                "poisoned": poison_next,
            }
        elif terminating:
            stack[-1]["current"] = _SS_EMPTY

    def gather_condition(pos: int, segment: _Segment):
        """The (tokens, join) parts of an if/elif condition,
        including any && / ||-chained segments that follow it;
        returns (parts, last consumed position)."""
        parts = [(segment.tokens, None)]
        probe = pos + 1
        while probe < len(segs) and segs[probe].sep_before in ("&&", "||"):
            parts.append((segs[probe].tokens, segs[probe].sep_before))
            probe += 1
        return parts, probe - 1

    pos = 0
    while pos < len(segs):
        segment = segs[pos]
        prefix = []
        for token in segment.raw_tokens:
            if token in (
                "if",
                "elif",
                "else",
                "then",
                "fi",
                "do",
                "done",
                "while",
                "until",
                "for",
                "case",
                "esac",
                "{",
                "}",
            ):
                prefix.append(token)
            else:
                break
        consumed = False
        for keyword in prefix:
            if keyword == "if":
                flush_chain()
                parts, pos = gather_condition(pos, segment)
                kind, then = _fold_condition(parts, variable)
                entry = stack[-1]["current"]
                frame = new_frame(
                    "if",
                    entry,
                    narrowed=(kind == "other"),
                    opaque=(kind == "unknown"),
                )
                frame["conds"] = [then]
                frame["known"] = kind == "status"
                if kind == "status":
                    frame["current"] = _ss_intersect(entry, then)
                stack.append(frame)
                consumed = True
            elif keyword == "elif":
                flush_chain()
                if stack[-1]["kind"] == "if":
                    frame = stack[-1]
                    frame["branches"].append(frame["current"])
                    parts, pos = gather_condition(pos, segment)
                    kind, then = _fold_condition(parts, variable)
                    remaining = frame["entry"]
                    if frame["known"]:
                        remaining = _ss_intersect(
                            frame["entry"],
                            _ss_complement(covered(frame)),
                        )
                    frame["conds"].append(then)
                    if kind == "status":
                        frame["current"] = _ss_intersect(remaining, then)
                    else:
                        frame["known"] = False
                        frame["current"] = remaining
                        if kind == "other":
                            frame["narrowed"] = True
                        else:
                            frame["opaque"] = True
                consumed = True
            elif keyword == "else":
                flush_chain()
                if stack[-1]["kind"] == "if":
                    frame = stack[-1]
                    frame["branches"].append(frame["current"])
                    frame["has_else"] = True
                    if frame["known"]:
                        frame["current"] = _ss_intersect(
                            frame["entry"],
                            _ss_complement(covered(frame)),
                        )
                    else:
                        frame["current"] = frame["entry"]
            elif keyword == "fi":
                flush_chain()
                if stack[-1]["kind"] == "if":
                    frame = stack.pop()
                    frame["branches"].append(frame["current"])
                    total = _SS_EMPTY
                    for branch in frame["branches"]:
                        total = _ss_union(total, branch)
                    if not frame["has_else"]:
                        if frame["known"]:
                            total = _ss_union(
                                total,
                                _ss_intersect(
                                    frame["entry"],
                                    _ss_complement(covered(frame)),
                                ),
                            )
                        else:
                            total = _ss_union(total, frame["entry"])
                    stack[-1]["current"] = total
                consumed = True
            elif keyword in ("while", "until", "for"):
                flush_chain()
                stack.append(
                    new_frame(
                        "loop",
                        stack[-1]["current"],
                        narrowed=True,
                        opaque=True,
                    )
                )
                consumed = True
            elif keyword == "done":
                flush_chain()
                if stack[-1]["kind"] == "loop":
                    frame = stack.pop()
                    stack[-1]["current"] = frame["entry"]
                consumed = True
            elif keyword == "case":
                flush_chain()
                stack.append(
                    new_frame(
                        "case",
                        stack[-1]["current"],
                        narrowed=True,
                        opaque=True,
                    )
                )
                consumed = True
            elif keyword == "esac":
                flush_chain()
                if stack[-1]["kind"] == "case":
                    frame = stack.pop()
                    stack[-1]["current"] = frame["entry"]
                consumed = True
            # "then", "do", "{", "}" change nothing themselves;
            # a statement they carry is processed below.
        if not consumed and not segment.dead:
            if segment.tokens or segment.assignments:
                process_simple(pos, segment)
        pos += 1
    flush_chain()
    return {"exits": exits, "fallbacks": fallbacks, "notices": notices}


def _graphical_first_problems(segments: list[_Segment]) -> list[str]:
    """Problems with beerus-live's graphical-first design.

    Judged on path conditions (see ``_launcher_walk``): the
    status capture must be the very next statement after the
    startx run and the only assignment to its variable in the
    script; each honored exit is credited only when every
    outcome that can reach it is that exit's code; the text
    form's exec and the two notice lines are credited only on
    the one path every other outcome takes — every code except
    0 and 2, with the variable-unset outcome (no startx at all)
    flowing there too — never inside a narrower branch.
    """
    problems: list[str] = []
    segs = [segment for segment in segments if segment.depth == 0]
    startx_pos = None
    startx_segment = None
    for pos, segment in enumerate(segs):
        if segment.dead or segment.unproven:
            continue
        if (
            segment.kind == "command"
            and segment.tokens
            and _basename(segment.tokens[0]) == "startx"
            and segment.func is None
            and segment.subshell == 0
        ):
            startx_pos = pos
            startx_segment = segment
            break
    if startx_pos is None or startx_segment is None:
        problems.append(
            "beerus-live never tries the graphical session: no "
            "startx run of /usr/local/bin/beerus-ui stands before "
            "the text fallback."
        )
        return problems
    client = next(
        (
            token
            for token in startx_segment.tokens[1:]
            if not token.startswith("-")
        ),
        None,
    )
    if client != _GUI_CLIENT:
        problems.append(
            "beerus-live's startx run does not start "
            "/usr/local/bin/beerus-ui as its client."
        )
    if startx_segment.sep_after == "||":
        problems.append(
            "beerus-live swallows the graphical session's exit "
            "status (|| after startx) — a designed refusal from "
            "the graphical session would be lost, and the text "
            "fallback would start as if nothing had been decided."
        )
    if (
        startx_segment.sep_after in ("&", "|")
        or startx_segment.stmt_bg
        or startx_segment.in_pipeline
        or _effective_tail(segs, startx_pos) in ("&", "|")
    ):
        problems.append(
            "beerus-live runs startx in the background or in a "
            "pipeline (& or | after startx) — the status captured "
            "after it would be the launch result, not the "
            "graphical session's own exit code, so a graphical "
            "failure could be reported as success and no fallback "
            "would run."
        )
    # Capture integrity: the capture is the statement
    # immediately after the startx run, its value is exactly
    # $?, and its variable is assigned nowhere else — a second
    # assignment (a constant, say) would replace the session's
    # real outcome with a made-up one before any test reads it.
    variable = None
    capture_pos = None
    for pos in range(startx_pos + 1, len(segs)):
        segment = segs[pos]
        if segment.dead:
            continue
        for assignment in segment.assignments:
            match = _STATUS_CAPTURE.match(assignment)
            if match:
                variable = match.group(1)
                capture_pos = pos
                break
        if variable is not None:
            break
    raw_fallbacks = [
        pos
        for pos, segment in enumerate(segs)
        if not segment.dead
        and _is_fallback_exec(segment)
        and pos > startx_pos
    ]
    if variable is None or capture_pos is None:
        problems.append(
            "beerus-live does not capture the graphical session's "
            "exit status (a variable set from $? after startx), "
            "so no outcome of the graphical session can be "
            "honored — every result would fall through to the "
            "text fallback."
        )
        if not raw_fallbacks:
            problems.append(
                "beerus-live has no text fallback after the graphical "
                "attempt (exec python3 -m beerus) — a machine whose "
                "graphical session cannot start would get nothing."
            )
        return problems
    capture_segment = segs[capture_pos]
    if (
        capture_pos != startx_pos + 1
        or capture_segment.kind != "assignment"
        or capture_segment.assignments != [f"{variable}=$?"]
    ):
        problems.append(
            "beerus-live does not capture the graphical session's "
            "exit status immediately after startx: the assignment "
            "from $? must be the very next statement after the "
            "startx run, with nothing in between and nothing "
            "alongside it — anything else leaves open whether the "
            "value it holds is the session's own outcome."
        )
    assignments_to_variable = 0
    for segment in segs:
        for assignment in segment.assignments:
            if assignment.split("=", 1)[0] == variable:
                assignments_to_variable += 1
        if segment.tokens and _basename(segment.tokens[0]) in (
            "export",
            "readonly",
            "local",
            "declare",
            "typeset",
        ):
            for token in segment.tokens[1:]:
                if "=" in token and token.split("=", 1)[0] == variable:
                    assignments_to_variable += 1
    if assignments_to_variable != 1:
        problems.append(
            "beerus-live assigns its captured status variable "
            "more than once — it must be assigned exactly once, "
            "from $? immediately after startx; a second "
            "assignment would replace the graphical session's "
            "real outcome with a made-up one before the tests "
            "read it."
        )
    walk = _launcher_walk(segs, variable, capture_pos)
    for code in ("0", "2"):
        want = _ss_singleton(int(code))
        if not any(
            recorded == code and eligible and sset == want
            for recorded, sset, eligible, _narrowed in walk["exits"]
        ):
            problems.append(
                f"beerus-live does not exit {code} when the "
                f"graphical session exits {code}: that outcome "
                "must be bound to the captured status, not "
                "left to fall through."
            )
    if not raw_fallbacks:
        problems.append(
            "beerus-live has no text fallback after the graphical "
            "attempt (exec python3 -m beerus) — a machine whose "
            "graphical session cannot start would get nothing."
        )
        return problems
    credited = [
        pos
        for pos, sset, eligible, narrowed in walk["fallbacks"]
        if eligible and not narrowed and _is_fallback_set(sset)
    ]
    anchor = credited[0] if credited else raw_fallbacks[-1]
    if not credited:
        problems.append(
            "beerus-live does not reach the exec that starts "
            "its text form on exactly the graphical outcomes "
            "that need it: the exec must stand after both "
            "honored exits, on the path every other outcome "
            "takes — an exec inside a narrower branch, or one "
            "a clean finish or a designed refusal can reach, "
            "proves nothing."
        )
    if any(
        segment.kind in ("command", "noop", "assignment")
        for segment in segs[anchor + 1 :]
    ):
        problems.append(
            "beerus-live runs further statements after its "
            "text fallback exec — the exec must remain the "
            "final command, so its exit code is the service's "
            "exit code."
        )
    notices_ok = False
    seen_first = False
    for line_index, pos, sset, eligible, narrowed in walk["notices"]:
        if pos >= anchor:
            continue
        if not (eligible and not narrowed and _is_fallback_set(sset)):
            continue
        if line_index == 0:
            seen_first = True
        elif line_index == 1 and seen_first:
            notices_ok = True
    if not notices_ok:
        problems.append(
            "beerus-live does not print its two designed "
            "notice lines, in order, before the text fallback "
            "exec — the operator would not be told plainly "
            "that the graphical session could not start and "
            "what runs instead of it."
        )
    return problems


def _eval_invoked_name(segment: _Segment) -> str | None:
    """For a plain-call eval segment, the name it invokes.

    The walk mirrors ``_invokes_eval``: structural
    keywords, wrappers (``command``, ``builtin`` …), and
    prefix assignments are stepped over; when the first
    substantive word is ``eval``, the invoked name is
    its argument as written, quotes stripped. Nested
    evals unwrap (round 32): ``eval eval sneak`` invokes
    sneak — each eval word whose argument is itself
    ``eval`` is stepped through to the next word.
    Anything else is not an eval call.
    """
    raw = segment.raw_tokens
    index = 0
    argument = None
    argument_index = None
    while index < len(raw):
        token = raw[index]
        base = _basename(token)
        if (
            token in _KEYWORDS
            or base in _WRAPPER_COMMANDS
            or base == "builtin"
            or _ASSIGNMENT.match(token)
        ):
            if base == "eval":
                if index + 1 < len(raw):
                    argument = raw[index + 1].strip("'\"")
                    argument_index = index + 1
                break
            index += 1
            continue
        return None
    while argument == "eval" and argument_index is not None:
        if argument_index + 1 < len(raw):
            argument = raw[argument_index + 1].strip("'\"")
            argument_index += 1
        else:
            return None
    return argument


def _call_head_name(segment: _Segment) -> str | None:
    """The function name a segment invokes, for EXECUTION
    purposes (round 26). Unlike the errexit-oriented call
    recogniser, this counts the calls the shell really
    runs: a call nested inside another helper's body, or
    used as a condition (``if sneak; then``), a negation
    (``! sneak``), or a loop guard — all of which execute
    the body in the current shell. Wrapper-prefixed heads
    (``command sneak``) never invoke the function and are
    excluded, as are definitions.
    """
    tokens = list(segment.raw_tokens)
    while tokens and (
        tokens[0]
        in (
            "if",
            "elif",
            "while",
            "until",
            "!",
            "{",
            "then",
            "else",
            "do",
        )
        or _ASSIGNMENT.match(tokens[0])
    ):
        tokens.pop(0)
    if not tokens:
        return None
    head = _basename(tokens[0])
    if head in _WRAPPER_COMMANDS or tokens[0] in _KEYWORDS:
        return None
    if len(tokens) == 1 and segment.sep_after == "(":
        return None  # a definition, not a call
    return tokens[0]


def _execution_anchors(
    segments: list[_Segment], blocked: frozenset = frozenset()
):
    """Build the call graph and return ``anchors(name)``:
    the top-level indexes at which a call chain reaching
    the function ``name`` executes.

    The graph is walked transitively (round 26): a body
    two helpers deep executes at the top-level call of
    the outermost helper. Every syntactic call is an
    edge — chained, pipelined, backgrounded, and subshell
    calls all execute the body, and their filesystem side
    effects persist even where their definitions do not
    (round 26, shell-proved) — and only the top-level
    end of a chain must be live for the point to count.
    A call in a provably-dead branch may count anyway,
    which errs toward refusal (the safe direction).

    Edges come from execution, not from the errexit-
    oriented call recogniser: condition and negation
    calls count (``if sneak; then``, ``! sneak``), and a
    plain-call eval IS a call — it stays one behind a
    wrapper (``command eval sneak``, round 30) and
    through nested evals (``eval eval sneak``, round
    32) — read by ``_eval_invoked_name``. (Unresolvable
    evals need no edge — they are execution points in
    their own right.)
    """
    callers: dict[str, list] = {}
    for j, candidate in enumerate(segments):
        if candidate.kind != "command" or not candidate.tokens:
            continue
        name = _call_head_name(candidate)
        if name is None and _eval_is_plain_call(candidate):
            name = _eval_invoked_name(candidate)
        if name is None:
            continue
        callers.setdefault(name, []).append(j)

    def anchors(
        name: str, seen: frozenset, proven_only: bool = False
    ) -> list:
        points: list = []
        for j in callers.get(name, []):
            if proven_only and (
                segments[j].unproven or j in blocked
            ):
                continue
            owner = segments[j].func
            if owner is None:
                if not segments[j].dead:
                    points.append(j)
            elif owner not in seen:
                points += anchors(
                    owner, seen | {owner}, proven_only
                )
        return points

    return anchors


def _unseen_execution_points(segments: list[_Segment]) -> list:
    """The indexes at which unseen text (an unresolvable
    eval, a dot-source) EXECUTES: its own position at the
    top level; the position of the call chain that reaches
    it when it sits in a function body.

    This is computed from execution, not from the segment's
    dead flag — deliberately (round 24): the poison pass
    marks a body dead to void what it PROVES, but a called
    helper's body still EXECUTES, side effects and all.
    Guards that ask "could unseen text have run by here?"
    must look at the call, not at the corpse. The call
    graph itself lives in ``_execution_anchors``. A
    command head that is itself an expansion (``$FN``)
    is a dispatch the checker cannot see: an execution
    point on its own.
    """
    anchors = _execution_anchors(segments)

    points: list = []
    for index, segment in enumerate(segments):
        if not _executes_unseen(segment):
            continue
        if segment.func is None:
            if not segment.dead:
                points.append(index)
            continue
        points += anchors(segment.func, frozenset({segment.func}))
    # A dispatch the checker cannot see.
    for index, segment in enumerate(segments):
        if (
            segment.kind == "command"
            and segment.func is None
            and not segment.dead
            and segment.raw_tokens
            and (
                "$" in segment.raw_tokens[0]
                or "`" in segment.raw_tokens[0]
            )
        ):
            points.append(index)
    return points


def _shadowed_before(
    segments: list[_Segment], names: set, before_index: int
) -> bool:
    """True when a proof-relevant command name may not mean
    what the file appears to say, at the point of use.

    The checker reads command NAMES; the shell resolves them.
    A function definition whose name collides with a credited
    command — ``test() { :; }``, ``systemctl() { :; }``,
    ``lb() { :; }`` — silently replaces the command (functions
    win over builtins and PATH in both dash and bash), an
    ``alias`` for the name (dash expands aliases even in
    non-interactive scripts — round 22), or text the
    checker cannot see (an unresolvable ``eval`` or a
    dot-source before the point of use could have defined
    such a shadow, or rewritten PATH itself). Any of them
    voids the credit.
    """
    if any(
        point < before_index
        for point in _unseen_execution_points(segments)
    ):
        return True
    for index, segment in enumerate(segments[:before_index]):
        if segment.dead:
            continue
        if (
            segment.tokens
            and len(segment.tokens) == 1
            and segment.tokens[0] in names
            and segment.sep_after == "("
        ):
            return True  # a definition of the name itself
        if (
            len(segment.raw_tokens) >= 2
            and segment.raw_tokens[0] == "function"
            and segment.raw_tokens[1] in names
        ):
            return True
        if segment.tokens and _basename(segment.tokens[0]) == "alias":
            for token in segment.tokens[1:]:
                if token.split("=", 1)[0] in names:
                    return True  # alias name=... (or bare name)
    return False


def _path_shadowed_before(
    segments: list[_Segment], before_index: int
) -> bool:
    """True when command resolution at the point of use may
    run through a directory the definition itself planted.

    A literal PATH assignment is visible in the file — but
    visibility is not the line: ``lb() { :; }`` is visible
    too, and it is a shadow. The line drawn here (round
    22): a PATH value whose components are all absolute,
    out-of-tree directories is environment trust, the
    build machine's own business; a value containing a
    RELATIVE or empty component (``./evilbin``, ``bin``,
    a leading ``:``) can point INTO the tree at a planted
    stub, and then the "command" that runs is part of
    the definition — and a definition cannot be allowed
    to supply its own proof commands. The state is
    tracked in order: a later all-absolute assignment
    re-establishes resolution.
    """
    tainted = False
    for segment in segments[:before_index]:
        if segment.dead:
            continue
        # Only PERSISTENT assignments set the state: a
        # plain assignment statement, or an export. A
        # command-prefix assignment governs only its own
        # command (that case is the credited segment's
        # own check, below) — it never persists, in dash
        # or bash (round 26 note).
        values = []
        if segment.kind == "assignment":
            values += [
                assignment.split("=", 1)[1]
                for assignment in segment.assignments
                if assignment.startswith("PATH=")
            ]
        if segment.tokens and _basename(segment.tokens[0]) == "export":
            values += [
                token.split("=", 1)[1]
                for token in segment.tokens[1:]
                if token.startswith("PATH=")
            ]
        for value in values:
            tainted = _path_value_tainted(value)
        if (
            segment.tokens
            and _basename(segment.tokens[0]) == "unset"
            and "PATH" in segment.tokens[1:]
        ):
            # unset PATH after startup leaves NO search
            # path at all (round 28, shell-proved in dash
            # and bash) — nothing external resolves after
            # it, until a later assignment re-establishes
            # one (the ordered loop above already gives a
            # later assignment the last word).
            tainted = True
    # The credited segment itself can carry the taint: a
    # command-prefix assignment (``PATH=./evilbin cmd``)
    # governs the lookup of that very command, and so do
    # assignments handed to ``env`` (round 24).
    if before_index < len(segments) and _segment_own_path_tainted(
        segments[before_index]
    ):
        return True
    return tainted


def _path_value_tainted(value: str) -> bool:
    """True when a PATH value has a relative or empty
    component (``.``, ``bin``, a leading/trailing colon)
    — a directory the definition could have planted.
    Components built from expansions cannot be resolved
    here and are skipped."""
    components = [
        part
        for part in value.split(":")
        if "$" not in part and "`" not in part
    ]
    return any(not part.startswith("/") for part in components)


def _segment_own_path_tainted(segment: _Segment) -> bool:
    """True when a segment's own PATH assignments point at
    a plantable directory.

    Two sources, read in the order the shell applies
    them (the LAST value is the one in force): the
    command-prefix assignments, and any assignments
    handed to an ``env`` anywhere in the wrapper chain —
    ``env -i PATH=…``, ``sudo env PATH=…``,
    ``timeout 5 env PATH=…`` (round 26: options and
    wrappers used to defeat a scan that stopped at the
    first option or looked only at the first word). The
    env scan walks the raw tokens up to the command the
    segment effectively runs, skipping option tokens.
    """
    values = [
        assignment.split("=", 1)[1]
        for assignment in segment.assignments
        if assignment.startswith("PATH=")
    ]
    if segment.tokens and segment.raw_tokens:
        command_base = _basename(segment.tokens[0])
        for token in segment.raw_tokens:
            if _basename(token) == command_base:
                break  # the command itself — assignments end
            if token.startswith("-"):
                continue  # an option (env's, sudo's, timeout's)
            if token.startswith("PATH="):
                values.append(token.split("=", 1)[1])
    tainted = False
    for value in values:
        tainted = _path_value_tainted(value)
    return tainted


def _parse_unit(text: str) -> dict:
    """Parse a systemd unit into {section: {key: [values]}}."""
    sections: dict = {}
    current = None
    for line in _code_lines(text):
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            sections.setdefault(current, {})
            continue
        if current is not None and "=" in line:
            key, _, value = line.partition("=")
            sections[current].setdefault(key.strip(), []).append(
                value.strip()
            )
    return sections


# systemd does not read a unit the way a substring scan
# does (round 22): single-value settings are LAST-wins (a
# second ``Restart=no`` quietly replaces ``on-failure``),
# list settings RESET on an empty assignment
# (``RestartPreventExitStatus=`` wipes the 2 above it),
# and more than one ExecStart makes systemd refuse the
# unit outright. The checks below use those semantics.
_UNIT_SINGLE_VALUE_KEYS = {
    "ExecStart",
    "Restart",
    "User",
    "TTYPath",
    "TTYReset",
    "TTYVHangup",
}


def _unit_effective(values: list, key: str) -> list:
    """The values systemd would act on for one directive."""
    if key in _UNIT_SINGLE_VALUE_KEYS:
        return values[-1:] if values else []
    effective: list = []
    for value in values:
        if value == "":
            effective = []  # an empty assignment resets the list
        else:
            effective.append(value)
    return effective


def _join_results_as_literals(text: str) -> str:
    """Replace each ``SEP.join(parts)`` whose separator and
    parts are all literals with the literal it assembles
    (round 36). A join over literals HAS a literal value;
    reading only the parts inside the call missed every
    concatenation around the result — ``'p' + ''.join(
    ['ur', 'ple'])`` assembles the forbidden word at
    runtime exactly like the plain literal form. Calls
    with a non-literal separator or parts are left as
    written (the join reading in the colour scan still
    reads their literal parts)."""
    pattern = re.compile(
        r"(['\"])((?:(?!\1).)*)\1\s*\.\s*join\s*\("
    )
    out: list = []
    last = 0
    for match in pattern.finditer(text):
        if match.start() < last:
            continue  # inside a span already replaced
        depth = 0
        end = None
        for i in range(match.end() - 1, len(text)):
            char = text[i]
            if char in "([{":
                depth += 1
            elif char in ")]}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end is None:
            continue
        parts = re.findall(
            r"['\"]([^'\"]*)['\"]", text[match.end() : end]
        )
        value = match.group(2).join(parts)
        quote = '"' if "'" in value else "'"
        out.append(text[last : match.start()])
        out.append(quote + value + quote)
        last = end + 1
    if not out:
        return text
    out.append(text[last:])
    return "".join(out)


def check(image_dir: Path) -> list[str]:
    """Return a list of problems with the definition (empty = sound)."""
    image_dir = Path(image_dir)
    repo_root = image_dir.parent
    problems: list[str] = []

    for relative in _REQUIRED_FILES:
        if not (image_dir / relative).is_file():
            problems.append(f"Missing definition file: image/{relative}")

    wants = image_dir / _WANTS_LINK
    if not wants.exists() and not wants.is_symlink():
        problems.append(f"Missing boot-service enable link: image/{_WANTS_LINK}")
    elif not wants.is_symlink():
        problems.append(
            f"image/{_WANTS_LINK} must be a symbolic link to "
            f"{_WANTS_TARGET} so the console starts at boot."
        )
    else:
        import os

        if os.readlink(wants) != _WANTS_TARGET:
            problems.append(
                f"image/{_WANTS_LINK} points at {os.readlink(wants)!r}, "
                f"not {_WANTS_TARGET!r}."
            )
        elif not (wants.parent / _WANTS_TARGET).is_file():
            problems.append(
                f"image/{_WANTS_LINK} points at {_WANTS_TARGET}, "
                "which does not exist in the definition."
            )

    build_sh = image_dir / "build.sh"
    if build_sh.is_file():
        segments = _command_segments(build_sh.read_text(encoding="utf-8"))
        commands = [
            s
            for i, s in enumerate(segments)
            if s.kind == "command"
            and not s.dead
            and not s.unproven
            # A backgrounded command races the steps after it
            # (the checksum above all): it does not count as
            # the build doing the step in order.
            and _effective_tail(segments, i) != "&"
        ]
        sys.path.insert(0, str(repo_root))
        try:
            from beerus.bootmedium import LIVE_VOLUME_LABEL
        except Exception:
            problems.append(
                "Could not read the protected volume label from "
                "beerus.bootmedium in this checkout — the label "
                "agreement check cannot run."
            )
            LIVE_VOLUME_LABEL = None
        finally:
            sys.path.pop(0)
        lb_configs = [
            s
            for i, s in enumerate(segments)
            if s.kind == "command"
            and not s.dead
            and not s.unproven
            and _effective_tail(segments, i) != "&"
            and len(s.tokens) >= 2
            and _basename(s.tokens[0]) == "lb"
            and s.tokens[1] == "config"
            # An ``lb`` shadowed by a same-named function (or
            # an unresolvable eval that could define one) is
            # not the build tool — its flags prove nothing.
            and not _shadowed_before(segments, {"lb"}, i)
            and not _path_shadowed_before(segments, i)
        ]
        if not lb_configs:
            problems.append(
                "build.sh never configures the image (lb config)."
            )
        else:
            # lb config MERGES: per its manual, calling it
            # with one option changes only that option and
            # leaves the rest as they were (round 24
            # correction — an earlier version of this check
            # wrongly assumed each run regenerates). A flag's
            # effective value is therefore the last value any
            # credited config states for it.
            def _effective_flag(flag):
                result = None
                for config in lb_configs:
                    value = _flag_value(config.tokens, flag)
                    if value is not None:
                        result = value
                return result

            if LIVE_VOLUME_LABEL is not None and (
                _effective_flag("--iso-volume") != LIVE_VOLUME_LABEL
            ):
                problems.append(
                    "build.sh's lb config does not set the image "
                    "volume label to the label beerus.bootmedium "
                    f"protects ({LIVE_VOLUME_LABEL}). The "
                    "image-level boot protection depends on the "
                    "two agreeing."
                )
            if _effective_flag("--binary-images") != "iso-hybrid":
                problems.append(
                    "build.sh's lb config does not build a hybrid "
                    "ISO image — the medium and its label are part "
                    "of the design."
                )
        if not any(
            s.kind == "command"
            and not s.dead
            and not s.unproven
            and _effective_tail(segments, i) != "&"
            and s.tokens
            and _basename(s.tokens[0]) == "python3"
            and any(
                _basename(token) == "stage_image.py"
                for token in s.tokens[1:]
            )
            and not _shadowed_before(segments, {"python3"}, i)
            and not _path_shadowed_before(segments, i)
            for i, s in enumerate(segments)
        ):
            problems.append(
                "build.sh does not run stage_image.py — the image "
                "would be built without the package or the marker."
            )
        if not any(
            s.kind == "command"
            and not s.dead
            and not s.unproven
            and len(s.tokens) >= 2
            and _basename(s.tokens[0]) == "lb"
            and s.tokens[1] == "build"
            # A piped build discards the build's own status —
            # the steps after it (the checksum) read success
            # from the pipeline's last command and could bless
            # a stale image; a backgrounded one races them.
            and _effective_tail(segments, i) not in ("|", "&")
            and not _shadowed_before(segments, {"lb"}, i)
            and not _path_shadowed_before(segments, i)
            for i, s in enumerate(segments)
        ):
            problems.append("build.sh never runs the build (lb build).")
        build_credit_indexes = [
            i
            for i, s in enumerate(segments)
            if s.kind == "command"
            and not s.dead
            and not s.unproven
            and s.tokens
            and (
                (
                    _basename(s.tokens[0]) == "lb"
                    and len(s.tokens) >= 2
                    and s.tokens[1] in ("config", "build")
                )
                or (
                    _basename(s.tokens[0]) == "python3"
                    and any(
                        _basename(token) == "stage_image.py"
                        for token in s.tokens[1:]
                    )
                )
            )
        ]
        if build_credit_indexes and any(
            point > max(build_credit_indexes)
            for point in _unseen_execution_points(segments)
        ):
            # In build.sh this slot sits before the image is
            # chosen and checksummed: unseen text there
            # could undo the build steps or bless a
            # different image (round 24).
            problems.append(
                "build.sh runs text the checker cannot see "
                "(an eval or a sourced file) after its build "
                "steps — it could undo them."
            )
        if not _is_executable(build_sh):
            problems.append("build.sh is not executable.")

    package_list = image_dir / "config/package-lists/beerus.list.chroot"
    if package_list.is_file():
        listed = set(_code_lines(package_list.read_text(encoding="utf-8")))
        for package in _REQUIRED_PACKAGES:
            if package not in listed:
                problems.append(
                    f"Package list is missing {package}, which the "
                    "running image needs."
                )
        for package in sorted(listed - _ALLOWED_PACKAGES):
            problems.append(
                f"Package list carries {package}, which is not on "
                "the definition's allowed list — the destruction "
                "environment installs only what it needs. If the "
                "image genuinely needs it, add it to the allowed "
                "list in check_definition.py in the same change."
            )

    service = (
        image_dir
        / "config/includes.chroot/etc/systemd/system/beerus-live.service"
    )
    if service.is_file():
        sections = _parse_unit(service.read_text(encoding="utf-8"))
        for section, directives in _REQUIRED_SERVICE_DIRECTIVES.items():
            if section not in sections:
                problems.append(
                    f"beerus-live.service is missing its [{section}] "
                    "section."
                )
                continue
            for key, value in directives:
                raw_values = sections[section].get(key, [])
                if key == "ExecStart":
                    # For services, an empty ExecStart=
                    # assignment RESETS the list (round 24):
                    # what systemd refuses is more than one
                    # entry in the list that survives.
                    entries: list = []
                    for entry in raw_values:
                        if entry == "":
                            entries = []
                        else:
                            entries.append(entry)
                    if len(entries) != 1:
                        problems.append(
                            "beerus-live.service's [Service] section "
                            "does not carry exactly one ExecStart — "
                            "systemd refuses a service with more "
                            "than one."
                        )
                    elif entries[0] != value:
                        problems.append(
                            "beerus-live.service's [Service] section "
                            f"does not carry the directive "
                            f"ExecStart={value}."
                        )
                    continue
                effective = _unit_effective(raw_values, key)
                if key == "RestartPreventExitStatus":
                    effective = [
                        token
                        for entry in effective
                        for token in entry.split()
                    ]
                if value not in effective:
                    problems.append(
                        f"beerus-live.service's [{section}] section "
                        f"does not carry the directive {key}={value}."
                    )

    hostname_file = (
        image_dir / "config/includes.chroot/etc/hostname"
    )
    if hostname_file.is_file():
        # The boot parameters name the host beerus-key; the
        # file in the image must agree (round-22 scope note:
        # presence alone was checked, content was not).
        hostname = hostname_file.read_text(encoding="utf-8").strip()
        if hostname != "beerus-key":
            problems.append(
                "The image hostname file does not say beerus-key "
                "— it must agree with the boot parameters."
            )

    stage_script = image_dir / "stage_image.py"
    if stage_script.is_file():
        # The staging script is part of the definition: a
        # gutted file would pass every other check while
        # staging nothing (round-22 scope note). Its two
        # jobs must be visible in it.
        stage_text = stage_script.read_text(encoding="utf-8")
        # Judged on code lines, like every other requirement
        # here: a comment mentioning the words does not
        # stage anything (round 24).
        stage_code = "\n".join(_code_lines(stage_text))
        if "live-image.json" not in stage_code:
            problems.append(
                "stage_image.py never writes the image marker "
                "(live-image.json) — the image would boot "
                "without its self-protection."
            )
        if "includes.chroot" not in stage_code:
            problems.append(
                "stage_image.py never stages into includes.chroot "
                "— the image would be built without the package."
            )

    launcher = image_dir / "config/includes.chroot/usr/local/bin/beerus-live"
    if launcher.is_file():
        launcher_text = launcher.read_text(encoding="utf-8")
        segments = _command_segments(launcher_text)
        problems.extend(
            _launcher_grammar_problems(launcher_text, "beerus-live")
        )
        # The launcher is judged the way the shell runs it: its
        # own statements, in order, in its own scope. Only plain
        # assignments persist — a command-prefix assignment dies
        # with its command, an assignment inside a nested shell
        # dies with that shell, and a function body takes effect
        # only where the function is actually called (live, at
        # the top level) before the exec. The last assignment
        # before the exec is the value the console would run
        # with, and the export attribute, once set, stays set.
        bodies: dict[str, list] = {}
        def_index: dict[str, int] = {}
        indexed = [
            (i, s) for i, s in enumerate(segments) if s.depth == 0
        ]
        for i, segment in indexed:
            if segment.func is not None:
                bodies.setdefault(segment.func, []).append((i, segment))
                def_index.setdefault(segment.func, i)

        def effective_statements(pool, active=frozenset(), owner=None):
            for i, item in pool:
                if item.dead or item.unproven or item.in_pipeline:
                    continue
                if item.subshell != 0:
                    # A statement inside ( … ) runs in a child
                    # shell: its assignments and exports die
                    # with it, and an exec there replaces the
                    # child, not the launcher.
                    continue
                if owner is None:
                    if item.func is not None:
                        continue
                elif item.func != owner:
                    continue
                if (
                    item.kind == "assignment"
                    or (
                        item.tokens
                        and _basename(item.tokens[0]) == "export"
                    )
                ) and _effective_tail(segments, i) in ("|", "&"):
                    # An assignment/export whose statement is a
                    # pipeline component or a background job —
                    # including inside a brace group piped or
                    # backgrounded as a whole — never reaches
                    # the launcher's own shell state.
                    continue
                if (
                    item.tokens
                    and item.tokens[0] in bodies
                    and item.tokens[0] not in active
                    and item.subshell == 0
                    and _raw_call_name(item)
                    in (item.tokens[0], "eval")
                    and _effective_tail(segments, i)
                    not in ("|", "&")
                    and i > def_index[item.tokens[0]]
                ):
                    yield from effective_statements(
                        bodies[item.tokens[0]],
                        active | {item.tokens[0]},
                        item.tokens[0],
                    )
                    continue
                yield item

        def body_runs_unseen(name: str, seen=frozenset()) -> bool:
            """True when a function's body — or anything it
            calls, transitively — runs unseen text."""
            if name in seen or name not in bodies:
                return False
            seen = seen | {name}
            for _i, item in bodies[name]:
                if _executes_unseen(item):
                    return True
                callee = (
                    _call_head_name(item) if item.tokens else None
                )
                if callee is not None and body_runs_unseen(
                    callee, seen
                ):
                    return True
            return False

        value: str | None = None
        exported = False
        exec_found = False
        exec_broken = False
        path_tainted = False
        path_unknown = False
        for segment in effective_statements(indexed):
            called = None
            if segment.tokens:
                # A plain-call eval names its callee through
                # the wrapper-aware reader (``command eval
                # sneak`` calls sneak); everything else
                # through the raw head (round 30).
                if _eval_is_plain_call(segment):
                    called = _eval_invoked_name(segment)
                else:
                    called = _raw_call_name(segment)
            if (
                called is not None
                and called in bodies
                and not _is_definition_segment(segment)
                and body_runs_unseen(called)
            ):
                # A called helper whose body runs unseen text:
                # the body itself is poison-dead (it proves
                # nothing), but the call EXECUTES it — its
                # side effects land here (round 24).
                value = None
                exported = False
                path_unknown = True
                continue
            if _is_definition_segment(segment) and segment.func in (
                "exec",
                "export",
            ):
                # A function named exec or export replaces the
                # builtin the proof relies on (round 22;
                # genuine in bash, a syntax error in dash —
                # refused either way).
                exec_broken = True
                continue
            if (
                segment.tokens
                and _basename(segment.tokens[0]) == "alias"
                and any(
                    token.split("=", 1)[0] == "exec"
                    for token in segment.tokens[1:]
                )
            ):
                # alias exec=':' — dash expands it, the
                # console never starts (round 22).
                exec_broken = True
                continue
            if _executes_unseen(segment):
                # Text that cannot be seen (an unresolvable
                # eval, a dot-source) makes the state after
                # it unknowable — it could have assigned or
                # unset anything (the round-20 finding: a
                # good assignment, then an eval that quietly
                # rewrote it). Later plain statements can
                # re-establish known state.
                value = None
                exported = False
                path_unknown = True
                continue
            if segment.kind == "assignment":
                for assignment in segment.assignments:
                    if assignment.startswith("PYTHONPATH="):
                        value = assignment.split("=", 1)[1]
                    elif assignment.startswith("PATH="):
                        # A literal statement re-establishes
                        # the PATH state — known again, and
                        # judged on its value (round 26).
                        path_unknown = False
                        path_tainted = _path_value_tainted(
                            assignment.split("=", 1)[1]
                        )
                continue
            if segment.kind != "command" or not segment.tokens:
                continue
            command = _basename(segment.tokens[0])
            if command == "unset":
                # unset removes state outright (round 28):
                # PYTHONPATH is simply gone, and PATH gone
                # after startup means no search path at
                # all — both void what the exec needs.
                if "PYTHONPATH" in segment.tokens[1:]:
                    value = None
                if "PATH" in segment.tokens[1:]:
                    path_tainted = True
                continue
            if command == "export":
                for token in segment.tokens[1:]:
                    if token == "PYTHONPATH":
                        exported = True
                    elif token.startswith("PYTHONPATH="):
                        value = token.split("=", 1)[1]
                        exported = True
                    elif token.startswith("PATH="):
                        path_unknown = False
                        path_tainted = _path_value_tainted(
                            token.split("=", 1)[1]
                        )
                continue
            if (
                command == "exec"
                and len(segment.tokens) >= 4
                and _basename(segment.tokens[1]) == "python3"
                and segment.tokens[2] == "-m"
                and segment.tokens[3] == "beerus"
            ):
                # The exec resolves python3 through PATH, in
                # the working directory systemd gives it —
                # the image's root, which the definition
                # itself can plant into (round 24). The exec
                # credit asserts PATH is not plantable, so
                # it needs the state KNOWN and clean: a
                # plantable value refuses, and so does an
                # unknown one — unseen text before the exec
                # could have rewritten PATH itself, exactly
                # as it could PYTHONPATH, and the state is
                # unknown until a literal statement
                # re-establishes it (round 26).
                if (
                    not path_tainted
                    and not path_unknown
                    and not _segment_own_path_tainted(segment)
                ):
                    exec_found = True
                break
        if exec_broken:
            exec_found = False
        if value is None or "/opt/beerus" not in _path_components(value):
            problems.append(
                "The console launcher does not put the staged "
                "package (/opt/beerus) on PYTHONPATH."
            )
        elif not exported:
            problems.append(
                "The console launcher sets PYTHONPATH but never "
                "exports it — the console would not see the staged "
                "package."
            )
        if not exec_found:
            problems.append(
                "The console launcher does not start the boot "
                "console (python3 -m beerus)."
            )
        if not _is_executable(launcher):
            problems.append("The console launcher is not executable.")
        # The graphical-first design (Phase 4), judged from the
        # same analysed segments: startx with the boot UI as its
        # client, the status captured and both terminal codes
        # honored, and the text fallback's exec after the attempt
        # as the final command.
        problems.extend(_graphical_first_problems(segments))

    ui_client = (
        image_dir / "config/includes.chroot/usr/local/bin/beerus-ui"
    )
    if ui_client.is_file():
        ui_text = ui_client.read_text(encoding="utf-8")
        ui_segments = _command_segments(ui_text)
        problems.extend(
            _launcher_grammar_problems(ui_text, "beerus-ui")
        )

        def _is_bootui_exec(segment: _Segment) -> bool:
            return (
                segment.kind == "command"
                and len(segment.tokens) >= 4
                and _basename(segment.tokens[0]) == "exec"
                and _basename(segment.tokens[1]) == "python3"
                and segment.tokens[2] == "-m"
                and segment.tokens[3] == "beerus.bootui"
            )

        if not any(
            _is_bootui_exec(segment)
            and not segment.dead
            and not segment.unproven
            for segment in ui_segments
        ):
            problems.append(
                "beerus-ui does not start the boot UI "
                "(python3 -m beerus.bootui)."
            )
        # The client must put the staged package on its Python
        # path and export it, exactly as beerus-live does for
        # the text session: without it the graphical session
        # cannot import the staged package and can never start.
        # Judged on the state in force at the boot UI exec —
        # the last plain assignment before it, and whether the
        # export attribute was set by then.
        ui_value: str | None = None
        ui_exported = False
        for segment in ui_segments:
            if segment.depth != 0 or segment.subshell != 0:
                continue
            if _is_bootui_exec(segment) and not segment.dead:
                break
            if segment.kind == "assignment":
                for assignment in segment.assignments:
                    if assignment.startswith("PYTHONPATH="):
                        ui_value = assignment.split("=", 1)[1]
                continue
            if (
                segment.kind == "command"
                and segment.tokens
                and _basename(segment.tokens[0]) == "export"
            ):
                for token in segment.tokens[1:]:
                    if token == "PYTHONPATH":
                        ui_exported = True
                    elif token.startswith("PYTHONPATH="):
                        ui_value = token.split("=", 1)[1]
                        ui_exported = True
                continue
            if (
                segment.kind == "command"
                and segment.tokens
                and _basename(segment.tokens[0]) == "unset"
                and "PYTHONPATH" in segment.tokens[1:]
            ):
                ui_value = None
        if ui_value is None or "/opt/beerus" not in _path_components(
            ui_value
        ):
            problems.append(
                "beerus-ui does not put the staged package "
                "(/opt/beerus) on its Python path — the "
                "graphical session would not find the staged "
                "package and could never start."
            )
        elif not ui_exported:
            problems.append(
                "beerus-ui sets its Python path but never "
                "exports it — the graphical session would not "
                "see the staged package."
            )
        if ui_value is not None:
            # The path value must be written out in full: the
            # only expansion allowed in it is the append of the
            # path's own previous value. A substitution would
            # run a command while the path is set, and another
            # variable would make the proved path a different
            # path at run time.
            path_rest = re.sub(
                r"\$\{PYTHONPATH[^}]*\}", "", ui_value
            )
            path_rest = re.sub(
                r"\$PYTHONPATH(?![A-Za-z0-9_])", "", path_rest
            )
            if "$" in path_rest or "`" in path_rest:
                problems.append(
                    "beerus-ui sets its Python path from a value "
                    "that is not written out in full — a "
                    "substitution or another variable in that "
                    "value would run or expand while the path "
                    "is set, and the path the check proves must "
                    "be the path that runs."
                )
        # The client does exactly one thing: it starts the
        # boot UI. Its only effective command may be that exec
        # (assignments and the export of the path are setup,
        # not commands) — an extra command before it (the
        # text session's module run first, in a subshell or
        # not), one after it, or a different module in its
        # place all mean the client runs something besides —
        # or instead of — the graphical session it exists to
        # start. Counted as written, dead or not: a command
        # parked after an exec is still a second command in
        # the file, and this client's shape is the design.
        effective = [
            segment
            for segment in ui_segments
            if segment.kind in ("command", "noop")
            and not (
                segment.tokens
                and _basename(segment.tokens[0]) == "export"
            )
        ]
        if len(effective) != 1 or not _is_bootui_exec(effective[0]):
            problems.append(
                "beerus-ui must contain exactly one effective "
                "command — the boot UI exec — and nothing "
                "before or after it; an extra command there "
                "runs alongside the graphical session."
            )
        if not _is_executable(ui_client):
            problems.append("beerus-ui is not executable.")

    hook = image_dir / "config/hooks/live/0900-beerus.hook.chroot"
    if hook.is_file():
        segments = _command_segments(hook.read_text(encoding="utf-8"))
        commands = [
            s
            for i, s in enumerate(segments)
            if s.kind == "command"
            and not s.dead
            and not s.unproven
            # A backgrounded command's effect is unsequenced —
            # the hook can finish before it lands (the same
            # race the build checks refuse for lb build).
            and _effective_tail(segments, i) != "&"
        ]
        # A needle counts only where the hook actually does the
        # job: the staged files must be existence-tested by their
        # exact paths with a failure that stops the hook, and
        # getty@tty1 itself must be masked by systemctl. A needle
        # printed, stored, carried as a bystander argument, tested
        # at the wrong path, or tested and discarded proves
        # nothing.
        # A proof also dies if the command it relies on may
        # have been shadowed by a same-named function (or an
        # unresolvable eval that could define one) before the
        # point of use — name-binding is part of the proof.
        live_index = _first_proven_index(
            segments, "/opt/beerus/beerus/live.py"
        )
        marker_index = _first_proven_index(
            segments, "/etc/beerus/live-image.json"
        )
        mask_index = next(
            (
                i
                for i, s in enumerate(segments)
                if s.kind == "command"
                and not s.dead
                and not s.unproven
                and _effective_tail(segments, i) != "&"
                and s.tokens
                and _basename(s.tokens[0]) == "systemctl"
                and "mask" in s.tokens
                and any(
                    t in ("getty@tty1", "getty@tty1.service")
                    for t in s.tokens
                )
            ),
            None,
        )
        hook_proofs = (
            (
                "live.py",
                "prove the staged console is in the image",
                live_index is not None
                and not _shadowed_before(
                    segments,
                    {"test", "[", "[[", "set", "exit"},
                    live_index,
                ),
            ),
            (
                "live-image.json",
                "prove the image marker is in the image",
                marker_index is not None
                and not _shadowed_before(
                    segments,
                    {"test", "[", "[[", "set", "exit"},
                    marker_index,
                ),
            ),
            (
                "getty@tty1",
                "keep the tty1 login away from the console",
                mask_index is not None
                and not _shadowed_before(
                    segments, {"systemctl"}, mask_index
                )
                and not _path_shadowed_before(segments, mask_index),
            ),
        )
        for needle, why, proven in hook_proofs:
            if not proven:
                problems.append(
                    f"The build hook does not mention {needle} — it "
                    f"must {why}."
                )
        bootui_index = _first_proven_index(
            segments, "/opt/beerus/beerus/bootui/app.py"
        )
        if bootui_index is None or _shadowed_before(
            segments,
            {"test", "[", "[[", "set", "exit"},
            bootui_index,
        ):
            problems.append(
                "The build does not prove the staged boot UI "
                "(beerus/bootui/app.py) is in the image — the "
                "graphical session would have nothing to run."
            )
        hook_credit_indexes = [
            i
            for i in (live_index, marker_index, mask_index, bootui_index)
            if i is not None
        ]
        if hook_credit_indexes and any(
            point > max(hook_credit_indexes)
            for point in _unseen_execution_points(segments)
        ):
            # Unseen text that runs AFTER the checks can
            # undo them — unmask the login, replace the
            # files (round 24).
            problems.append(
                "The build hook runs text the checker cannot "
                "see (an eval or a sourced file) after its "
                "checks — it could undo them."
            )
        if not _is_executable(hook):
            problems.append("The build hook is not executable.")

    for path in sorted(image_dir.rglob("*")):
        if path.is_file() and not path.is_symlink():
            relative = path.relative_to(image_dir)
            if relative.parts[:3] == _STAGED_PREFIX or (
                str(relative) == _STAGED_MARKER
            ):
                continue  # staged build output, not definition content
            if str(relative) == "check_definition.py":
                # This file defines the needle itself, in
                # pieces, on purpose — scanning it could
                # only ever match its own constant.
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            # The scan reads the value the shell would build:
            # quotes are stripped (adjacent fragments join),
            # backslash-newline continuations are joined, and
            # expansions are removed (an empty expansion
            # contributes nothing) — the word split any of
            # those ways IS the word at runtime (rounds
            # 22/24).
            squashed = content
            py_assembled = False
            if path.suffix == ".py":
                # Python assembles adjacent and +-joined
                # string literals at runtime (round 26);
                # join those seams before the general squash.
                # Literal join() calls are first read as the
                # literals they assemble (round 36), so
                # concatenation around a join combines with
                # its result here and in the chr reading.
                squashed = _join_results_as_literals(squashed)
                squashed = re.sub(
                    r"(['\"])\s*\+\s*(['\"])", r"\1\2", squashed
                )
                squashed = re.sub(
                    r"(['\"])(\s+)(['\"])",
                    lambda match: match.group(1) + match.group(3),
                    squashed,
                )
                # Two runtime assemblies of the same class
                # (round 28): a join() over a literal list
                # or tuple, and a run of chr() codes. Both
                # are read narrowly — the literal arguments
                # as written — not by evaluating the file.
                # Comments are stripped for this reading
                # (round 30: a chr run may be split by a
                # comment seam inside the expression).
                code_only = _join_results_as_literals(
                    re.sub(r"#[^\n]*", "", content)
                )
                # The join argument is read with a balanced
                # scan, not a flat pattern (round 32): a
                # star-unpacked nested list ends a flat
                # pattern early, but the literals inside
                # still join at runtime.
                for join_at in re.finditer(r"join\s*\(", code_only):
                    depth = 0
                    for end in range(join_at.end() - 1, len(code_only)):
                        char = code_only[end]
                        if char in "([{":
                            depth += 1
                        elif char in ")]}":
                            depth -= 1
                            if depth == 0:
                                break
                    else:
                        continue
                    listed = code_only[join_at.end() : end]
                    joined = "".join(
                        re.findall(r"['\"]([^'\"]*)['\"]", listed)
                    )
                    if _FORBIDDEN_WORD in joined.lower():
                        py_assembled = True
                # A chr run may be interleaved with string
                # literals (round 32): the expression's
                # value is the concatenation, so the run
                # accumulates literal text found between
                # codes whenever the gap is only joiners
                # and literals.
                code_pattern = r"chr\((0x[0-9a-fA-F]+|\d+)\)"
                current = ""
                last_end = None

                def absorb(gap: str, run: str) -> str:
                    literals = re.findall(
                        r"['\"]([^'\"]*)['\"]", gap
                    )
                    residue = re.sub(
                        r"['\"][^'\"]*['\"]", "", gap
                    )
                    if residue.strip(" \t\r\n+(),"):
                        return "".join(literals)
                    return run + "".join(literals)

                for match in re.finditer(code_pattern, code_only):
                    if last_end is not None:
                        current = absorb(
                            code_only[last_end : match.start()],
                            current,
                        )
                    else:
                        # The literal BEFORE the first code
                        # is part of the same concatenation
                        # (round 34): the last literal in the
                        # leading text counts when only
                        # joiners separate it from the run.
                        leading = code_only[: match.start()]
                        literals = list(
                            re.finditer(
                                r"['\"]([^'\"]*)['\"]", leading
                            )
                        )
                        if literals and not leading[
                            literals[-1].end() :
                        ].strip(" \t\r\n+(),"):
                            current = literals[-1].group(1)
                    current += chr(int(match.group(1), 0))
                    if len(current) >= 3 and (
                        _FORBIDDEN_WORD in current.lower()
                    ):
                        py_assembled = True
                    last_end = match.end()
                if last_end is not None:
                    current = absorb(code_only[last_end:], current)
                    if _FORBIDDEN_WORD in current.lower():
                        py_assembled = True
            squashed = squashed.replace("'", "").replace('"', "")
            squashed = squashed.replace("\\\r\n", "").replace(
                "\\\n", ""
            )
            squashed = re.sub(
                r"\$\{[^}]*\}|\$[A-Za-z_][A-Za-z0-9_]*", "", squashed
            )
            if py_assembled or _FORBIDDEN_WORD in squashed.lower():
                problems.append(
                    f"image/{relative} mentions "
                    "the forbidden colour — the brand is green and "
                    "black only."
                )

    return problems


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    image_dir = Path(argv[0]) if argv else Path(__file__).resolve().parent
    problems = check(image_dir)
    if problems:
        for problem in problems:
            print(f"check_definition: {problem}")
        return 1
    print(
        "Image definition check passed: the definition is complete "
        "and consistent. (This does not build the image — see "
        "docs/IMAGE-BUILD.md.)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
