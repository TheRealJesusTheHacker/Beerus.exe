# Changelog

All notable changes to BEERUS.EXE are recorded here.

## [0.5.0] — 2026-10-20

Phase 3 — the bootable image definition.

- **The Destruct Key Pro image, defined in-repo (`image/`):** a
  Debian live-system definition — package list, files baked into
  the image, build hooks, and `build.sh` (stage → configure →
  build → checksum) — so the key is reproducible from source.
  The step-by-step build guide, including the first-boot
  verification checklist, is `docs/IMAGE-BUILD.md`. No image has
  been built from this definition yet: builds run on a real
  build machine, and the first build plus sacrificial-drive
  field validation are Phase 5 hardware work. Nothing here
  claims otherwise, and no release is cut for this phase — a
  release would imply a downloadable image that does not exist.
- **Boot straight into the tool:** the image starts a boot
  service (`beerus-live`) that owns the first console and runs
  the new boot console — the host system never starts, no host
  drive is mounted or touched before the operator chooses a
  target, and the environment needs no network.
- **The boot console (`beerus.live`, `python3 -m beerus`):** a
  terminal session over the same `GateAuthority`, `WipeEngine`,
  `WipeVerifier`, and certificate code the portable app uses —
  no second gate, no forked flow. Drive list with plain
  eligibility reasons, typed-serial confirmation, explicit
  scope acknowledgment, the exact final phrase, live progress,
  the verification verdict, and the certificate or failure
  report. Every state is designed: no eligible drives, a refused
  selection, a cancelled gate, walking away mid-flow, an
  interrupted wipe (reported as NOT sanitized, in those words).
  Job documents go to memory-backed storage inside the live
  system — never the key — and the console says at the start
  and after every document that they are gone at shutdown
  unless copied off.
