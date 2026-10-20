# BEERUS.EXE — Build Roadmap

The destruction chart. Phases run in order, and the order is the
point: **the wipe engine and its consent gates are designed
first** — destruction that can't prove what it's about to destroy
doesn't ship, and the bootable image comes only after the engine
has earned it.

**The one job:** boot any machine from the Destruct Key Pro, pick
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

**Status: PLANNED.**

## Phase 4 — The boot-environment UI

- Futuristic animated interface (PySide6 family), green on
  black, premium and calm — it runs in front of clients on
  decommissioning jobs, and it looks like it belongs there.
- **The consent gates at full prominence:** model, serial,
  method, and scope presented as the main event of the flow, not
  fine print — every control functional, no decorative buttons.
- Plain language throughout; every failure state a designed
  screen with one clear next action.

**Status: PLANNED.**

## Phase 5 — Hardware image build + field validation

- **Built on real hardware:** live-image builds don't run on a
  dev VM (the filesystem and chroot limits are documented across
  the fleet) — the image is assembled on hardware, from the
  Phase 3 definition.
- **Sacrificial-drive validation:** test wipes on throwaway
  drives of each type (SATA SSD, NVMe, spinning HDD) —
  destruction, verification pass, and certificate checked end to
  end against the announced scope.
- **Signed release images:** the shippable Destruct Key Pro image
  is signed and checksummed, so a key in the field can be
  verified as a genuine Sentinel build.

**Status: PLANNED.**

## Later / backlog

- Batch mode for multi-drive decommissioning benches (one
  certificate per drive, one job report per bench run) — only
  after the single-drive flow is field-proven.
- A certificate archive format for Sentinel's job records.

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
