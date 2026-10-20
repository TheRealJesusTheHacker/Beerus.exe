# BEERUS.EXE

**Sentinel Safeguard LLC — secure drive destruction, on a key.**

[![Version](https://img.shields.io/badge/version-v0.2.0-2ee06f?style=flat-square)](https://github.com/TheRealJesusTheHacker/Beerus.exe/blob/main/beerus/__init__.py)
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

And every wipe produces a **certificate of destruction**: the
paper trail that turns end-of-life hardware into a documented,
billable Sentinel service.

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
   and gives a plain pass or fail.
7. **Certify** — a printable certificate of destruction is
   generated for the job.

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
- **Verification** — after the wipe, a verification pass samples
  the drive and reports an honest pass or fail. A fail is reported
  as a fail — on screen and on the certificate.

## Certificate of destruction — the Pro feature

Every completed wipe generates a printable certificate of
destruction recording:

- Drive **model and serial number**
- **Method** used (per NIST 800-88)
- **Date and time**
- **Verification result**
- **Operator**

For Sentinel's client decommissioning work, the certificate *is*
the deliverable: proof, per drive, that the data is gone.
Certificates are business records — they are generated on the job
and never live in this repository.

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

**v0.2.0 — Phase 1 complete: the wipe engine and its consent
gates are built.** The `beerus` package now contains the drive
inventory (model, serial, size, interface, WWN for every
attached drive), NIST 800-88 method selection, the consent-gate
framework (typed-serial confirmation, full scope acknowledgment,
an exact final confirmation phrase, single-use expiring tokens),
and the engine that refuses to run without all of it — and
refuses the boot drive and any drive it cannot identify,
always. The real-drive executor ships disarmed; arming belongs
to the boot-image entry point in a later phase, and destruction
on real hardware is validated in Phase 5. 188 tests, all against
synthetic targets. Verification and certificates are Phase 2;
the bootable image is Phase 3 — see the [roadmap](docs/ROADMAP.md).

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
