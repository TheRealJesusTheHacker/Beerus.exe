# BEERUS.EXE

**Sentinel Safeguard LLC — secure drive destruction, on a key.**

[![Version](https://img.shields.io/badge/version-v0.3.0-2ee06f?style=flat-square)](https://github.com/TheRealJesusTheHacker/Beerus.exe/blob/main/beerus/__init__.py)
[![Platform](https://img.shields.io/badge/platform-Bootable%20USB-0078D6?style=flat-square)](https://github.com/TheRealJesusTheHacker/Beerus.exe)
[![Python](https://img.shields.io/badge/Python-3-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Proprietary-b03a2e?style=flat-square)](https://github.com/TheRealJesusTheHacker/Beerus.exe/blob/main/LICENSE)

*What Lazarus raises, Beerus ends.*

---

## Overview

In the stories, Beerus is the God of Destruction — the one being
whose job is endings, done completely and without reversal.

BEERUS.EXE is Sentinel Safeguard's drive-destruction tool. This
repository is flashed to a USB drive to create the **Destruct Key
Pro**: a bootable sanitization key. Plug it into any machine, boot
from it, pick a drive, and the data on that drive is destroyed to
the **NIST 800-88** media sanitization standard — the standard
governments and enterprises use when "deleted" has to actually
mean gone.

**Portable form:** Beerus's portable edition is the bootable USB image itself — the Destruct Key Pro USB is its portable form, with no separate portable download or installer.

And every wipe produces a document: a **certificate of
destruction** when verification passes — the paper trail that
turns end-of-life hardware into a documented, billable Sentinel
service — or a plainly-marked failure report when it doesn't.
A wipe that can't be verified never earns the certificate.

## How it works

1. **Flash** — this repo's image is written to a USB drive. That
   USB is now a Destruct Key Pro.
2. **Boot** — the key boots any machine straight into Beerus. The
   machine's own system never starts; nothing on its drives is
   touched until you say so.
3. **Choose** — Beerus lists every drive it can see: model, serial
   number, size. You pick the target.
4. **Prove it** — the consent gates (below) make you confirm the
   exact drive, by model and serial, through deliberate steps.
   Destruction is never one careless click.
5. **Destroy** — the right NIST 800-88 method for the drive runs,
   and Beerus reports progress honestly while it works.
6. **Verify** — a post-wipe verification pass checks the result
   and gives a plain pass, fail, or not verified.
7. **Certify** — a printable certificate of destruction is
   generated when verification passes. When it doesn't, Beerus
   issues a failure report instead — what was attempted, what
   verification found, and what to do next.

## Destruction methods

Beerus follows **NIST 800-88** — the method is chosen by the drive,
not by habit:

- **SSD / NVMe** — the drive's own sanitize commands: ATA Secure
  Erase / Security Erase for SATA drives, NVMe Format and Sanitize
  for NVMe. These are the methods flash storage actually honors;
  overwriting an SSD leaves data behind in places the drive keeps
  to itself.
- **Spinning hard drives (HDD)** — multi-pass overwrite across the
  full drive.
- **Verification** — after the wipe, a verification pass reads
  the drive back: the full surface for overwrite wipes, a
  disclosed sample plus the drive's own status for firmware
  wipes. The verdict — pass, fail, or not verified — is reported
  exactly as found, on screen and on the job document.

## Certificate of destruction — the Pro feature

Every wipe that passes verification generates a printable
certificate of destruction recording:

- Drive **model and serial number**
- **Method** used (per NIST 800-88)
- **Date and time**
- **Verification result**
- **Operator**

For Sentinel's client decommissioning work, the certificate *is*
the deliverable: proof, per drive, that the data is gone.
Certificates are business records — they are generated on the job
and never live in this repository. A wipe that fails verification
— or can't be verified — gets a failure report instead of a
certificate, stating what was attempted, what verification
found, and what to do next. There is no way to print a clean
certificate for a drive that didn't earn one: the verification
result is signed when it is minted and bound to the drive and
the method that actually ran, and the certificate is issued in
the same run as the wipe.

## The consent gates

Destruction is permanent, so Beerus is built the way Hellhound
taught the medic kit to build: the warnings and the consent *are*
the product, designed in from the first line — not bolted on
later.

- **Model AND serial, up front.** The target drive's model and
  serial number are displayed prominently before anything runs.
  "The big one" is never an acceptable target description.
- **Deliberate, multi-step confirmation.** Reaching the point of
  no return takes several intentional steps. Nothing destructive
  is a single click, a default, or a timeout.
- **Scope announced before it happens.** What will be destroyed,
  by what method, and what *won't* be touched is stated in full
  before the wipe starts — and the actual wipe never exceeds the
  announced scope.
- **The boot key protects itself.** The Destruct Key Pro itself
  can **never** be a wipe target. Beerus excludes its own boot
  device from the target list, always.
- **No silent actions, ever.** Beerus does nothing to any drive
  without showing it. If it isn't on the screen, it isn't
  happening.

## The medic kit

BEERUS.EXE joins Sentinel Safeguard's medic-kit line:
**NitroCore** (system optimizer), **VectorZip** (archive tool),
**ARGUS.EXE** (antivirus), **CHARON.EXE** (driver updater), and
**LAZARUS.EXE** (backup, restore & recovery). Lazarus brings
drives back from the dead; Beerus is the other end of the
lifecycle — when a drive's life is over, Beerus makes the ending
permanent and documented. Each tool does one job at a premium
standard: plain language, no corners cut, nothing promised that
isn't delivered.

## Status

**v0.3.0 — Phase 2 complete: verification and certificates are
built.** On top of Phase 1 (drive inventory, NIST 800-88 method
selection, the consent-gate framework, and the engine that
refuses to run without all of it), the `beerus` package now
verifies every wipe it can: overwrite wipes are read back across
the full surface and checked byte-for-byte; firmware wipes are
checked against the drive's own status plus a sampled read-back
that discloses exactly what was sampled. The verdict — pass,
fail, or not verified — is signed when it is minted and bound
to the drive and the method that actually ran. A passing wipe
earns a printable certificate of destruction; a failed or
unverifiable one gets a plainly-marked failure report, and no
path through the library produces a clean certificate for a
drive that didn't earn it. 257 tests, all against synthetic
targets, with an independent audit cycle completed (findings
F1–F14 fixed and re-audited). The real-drive executor still
ships disarmed; arming belongs to the boot-image entry point,
and destruction on real hardware is validated in Phase 5. The
bootable image is Phase 3 — see the [roadmap](docs/ROADMAP.md).

One honest note on how this gets built: the bootable image is
assembled on real hardware in the final phase. The repo versions
everything that defines it — the wipe engine, the certificate
generator, the UI, and the image definition itself.

## License

Proprietary — see [LICENSE](LICENSE). © 2026 Sentinel Safeguard
LLC. All rights reserved.

---

*BEERUS.EXE is a Sentinel Safeguard LLC product. Sentinel is the
pretty face: cybersecurity, IT, and recovery services for the
Kansas City metro.*
