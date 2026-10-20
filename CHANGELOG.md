# Changelog

All notable changes to BEERUS.EXE are recorded here.

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
  and SHA-256 checksums. 309 tests, all against synthetic
  targets.

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