- **Arming, exactly where it belongs:** the boot console is the
  one place the real-drive executor is armed, and only after
  two proofs — the image's build-time marker
  (`/etc/beerus/live-image.json`, staged at build time by
  `image/stage_image.py` from the package's own constants) is
  present and valid in the running system, and the session is
  root. The proofs are enforced inside the arming function
  itself (`build_live_engine` raises a plain-language refusal
  without them — there is no way to call it "just to build the
  engine"), and the marker must name this image's own volume
  label or it is not a marker for a key this code arms beside.
  Any other machine — a developer's, a test runner — gets the
  refusal and exit code 2 before an engine exists. Importing
  the package arms nothing.
- **Image-level self-protection (`beerus.bootmedium`):** the
  second, independent layer behind the mount-tree exclusion,
  closing the blind spot recorded since Phase 1 (an unmounted
  boot partition is invisible to mount-based detection — the
  live key's own EFI partition is the case). Inventory now also
  treats a disk as the boot device when any of its partitions
  carries the image's baked-in volume label (`BEERUS_KEY`,
  readable via `lsblk` with nothing mounted) or sits at a
  live-boot medium mountpoint. Marking is one-directional: a
  cloned key carrying the label is protected too — Beerus
  over-protects keys, never under-protects one. The engine's
  and gates' existing boot refusals apply unchanged, because
  the protection arrives through the same `is_boot` flag.
- **Definition checker (`image/check_definition.py`):** the
  definition validates without a build machine — required files,
  the build's volume label matching the protection code's
  label, the engine's tools present in the package list, the
  service starting the launcher, the launcher starting the
  console. The test suite runs it, along with the staging logic
  (fresh package copy, generated marker verified against the
  live marker reader, refusal to stage an incomplete tree).
- **Independent audit cycle:** the phase was audited before
  shipping (3 HIGH, 4 MEDIUM, 7 LOW findings — all fixed and
  re-audited, each with a permanent regression test). The
  headline fixes: the arming function's proofs are now enforced
  inside the function itself rather than trusted to its caller;
  the live verifier is wired with the firmware-status runner
  (without it no NVMe wipe on the key could ever be certified —
  the portable launcher's wiring, now matched); the drive menu
  rejects `0`, negative, and non-numeric input outright instead
  of letting Python's negative indexing select a drive; the
  console's failure seams (document save, executor, drive
  listing) answer unexpected OS and runtime errors in plain
  language — a wipe interrupted by one is reported NOT
  sanitized, never a traceback; drive identity strings are
  cleaned of control characters at parse time, so a crafted
  model or serial cannot forge console output on the terminal;
  the marker must now name this image's own volume label, and
  its documents directory is constrained to the live system's
  memory, never the key; and the definition checker parses real
  assignments instead of substrings, allowlists the image's
  packages, and checks executable bits. A final re-audit of the
  checker itself found indirect spellings — wrapped, prefixed,
  compounded, or merely stored commands — that could still
  satisfy it; its command analysis was rebuilt (each command in
  a script is segmented and identified by the program that
  actually runs; flags, exports, and the exec'd program are
  matched by token, never by substring), and every bypass from
  that round is now rejected. A second re-audit of the rebuilt
  checker pushed further — heavier wrappers, a nested shell,
  bystander commands carrying the words as plain arguments, and
  a PYTHONPATH that merely mentioned the staged directory — so
  the hook check became semantic (the staged files count only
  where a command existence-tests them by their full paths;
  getty counts only where systemctl masks it) and the path
  check matches whole components. Every bypass from every
  round is now rejected and pinned by a regression test. A
  third re-audit pushed further still — tests of the wrong
  location, a look-alike unit name, proofs parked in a dead
  branch or discarded with ``|| true``, and a launcher value
  shadowed, scoped away, or attached to the wrong command — so
  the hook proofs are anchored to the exact staged paths and
  the exact unit, a proof's failure must actually stop the
  hook, dead code counts for nothing, and the launcher is
  judged as ordered state in its own scope. A fourth re-audit
  pushed into the corners of that model — constant-false
  conditions spelled with negation or literal arithmetic, a
  ``set -e`` that arrived late or was switched back off, an
  exit offered from inside a subshell, and proofs parked in a
  function nobody calls — and the checker now evaluates
  constant conditions, tracks errexit in order, refuses
  subshell exits as enforcement, and counts a function body
  only when the function is actually called. A fifth re-audit
  probed the remaining corners — arithmetic conditions,
  ``case`` arms, functions invoked only from a subshell or
  only after the exec — and the checker now evaluates literal
  arithmetic conditions (and trusts no condition it cannot
  evaluate), tracks ``case`` arm reachability, and credits a
  function body only at a live top-level call site. A sixth
  re-audit found the same constant conditions guarding
  commands through ``&&``/``||`` instead of ``if`` — the
  checker now judges a guarded command by its guard's
  evaluated value, in every file it checks. A seventh re-audit
  found the chain form of the same family — a skipped
  command's own value must not resurrect the chain — and the
  checker now propagates chain status the way the shell does:
  a command that never ran changes nothing. An eighth re-audit
  respelled the chain across a line break and into a pipeline —
  the checker now carries chains across operator-continued
  lines, and treats pipeline components as the subshell scope
  they are for enforcement. A ninth re-audit respelled the
  exemption itself: a test credited only through ``set -e``
  stays unenforced when it is a non-final ``&&`` member,
  backgrounded, or negated (including as the last component
  of a negated pipeline) — the checker now withholds the
  errexit credit in exactly those positions, credits a chain
  that genuinely reaches an ``|| exit``, and refuses a
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
- **Tests:** 322 new tests (boot-medium predicates + marker,
  inventory integration incl. the unmounted-label case and
  hostile field types, scripted console sessions end to end
  against synthetic file targets, refusal gating inside the
  arming function itself, NVMe verification through the live
  wiring, staging and definition checks incl. the audit
  regressions) — all synthetic, as always. The full suite is
  green; the real-drive executor remains two-factor disarmed
  in every test and CI context.

## [0.4.0] — 2026-10-20

Beerus Portable — the portable app edition, and file-level wiping.

- **Beerus Portable (new form):** a no-install Windows app
  (`beerus_portable`) running the same engine, gates,
  verification, and certificates on an already-booted machine.
  Windows inventory is read through read-only PowerShell
  queries into the engine's own drive records. Live-system
  refusal rules: the drive Windows is running from and the
  drive the app itself is running from are marked as boot
  devices, so the engine and gates refuse them at every layer,
  and unidentifiable drives are refused by the engine's rule.
  The shipped whole-drive executor stays disarmed by both of
  its factors — arming remains a deployment act, and the app
  contains no arming path.
- **Files & folders mode (`beerus.filewipe`):** selected files
  are overwritten in full (the engine's pass plan), flushed,
  and read back and verified BEFORE their entries are removed;
  entries go last, by rename-to-random + truncate + delete.
  Scope is fixed and counted before consent; links are never
  followed; protected OS locations are refused plainly;
  per-item outcomes are reported with reasons (locked files
  are reported, never forced).
- **Free-space mode:** a volume's free space is filled through
  the overwrite pass plan with a disclosed safety margin, the
  filler read back (sampled) and removed; existing files are
  untouched.
- **Consent + reporting for file modes:** an HMAC-bound gate
  authority (scope-hash binding, exact typed phrase,
  single-use expiring tokens) and signed wipe reports with
  the certificate module's anti-forgery posture — plainly
  reports, never certificates of destruction.
- **Honesty disclosures:** the SSD wear-leveling/TRIM limit
  and the other-copies limit are stated at mode selection, in
  the scope statement, and in the report.
- **Release:** tag-gated Windows workflow builds
  `BeerusPortable.zip` (one-folder layout, no installer) with
  the full suite offscreen, a staged-app launch smoke test,
  and SHA-256 checksums. 331 tests, all against synthetic
  targets.
- **Windows-verified:** cutting this release ran the full
  suite on a Windows runner for the first time, and it
  exposed POSIX-only spots in the engine — device-path
  validation keyed to the host's path rules, unguarded
  `O_NOFOLLOW`, positioned reads, text-mode descriptor
  writes that would have corrupted overwrite passes, and
  link refusals delegated to a flag Windows does not have.
  All fixed (explicit `lstat` link refusals, `posixpath`
  validation for `/dev` paths, `O_BINARY` data surfaces, a
  `pread` fallback); the suite is green on Windows and
  Linux alike.
- **Independent audit cycle:** the portable surface was
  audited before release (2 HIGH, 6 MEDIUM, 3 LOW findings,
  all fixed and re-audited, each with a permanent regression
  test): protected-folder checks now run on fully resolved
  paths; files with more than one name (hardlinks) are
  refused rather than silently destroying data at unlisted
  names; scoped files are bound to their stat identity and
  re-verified on the opened descriptor; aliased paths count
  once; file-gate sessions expire like drive-gate sessions;
  a free-space fill that falls short of the confirmed target
  is reported as incomplete, never complete; the filler
  folder is refused if it pre-exists as a link or with
  foreign contents; report/record encodings are
  length-prefixed against repartitioning; wrong-kind tokens
  and scopes refuse cleanly. A re-audit of the fixes closed
  them out and added four smaller findings of its own — a
  link entry swapped after confirmation, token expiry bound
  to mint time instead of the session deadline, two result
  checks missing type guards, free-space cleanup reporting
  that misdescribed leftovers — all fixed with regression
  tests as well. One bound is documented, not hidden: the
  scope binds a file's identity record, so a rewrite that
  also restores the exact modification stamp is not
  detectable — the same identity standard the drive gates
  use.

## [0.3.0] — 2026-10-20

Phase 2 — post-wipe verification + certificate of destruction.

- **Verification pass:** after a wipe, Beerus reads the drive
  back and renders an honest verdict — pass, fail, or not
  verified, with no softening. Overwrite (Clear) wipes are read
  back across the full surface and checked byte-for-byte against
  the final pass. Firmware (Purge) wipes are checked against the
  drive's own status where one exists (NVMe sanitize status)
  plus a sampled read-back that reports exactly what was
  sampled — bytes and regions — never implying full coverage.
  A check that cannot be performed is reported as NOT VERIFIED,
  never rounded up to a pass.
- **Signed results:** verification results are HMAC-signed when
  they are minted, bound to the drive, the method that actually
  ran, and exactly what was checked. A hand-built or edited
  result does not verify, and certificates refuse it.
- **Certificate of destruction:** a printable HTML certificate
  per verified wipe — drive model and serial, capacity, method
  and NIST 800-88 category, start and finish times, the
  verification result with its coverage, operator, and a record
  ID. **A failed or unverifiable wipe blocks a clean
  certificate** — the generator issues a plainly-marked failure
  report instead (what was attempted, what verification found,
  what to do next). Certificates are issued in the same run as
  the wipe and are written only to a caller-chosen local
  directory; existing records are never overwritten.
- **Engine wiring:** when a verifier is wired in, the engine
  verifies after the wipe completes and attaches the result to
  the wipe record; a verification error attaches a NOT VERIFIED
  result instead of failing the run.
- 257 tests, all against synthetic targets. Independent audit
  cycle completed: findings F1–F14 (including the record-binding
  hardening above) fixed and re-audited clean.

## [0.2.0] — 2026-10-20

Phase 1 — drive inventory + wipe engine core, with the consent
gates designed in from line one.

- **Drive inventory:** every attached drive enumerated with
  model, serial number, size, interface, and WWN. A drive
  Beerus cannot identify — missing or placeholder model/serial —
  is a drive Beerus will not wipe, enforced at every layer.
- **Boot-device self-exclusion:** the drive Beerus booted from
  is marked from the mount tree and refused by the gates and
  again by the engine, by identity — device re-lettering or
  renaming changes nothing.
- **Consent gates:** typed-serial identity confirmation, full
  scope acknowledgment, and an exact `ERASE <serial>` final
  phrase. Completing the gates mints a confirmation token that
  is HMAC-bound to the drive and the exact wipe plan, expires,
  and can be used once.
- **Method selection (NIST 800-88):** NVMe Sanitize and ATA
  Secure Erase for flash (Purge); four-pass overwrite for
  spinning disks (Clear). USB-attached and unclassifiable drives
  are refused in plain language in this version.
- **Engine:** re-enumerates and re-verifies the target before
  the token is consumed; the executor cannot be reached before
  every check passes. Progress is reported from real counters
  only — firmware erases report the method that actually ran,
  never an invented percentage.
- **Executor seam:** all destruction flows through one seam. The
  file-backed executor only ever writes to regular files; the
  real-drive executor ships disarmed (two-factor arming) until
  the boot-image entry point arms it in a later phase.
- 188 tests, all against synthetic targets — the suite cannot
  touch a real drive. Safety audit cycle completed: independent
  audit findings F1–F12 fixed and re-audited.

## [0.1.0] — 2026-10-20

- Project kickoff: repository scaffold, build roadmap, package
  stub, proprietary license.
