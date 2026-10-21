# BEERUS.EXE — Build Roadmap

The destruction chart. Phases run in order, and the order is the
point: **the wipe engine and its consent gates are designed
first** — destruction that can't prove what it's about to destroy
doesn't ship, and the bootable image comes only after the engine
has earned it.

**The one job:** boot any machine from Live Boot, pick
a drive, destroy its data to NIST 800-88, verify the result, and
hand over a certificate of destruction.

**Safety law:** the target's model AND serial are shown before
anything runs, confirmation is deliberate and multi-step, scope
is announced in full before the wipe, the boot key itself can
never be a target, and nothing happens silently. No exceptions,
no settings that weaken them.

---

## Phase 1 — Drive inventory + wipe engine core (consent gates from line one)

The foundation phase — and the gates are part of the foundation,
not a later coat of paint.

- **Drive inventory:** enumerate every attached drive with model,
  serial number, size, and interface — the identification the
  consent gates stand on. A drive Beerus cannot identify is a
  drive Beerus will not wipe.
- **Boot-device self-exclusion:** the device Beerus booted from
  is identified and permanently excluded from the target list —
  enforced in the engine, not just hidden in the UI.
- **Consent-gate framework:** model + serial displayed
  prominently, deliberate multi-step confirmation, scope
  announced in full before any destructive call exists to
  confirm. The gates are built with the engine, not after it.
- **Method selection per NIST 800-88:** ATA Secure Erase /
  Security Erase for SATA, NVMe Format / Sanitize for NVMe,
  multi-pass overwrite for spinning disks — chosen by the drive's
  own reported capabilities.
- **Wipe execution with honest progress:** announced scope is the
  actual scope; progress and errors are reported in plain
  language while the wipe runs.

**Status: COMPLETE — shipped in v0.2.0 (2026-10-20).**

As built: `beerus/inventory.py` enumerates drives from `lsblk`
(model, serial, size, interface, WWN) and marks the boot device
from the mount tree; a drive whose model or serial is missing or
a firmware placeholder is unidentifiable, and unidentifiable
drives are refused at every layer. `beerus/methods.py` selects
per NIST 800-88 — NVMe Sanitize and ATA Secure Erase (Purge),
four-pass overwrite for spinning disks (Clear); USB-attached and
unclassifiable drives get a plain-language refusal in this
version. `beerus/gates.py` is the consent framework: typed-serial
identity confirmation, full scope acknowledgment, and an exact
`ERASE <serial>` final phrase, minting a single-use, expiring,
HMAC-bound confirmation token. `beerus/engine.py` re-enumerates
and re-verifies the target's identity (model, serial, size,
interface, WWN — deliberately *not* the device path, so
re-lettering changes nothing) before the token is consumed; the
executor is unreachable before every check passes.
`beerus/executor.py` is the single destructive seam: a
file-backed executor that only ever writes to regular files, and
the real-drive executor, which ships **disarmed** (constructor
flag *and* environment variable, checked before anything runs)
until the boot-image entry point arms it in a later phase. The
suite is 188 tests, all against synthetic targets — no test can
touch a real drive. An independent audit of the safety
invariants (findings F1–F12) was fixed and re-audited clean.

Honest residuals, by design: identity rests on what the drive
reports — two drives reporting identical model, serial, size,
and WWN are indistinguishable to any software; boot detection in
this phase reads the mount tree (an unmounted boot partition is
invisible to it), and Phase 3's image-level self-protection is
the second, independent layer for exactly that case.
Sacrificial-drive validation on real hardware remains Phase 5.

## Phase 2 — Verification + certificate of destruction

- **Post-wipe verification pass:** sample the wiped drive and
  render an honest verdict — pass or fail, no softening either
  way. A fail blocks a clean certificate.
- **Certificate of destruction generator:** a printable
  certificate for every wipe — drive model, serial number,
  method used, date/time, verification result, operator.
- **Job records:** certificates are generated on the job and kept
  as the client's business record — never committed to this repo
  (they carry client drive serials).

