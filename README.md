# BEERUS.EXE

**Sentinel Safeguard LLC — secure drive destruction, on a key.**

[![Version](https://img.shields.io/badge/version-v0.4.0-2ee06f?style=flat-square)](https://github.com/TheRealJesusTheHacker/Beerus.exe/blob/main/beerus/__init__.py)
[![Platform](https://img.shields.io/badge/platform-Windows%20app%20%2B%20Bootable%20USB-0078D6?style=flat-square)](https://github.com/TheRealJesusTheHacker/Beerus.exe)
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

And every wipe produces a document: a **certificate of
destruction** when verification passes — the paper trail that
turns end-of-life hardware into a documented, billable Sentinel
service — or a plainly-marked failure report when it doesn't.
A wipe that can't be verified never earns the certificate.

## Two forms: the Destruct Key Pro and Beerus Portable

Beerus comes in two forms, and they are the same tool — one
engine, the same consent gates, the same verification, the same
documents. No forked wipe logic exists anywhere.

- **The Destruct Key Pro (bootable live USB)** — the original
  form. Boot any machine from the key; the machine's own system
  never starts. This is the form for destroying the drive a
  machine runs from, and for bare-metal decommissioning work.
- **Beerus Portable (the Windows app)** — a no-install Windows
  app, carried on a stick and run on an already-booted machine:
  download `BeerusPortable.zip` from the release, unzip it, run
  `Beerus.exe`. Same engine underneath; the Windows edition of
  the drive inventory asks Windows (read-only) instead of
  `lsblk`. Because it runs on a live system, it plays by live
  rules: **it refuses, plainly and by design, the drive Windows
  is running from, the drive the app itself is running from
  (the stick), and any drive it cannot identify** — and the
  engine's own boot-device protection backs every one of those
  refusals at the gate and engine layers, not just on screen.

### What Beerus Portable wipes — three target modes

1. **Whole drive** — a non-system drive, destroyed through the
   full engine: NIST 800-88 method selection, the typed-serial
   gate, surface read-back verification, and the certificate
   of destruction (or the marked failure report).
2. **Files & folders** — the exact files and folders picked.
   Each file's **data** is overwritten in full, flushed to disk,
   and **read back and verified before its entry is removed**;
   the entry goes last (rename-to-random, truncate, delete).
   This is never mere deletion — a file whose overwrite or
   verification fails keeps its entry and is reported with its
   reason (a Windows-locked file is reported, never forced,
   never silently skipped). The scope is counted and listed in
   full before anything runs; links are never followed, so the
   wipe can never widen past what was confirmed. Each run
   issues a signed **wipe report** — plainly a report, not a
   drive certificate.
3. **Free space** — the unused space of a chosen volume, filled
   with overwrite passes and the filler removed, so files
   deleted earlier cannot be recovered. Existing files and the
   operating system are untouched, and a safety margin is
   always left empty.

Two honesty rules ride on the file modes, on screen and in the
report — designed text, not fine print. **On an SSD,** a
file-level overwrite cannot guarantee the physical cells that
held the data were rewritten (wear-leveling and TRIM sit
between the file system and the cells); the strong guarantee is
a whole-drive sanitize. **Other copies** of a file's data can
exist outside the picked files — shadow copies, temp files,
thumbnails, journals — and Beerus says so rather than imply
otherwise.

In the portable app, whole-drive destruction ships **disarmed**,
exactly as the engine prescribes: the real executor's two
factors (an armed construction plus its environment switch) are
never set by the app, so a confirmed whole-drive wipe is
refused before anything is sent to a drive; arming is a
deployment act performed by the owner, not something the app
does. The file and free-space engines write only what their
own HMAC-bound consent tokens authorize — the confirmed files,
and filler in one volume's free space.

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

**v0.4.0 — Beerus Portable: the app edition, with all three
target modes.** The portable Windows app (`beerus_portable`)
runs the same engine on a booted machine, with a Windows
inventory seam (read-only PowerShell queries translated into
the engine's own drive records) and the live-system refusal
rules: the Windows system drive and the app's own drive are
marked as boot devices, so the engine and gates refuse them at
every layer. On top of whole-drive mode, `beerus.filewipe`
adds file/folder wiping (overwrite + read-back verification
*before* entry removal, fixed scope, per-item signed reporting)
and free-space wiping, under an HMAC-bound consent authority
that mirrors the drive gates. The release workflow builds
`BeerusPortable.zip` on Windows — full test suite offscreen,
one-folder PyInstaller build, a launch smoke test of the
staged app, and SHA-256 checksums. 331 tests at this version,
all against synthetic targets; nothing in the suite or in CI
ever touches a real drive. The whole-drive executor still
ships disarmed (both factors) in every build; destruction on
real hardware is validated in Phase 5. The bootable image is
Phase 3 — see the [roadmap](docs/ROADMAP.md).

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
