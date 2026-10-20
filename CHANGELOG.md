# Changelog

All notable changes to BEERUS.EXE are recorded here.

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