**Status: COMPLETE — shipped in v0.3.0 (2026-10-20).**

As built: `beerus/verify.py` verifies per method. Overwrite
(Clear) plans are read back across the full surface and checked
byte-for-byte against the plan's final pass; a plan ending in a
random pass cannot be byte-verified and is honestly reported
NOT VERIFIED. NVMe (Purge) plans are verified by the drive's
own sanitize status — read through the same command-builder
seam the executor uses — plus a sampled read-back (64 regions,
overlaps never double-counted); ATA Secure Erase has no status
read in this architecture, so its verification rests on the
sampled read-back and says so. Sampled results always state the
exact bytes and regions checked against the drive's total. A
check that cannot be performed — unreadable surface, failed
read, missing firmware status — is NOT VERIFIED, never a pass;
positive evidence of surviving data is a FAIL with the remnant
count and first offsets. Verification results are HMAC-signed
at mint with a per-run key and bound to the drive's identity,
the method that actually ran (including the NVMe Sanitize →
Format fallback), and every reported figure, so an edited or
hand-built result does not verify. When a verifier is wired
into the engine, verification runs after the wipe completes
and rides on the wipe result; a verification error attaches a
NOT VERIFIED result rather than failing the run.
`beerus/certificate.py` builds the job record from the drive,
the wipe result, and the operator, and refuses — at record
build, at document issue, and again in the renderers — any
record whose verification is unsigned, edited, or bound to a
different drive, size, or method. Only a genuine PASS earns
the printable HTML certificate (model, serial, capacity,
method + NIST 800-88 category, start/finish times, the
verification verdict with its coverage, operator, record ID);
everything else receives the failure report with the
verification's own findings and the next steps. Documents are
written locally with record-ID-bound filenames and are never
silently overwritten. The independent audit of this phase
(findings F1–F14, including the record-binding hole the first
draft left between the signed verification and the rest of the
record) was fixed and re-audited clean; the suite is 257 tests,
all against synthetic targets and fake runners.

Honest residuals, by design: certificates must be issued in
the same run as the wipe — the signing key lives only for the
run, so a past run's result cannot be re-certified later (a
certificate archive format is in the backlog below). Purge
read-back is a sample by nature: firmware erases happen where
no read-back can see, which is why the drive's own status is
checked where one exists and the sample's coverage is always
disclosed. The read-back surface reader for real drives opens
read-only and trusts the drive path exactly as the executor
does; hardware truth for all of this is Phase 5's
sacrificial-drive validation.

## Phase 2.5 — Beerus Portable: the app edition + file modes

Owner direction (2026-10-20): Beerus has TWO forms. The live
USB stays; alongside it, a portable app edition runs the same
engine on an already-booted Windows machine, and the tool wipes
files, folders, and free space as well as whole drives.

**As built (v0.4.0):**

- **`beerus_portable` — the app.** A PySide6 window over the
  existing engine: no forked wipe logic, no second gate. The
  Windows inventory seam (`beerus_portable.windows_inventory`)
  reads drives through read-only PowerShell queries and
  translates them into the engine's own `Drive` records.
- **Live-system refusal rules.** The drive Windows runs from and
  the drive the app itself runs from are marked `is_boot`, so
  the engine's and gates' boot-device refusals protect them at
  every layer; the app adds the plainer portable wording in
  front. Unidentifiable drives are refused by the engine's own
  rule, unchanged.
- **Three target modes.** Whole drive (the full drive flow,
  certificates included); files & folders; free space. The
  gate pages are shared: scope statement → acknowledgment →
  exact typed phrase → execution — with drive mode's
  typed-serial step in front where a drive is the target.
- **`beerus.filewipe` — the file-level engine.** Scope is built
  before consent (folders walked at build time; count and
  bytes announced; links recorded, never followed; protected
  OS locations refused plainly). Execution overwrites each
  file in full — the engine's own overwrite routine and pass
  plan — flushes, reads back and verifies BEFORE the entry is
  removed, then removes the blanked entry last
  (rename-to-random + truncate + delete). Failures are
  per-item with reasons; locked files are reported, never
  forced. Free-space mode fills a volume's free space across
  the same pass plan and samples the filler back.
- **Consent + documents for file modes.** An HMAC-bound gate
  authority mirrors the drive gates (scope-hash binding,
  exact phrase naming the count, single-use expiring tokens).
  Runs issue a signed wipe REPORT — the certificate module's
  anti-forgery posture, plainly not a certificate of
  destruction, which stays reserved for whole-drive work.
- **Honesty, on screen and in the report.** The SSD
  wear-leveling/TRIM disclosure and the other-copies
  disclosure (shadow copies, temp files, thumbnails,
  journals) are designed text at mode selection, in the scope
  statement, and in the report — pinned by tests.
- **Packaging + release.** One-folder PyInstaller build
  (`packaging/Beerus.spec`), tag-gated Windows workflow: full
  suite offscreen, staged-app launch smoke test,
  `BeerusPortable.zip` + `SHA256SUMS.txt`. The shipped
  whole-drive executor stays disarmed by both factors; the
  file engines write only what their consent tokens
  authorize.
- The boot line gains this engine capability too: when the
  Phase 4 boot UI is built, file/folder and free-space modes
  are available to it from `beerus.filewipe` — no second
  implementation.

**Status: COMPLETE (v0.4.0).**

## Phase 3 — Bootable image definition

- **Debian live-system definition, versioned in this repo:** the
  full definition of the Destruct Key Pro image — packages,
  configuration, and boot behavior — so the key is reproducible
  from source.
- **Boot straight into the tool:** the machine boots from USB
  directly into Beerus; the host system never starts, and no host
  drive is mounted or touched before the operator chooses a
  target.
- **USB self-protection at the image level:** the boot device is
  protected by the image design itself, backing the engine's
  self-exclusion with a second, independent layer.

**Status: COMPLETE — definition shipped in v0.5.0 (2026-10-20).**

As built: the definition lives under `image/`. `build.sh` is
the build: it stages (`stage_image.py` — a fresh copy of the
`beerus` package from the checkout, plus the image marker
generated from the package's own constants), configures a
Debian bookworm live system as a hybrid ISO with the volume
label `BEERUS_KEY`, and builds with live-build, checksumming
the result. `config/` carries the rest of the definition: the
package list (the interpreter, `lsblk`, `hdparm`, `nvme-cli`,
the live system, a small operator toolkit — nothing that opens
a network service), the files baked into the image (hostname;
the `beerus-live` systemd service; the console launcher), and
a build hook that fails the build if the staged package or the
marker is missing, masks the ordinary first-console login, and
keeps network services off. `docs/IMAGE-BUILD.md` is the full
procedure: build-machine setup, the build, writing the image
to a USB key raw (so the label survives), and the first-boot
verification checklist. `check_definition.py` validates the
definition without a build machine — required files, the
build's volume label matching the protection code's label, the
engine's tools in the package list, the service starting the
launcher — and the test suite runs it.

Boot flow: the `beerus-live` service owns tty1 and starts
`beerus.live` — a terminal console over the *same*
`GateAuthority`, `WipeEngine`, `WipeVerifier`, and certificate
code as the portable app (no second gate implementation). It is
also the boot-image entry point Phase 1 reserved: the only
place the real-drive executor is armed, and only after the
running system proves it is the live image (the build-time
marker at `/etc/beerus/live-image.json` loads and validates —
including naming this image's own volume label) and the session
is root. The proofs are demanded by the arming function itself,
not trusted to its caller — anything else gets a plain-language
refusal and exit code 2 before an engine exists. The live
verifier is wired exactly as the portable launcher wires it,
firmware-status runner included, so an NVMe wipe on the key
can be verified and certified the same way. Job documents
are written to memory-backed storage in the live system, never
to the key, and the console states at the start and after every
document that they are gone at shutdown unless copied off; the
Phase 4 UI inherits these flows and this documents behavior.

The phase's independent audit (3 HIGH, 4 MEDIUM, 7 LOW) was
fixed and re-audited: the proofs moved inside the arming
function itself, the live verifier gained the firmware-status
runner the portable launcher already wired (without it, no
NVMe wipe on the key could ever be certified), menu input was
locked to listed numbers only, the console's error seams were
hardened to plain language throughout, identity strings are
cleaned of control characters at parse time, the marker must
name the image's own label with its documents directory
constrained to memory, and the definition checker learned to
parse rather than substring-match. Each fix carries a
regression test. A final re-audit of the checker itself found
indirect spellings — wrapped, prefixed, compounded, or merely
stored commands — that could still satisfy it; the checker's
command analysis was rebuilt to segment each command and
identify it by the program that actually runs, with flags,
exports, and the exec'd program matched by token. Every
bypass from that round is rejected, each pinned by a
regression test. A second re-audit of the rebuilt checker
pushed further — heavier wrappers, a nested shell, bystander
commands carrying the words as plain arguments, and a
PYTHONPATH that merely mentioned the staged directory — so
the hook check became semantic: the staged files count only
where a command existence-tests them by their full paths,
getty counts only where systemctl masks it, and paths are
matched as whole components. Every bypass from every round
is now rejected and pinned. A third re-audit pushed further
still — tests of the wrong location, a look-alike unit name,
proofs parked in a dead branch or discarded with
``|| true``, and a launcher value shadowed, scoped away, or
attached to the wrong command — so the hook proofs are
anchored to the exact staged paths and the exact unit, a
proof's failure must actually stop the hook, dead code counts
for nothing, and the launcher is judged as ordered state in
its own scope. A fourth re-audit pushed into the corners of
that model — constant-false conditions spelled with negation
or literal arithmetic, a ``set -e`` that arrived late or was
switched back off, an exit offered from inside a subshell,
and proofs parked in a function nobody calls — and the
checker now evaluates constant conditions, tracks errexit in
order, refuses subshell exits as enforcement, and counts a
function body only when the function is actually called. A
fifth re-audit probed the remaining corners — arithmetic
conditions, ``case`` arms, functions invoked only from a
subshell or only after the exec — and the checker now
evaluates literal arithmetic conditions (and trusts no
condition it cannot evaluate), tracks ``case`` arm
reachability, and credits a function body only at a live
top-level call site. A sixth re-audit found the same constant
conditions guarding commands through ``&&``/``||`` instead of
``if`` — the checker now judges a guarded command by its
guard's evaluated value, in every file it checks. A seventh
re-audit found the chain form of the same family — a skipped
command's own value must not resurrect the chain — and the
checker now propagates chain status the way the shell does:
a command that never ran changes nothing. An eighth re-audit
respelled the chain across a line break and into a pipeline —
the checker now carries chains across operator-continued
lines, and treats pipeline components as the subshell scope
they are for enforcement. A ninth re-audit respelled the
exemption itself: a test credited only through ``set -e``
stays unenforced when it is a non-final ``&&`` member,
backgrounded, or negated (including as the last component of
a negated pipeline) — the checker now withholds the errexit
credit in exactly those positions, credits a chain that
genuinely reaches an ``|| exit``, and refuses a
backgrounded exit as enforcement. A tenth re-audit found
the same exemptions one segment further out — on a group's
closing operator, at a function call site, inside a
command substitution, in negation the segmenter had
stripped — and the checker now propagates statement
context instead of judging segments alone: the effective
tail through group closes, statement-level negation,
call-site errexit inheritance (errexit is judged where a
body is called, not where it is defined), subshell and
background persistence for the launcher, and a
backgrounded build step no longer counting as the step
done in order. An eleventh re-audit found the pattern one
construct further out again — wrapper-prefixed calls that
never invoke the function, ``set -e`` armed inside a piped
or backgrounded brace group, non-final tests inside an
exempt group, a paren group spread across lines, layered
negation, empty ``for`` lists, and function redefinition —
and the checker now recognises calls by their raw head
(``eval`` excepted, which truly runs its argument),
refuses state set in a child shell however it is grouped,
judges a grouped test by its group's position, records
multi-line group closes, evaluates empty ``for`` lists,
and lets the last definition win; a backgrounded mask and
a piped ``lb build`` stopped counting for the same
unsequenced/discarded-status reasons as before. A twelfth
re-audit closed the three holes that remained: brace-group
tails inside function bodies (the group scan now looks
inside bodies, bounded by the body's own brace), ``eval``
credited without reading its argument (eval is now
transparent — the evaluated word is the call, so
``eval command …`` is a wrapper call and an eval that
redefines a function voids the static body's proofs),
and ``for`` lists the checker cannot resolve (a body
proves only over a fully literal list). A thirteenth
re-audit closed the extensions of those families: any
``eval`` that is not a plain visible call — variable,
substitution, escaped, wrapper-prefixed, or hidden in a
called helper — now poisons function-body credit at its
execution point (indirection is not graded); loop bodies
in general prove only when iteration is guaranteed
(``while`` on a constant-true condition, ``until`` on a
constant-false one, ``for`` over at least one fully
literal word — one literal is enough, as the shell
guarantees that iteration). A fourteenth re-audit found
the state the checker had never modelled at all —
name-binding: a function named like a credited command
(``test() { :; }``, ``systemctl() { :; }``, ``lb() { :; }``)
silently replaces it, plain or eval-introduced, and an
unresolvable eval can rewrite the variables a proof
depends on. Credited commands are now void when their
name was defined earlier in the script or an
unresolvable eval precedes their use, and the
launcher's ordered state goes unknown at an
unresolvable eval until plain statements re-establish
it. (A literal PATH assignment in the file is not a
shadow: it is visible in the definition text, there
to be read.) A fifteenth re-audit closed the
name-binding perimeter and corrected that boundary:
aliases (dash expands them in non-interactive
scripts) and dot-sourcing (unseen text, like eval)
now void credits exactly as definitions and evals
do; a literal PATH assignment whose value has a
relative or empty component voids external-command
credits too — it can point into the tree at a planted
stub, so the definition would be supplying its own
proof commands (all-absolute values remain
environment trust); the unit file is read with
systemd's own semantics (single-value settings are
last-wins, list settings reset on an empty
assignment, a second ExecStart is a refusal); the
flags that matter are read from the LAST lb config;
the colour scan strips quotes first (the shell joins
quoted fragments); and the hostname file's content
and the staging script's two jobs are checked, not
just their presence. A sixteenth re-audit found the
poison policy hiding its own evidence: a helper whose
body runs unseen text is marked dead (its proofs are
void), and the guards had been skipping dead segments
— so unseen text inside a called helper executed
invisibly at the call. Guards now ask where unseen
text EXECUTES (top level: its own position; in a
body: its call's), never whether its segment is dead.
The same round closed the taint riding on the
credited command itself (command-prefix and env
PATH assignments), gave the launcher's exec the PATH
coverage it lacked (its python3 resolves in the
image's own root directory), refused unseen text
running after the last check (it can undo the
checks), taught the colour scan the expansion and
continuation spellings, read the staging script's
jobs on code lines only, and corrected two of the
checker's own models: lb config merges (a flag's
effective value is the last value stated for it),
and an empty ExecStart= resets the list, so one real
entry after a reset is valid. A seventeenth re-audit
walked the call graph: unseen text two helpers deep,
a helper called as a condition or a negation, and a
dispatch through a variable all executed invisibly —
execution points are now computed transitively over
every syntactic call, and an expansion-headed command
is an execution point on its own. The launcher's
PATH is unknown after unseen text, exactly like its
PYTHONPATH, until a literal statement re-establishes
it (the round-24 decision to trust it did not hold:
a planted python3 ran). The env PATH scan walks the
whole wrapper chain (env's options, sudo, timeout);
command-prefix PATH assignments no longer taint
later credits (they never persist — their own
command is judged separately, last value in force);
and the colour scan joins Python string-literal
seams in .py files (the checker does not scan its
own source — it defines the needle in pieces, on
purpose). An eighteenth re-audit composed the
pieces: a plain-call ``eval`` is now an edge in the
execution graph (unseen text inside an eval-called
helper had escaped it); ``unset`` is modelled (an
unset PATH after startup leaves no search path at
all and voids external credits until re-established;
an unset PYTHONPATH in the launcher is simply
gone); a bare ``exec`` terminates a script exactly
like ``exit``; the colour scan reads join() lists
and chr() runs in Python files; and defining an
unseen-bodied helper — never called — no longer
voids the launcher on its own. A nineteenth
re-audit composed the last two pieces: an eval
behind a wrapper (``command eval sneak``) is an
execution edge again — the name is read through
the wrapper, as the eval detector walks — and
termination became an execution-point question
like unseen text: an exec terminates at its own
position when it provably runs in the main shell
(chained on a proven guard, inside a taken branch,
or at a called helper's call point), while an exec
in a subshell or a pipeline, and an exec with no
command at all (redirections only), terminate
nothing. The colour scan's join reading covers
tuples, its chr reading hex codes and comment
seams. A twentieth re-audit composed the graph
itself: termination now runs through the same
transitive execution graph as unseen text — an
exec inside a helper called through eval, as a
condition, under negation, or two helpers deep
terminates at the outer call — and ``exit`` runs
through it alongside ``exec`` (it had only the
bare-spelling rule); nested evals unwrap
(``eval eval sneak`` invokes sneak); and a
terminator counts only where it is PROVEN reached
on every path, so the guard blocks behind
unevaluable conditions — the shape the shipped
scripts use — keep the code after them credited.
The colour scan reads chr() runs interleaved
with string literals and join arguments with a
balanced scan (a star-unpacked nested list no
longer ends the reading early). Two older loop
controls were corrected in the same round: an
unconditional ``exit`` they carried in a loop
body genuinely ends the script before the proofs
after the loop, so their guards are now the
``|| exit`` form shell semantics requires. A
twenty-first re-audit closed the last two narrow
holes: a terminator (or a proof) sharing a segment
with ``else`` was judged with the then branch's
frame state — inverted both ways — and the
segmenter and the termination replay now switch
the frame before judging an else segment; and the
colour scan absorbs the literal before the first
chr() code, completing its reading of the
concatenated value. A twenty-second re-audit
found the same branch-switch disease in the one
segment the else fix had not covered: an ``elif``
segment carries its condition command in itself
and was stamped with the previous branch's state,
so a terminator or unseen text AS an elif
condition escaped both models — the elif segment
is now judged in the state it executes in, in the
segmenter and the termination replay alike; and
the colour scan reads a literal join() call as
the literal it assembles, so concatenation around
a join result combines with it like any other
literal. A twenty-third re-audit closed the
colour family outright and found the branch-
switch disease's last nesting slot: a nested
``if`` riding the ``then``/``do`` segment opened
no frame at all, so its inner elif/else slots
were judged against the outer frame — the riding
opener now opens its frame in the segmenter and
in the termination replay, and the dead-branch
mirror (a riding chain inside a branch that
never runs, wrongly refused) is fixed by the
same change. A twenty-fourth re-audit granted
the unseen-text and colour families closed and
found termination's last general gap: branch
COVER — a chain whose every reachable branch
terminates and which cannot fall through ends
every path, but each terminator was individually
unproven and the branches were never joined. A
branch-cover pass now joins them at ``fi``
(direct terminators, terminating helpers called
in the branches, nested covering chains, and
bodies that cover internally all compose); and
the segmenter's bare-terminator latch honours
the unproven stamp on case arms, as the model
always stated.

Self-protection, the second layer (`beerus/bootmedium.py`):
inventory now marks a disk as the boot device when any of its
partitions carries the image's baked-in volume label — labels
are read from `lsblk` with nothing mounted, which closes the
Phase 1 residual for the live key's own unmounted EFI
partition — or sits at a live-boot medium mountpoint
(`/run/live/medium` and friends). The protection arrives
through the existing `is_boot` flag, so the engine's and
gates' refusals apply unchanged; marking is one-directional, so
a cloned key carrying the label is protected too.

Honest residuals, by design: **no image has been built from
this definition yet** — live-build does not run on the
development VM (filesystem/chroot limits), so the first build,
the first boot, and every hardware behavior above are Phase 5
validation, and the docs say so wherever they describe them.
The label layer trusts the label: a non-key drive deliberately
labelled `BEERUS_KEY` would be over-protected (refused), never
under-protected — the safe direction, and the stated rule.
Booting the image's contents through an exotic path that
neither mounts a medium nor presents a labelled filesystem
(a third-party multiboot extractor) is outside the supported
flash procedure, which is a raw write of the built image; the
mount-based first layer still applies wherever a medium mount
exists. The Phase 4 boot UI is the remaining consumer: it
plugs into `beerus.live`'s flows rather than replacing them.

## Phase 4 — The boot-environment UI

- Futuristic animated interface (PySide6 family), premium and
  calm — it runs in front of clients on decommissioning jobs,
  and it looks like it belongs there.
- **The consent gates at full prominence:** model, serial,
  method, and scope presented as the main event of the flow, not
  fine print — every control functional, no decorative buttons.
- Plain language throughout; every failure state a designed
  screen with one clear next action.
- The portable app (Phase 2.5) is the first shipped UI over
  this engine; the boot UI reuses its gate presentation.

**Status: COMPLETE — boot UI shipped in v0.6.0 (2026-10-20).**

As built:

- **The UI over `beerus.live`'s flows** (`beerus.bootui`):
  welcome (operator name, the memory-only documents warning),
  drive list (eligibility reasons on the greyed rows; the
  Start button can never light for an ineligible row), the
  identity, scope, and final-phrase gates in the portable's
  presentation and the console's words, the wipe on a worker
  thread with the engine's own progress (a percentage only
  when an event carries one; a firmware erase runs a busy bar
  and no invented number), and the console's exact result and
  document wording, including the NOT-sanitized states. Every
  refusal lands on a designed message page. A ~150 ms fade
  marks page changes — visual only, never blocking. The
  window refuses to close while a wipe runs.
  `beerus.live.build_live_engine` remains the only arming
  site: the UI contains no arming code, constructs no
  executor, and shows the builder's refusal in a refusal
  window (exit 2); a machine with no graphical session at all
  exits 76.
- **The fallback design, and how it stays honest:** the
  launcher (`beerus-live`) tries the graphical session first
  and falls back to the text console inside the same service.
  The honesty mechanism is placement: the *launcher* states
  the fallback (two plain lines before the console starts);
  the console itself states nothing about graphics, so nothing
  it says can be wrong on a machine where graphics started
  fine; and the UI's welcome line "Graphical session" is true
  by construction — the page can only render inside a running
  graphical session. Exit codes are honored exactly: 0 clean,
  2 a final designed refusal, anything else the fallback.
- **Beerus's own palette:** by the owner's namesake exception
  for this tool (2026-10-20 — purple for Beerus "because it's
  appropriate"), the boot UI is deep violet-black, Beerus
  violet, and destruction gold — not the fleet green/black —
  with the erase action in true danger red. Every text
  pairing is computed and pinned ≥ 4.5:1, and the constants
  are literal-pinned in the palette tests. The portable app
  kept the fleet palette at this phase; it joined this same
  identity in v0.6.1, pinned to agree with the boot UI's
  theme constant for constant.
- **Definition changes:** graphical-session packages in the
  package list (X core, framebuffer/VESA, libinput, `xinit`,
  PySide6 Qt, DejaVu Sans — the console needs none of them);
  the new `beerus-ui` client; the build hook proves the staged
  boot UI and the client; staging requires the boot UI
  package; and `check_definition.py` verifies the design
  semantically (startx client, status capture, both honored
  exit codes, fallback exec last, hook proof), with mutation
  tests for each failure shape.
- **Mode disposition, stated plainly:** the Phase 4 sketch
  mentioned surfacing the file/folder and free-space modes in
  the boot UI. The shipped boot UI presents the boot line's
  whole-drive flow — the flow Phase 3 built and the portable's
  gate presentation mirrors. **Decision 2026-10-21 (B-10) —
  CLOSED; disposition amended to "decided: portable only."**
  Reason: a destruction tool's boot form does one thing with
  total clarity; file wiping's shipped home is the portable.
- **Tests:** 43 new in the build (boot-UI theme, boot-UI GUI,
  boot-UI session + launcher, image-definition), with the
  audit and re-audit rounds adding their regression batteries
  on top; at the final gate — merged onto the v0.5.1 kit-hunt
  tree — the suite stands at 756 passed (1 skipped: a Windows
  junction case, by design off Windows). All against synthetic
  targets; the real executor stays two-factor disarmed
  everywhere in them.
- **Renamed in this phase (owner direction, 2026-10-20):**
  the bootable form's display name, "Destruct Key Pro" →
  **Live Boot**, across the boot UI, the console, the image
  definition's display strings, and present-tense docs.
  Functional identity did not move: the `BEERUS_KEY` volume
  label, the `beerus-key` hostname, the live-image marker, and
  the unit/file names are the key's self-protection, not its
  display name. Earlier phases' narrative keeps the name of
  its time.

## Phase 5 — Hardware image build + field validation

- **Built on real hardware:** live-image builds don't run on a
  dev VM (the filesystem and chroot limits are documented across
  the fleet) — the image is assembled on hardware, from the
  Phase 3 definition.
- **Sacrificial-drive validation:** test wipes on throwaway
  drives of each type (SATA SSD, NVMe, spinning HDD) —
  destruction, verification pass, and certificate checked end to
  end against the announced scope.
- **Signed release images:** the shippable Live Boot image
  is signed and checksummed, so a key in the field can be
  verified as a genuine Sentinel build.
- **Graphical boot on real hardware (Phase 4 carry-forward):**
  the graphical session starting on a real machine, the drive
  list rendering, and a full job run through the boot UI.
- **The fallback, triggered for real:** boot a machine whose
  graphics cannot start and confirm the launcher's notice and
  the text console serve the full flow.
- **Package truth at first build:** Qt/PySide6 package
  availability in the bookworm archive, confirmed when the
  image is first assembled.

**Status: PLANNED.**

## Later / backlog

- Batch mode for multi-drive decommissioning benches (one
  certificate per drive, one job report per bench run) — only
  after the single-drive flow is field-proven.
- A certificate archive format for Sentinel's job records —
  **CLOSED 2026-10-21 (B-11).** Reason: certificates are
  HMAC-authentic PASS documents as issued; a second signing
  story doubles the trust surface for a format no client has
  asked for.

## Standing rules

- **The Hellhound standard.** Warnings unmissable; consent
  deliberate and informed, given before anything runs; announced
  scope equals actual scope; no silent actions, ever. Beerus is
  judged on warning quality and consent design exactly the way
  Hellhound is.
- **Authorized drives only.** Beerus wipes drives the operator
  owns or is authorized to decommission — client hardware under a
  Sentinel service engagement, or the operator's own.
- **The boot key is never a target.** No phase, no setting, no
  override changes this.
- **Honest verification.** A wipe that can't be verified is
  reported as unverified — on screen and on the certificate.
- **Plain language.** Every user-visible word is written for the
  people in the room — clients watch this tool run. No raw OS
  errors, ever.
- **Audit everything.** Build → audit → fix → re-audit, per
  Sentinel's standing rule across the fleet.
- Brand: green and black. No purple anywhere.
