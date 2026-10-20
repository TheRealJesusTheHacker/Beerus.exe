# Building the Live Boot image

The step-by-step for turning this repository's image definition
(`image/`) into a bootable Live Boot USB key.

> **Status, stated plainly:** this definition has been checked
> for completeness and consistency by the test suite, but the
> image itself has **not been built yet**. Live-image builds run
> on a real build machine — they do not run on a small
> development VM (the filesystem cannot take ownership of files
> the way the image assembly requires, and chroot mounts are
> blocked there). The first real build, and the validation of
> the finished key on sacrificial drives, is **Phase 5** work.
> Nothing in this repository claims a key built from this
> definition has booted anywhere yet.

## What you end up with

A USB key that boots any machine straight into Beerus: the
drive list, the consent gates, the wipe, the verification, and
the certificate of destruction — with the machine's own system
never started and no network needed at any point. The boot
service's launcher tries the graphical session first (an X
session running the boot UI); if a graphical session cannot
start on the machine at hand, it says so in two plain lines
and continues in the text console inside the same service —
the same flow in either presentation. The graphical packages
(X server with the generic framebuffer/VESA drivers, libinput,
`xinit`, the PySide6 Qt libraries, DejaVu Sans) are in the
package list for the boot UI alone; the text console needs
none of them, and the fallback is always available. Job
documents are written to memory inside the live system (never
to the key) and Beerus reminds the operator to copy each one
off before shutdown.

## The build machine

- A machine (or full virtual machine with real disk ownership —
  not a container) running **Debian 12 (bookworm)**, with:
  - root access (the build runs as root),
  - about **15 GB** of free disk space for the build tree and
    the finished image,
  - network access **for the build only** — the builder
    downloads the image's packages from the Debian mirrors. The
    finished key needs no network, ever.
- The `live-build` package from Debian.
- A USB key to write the image to (its size should match the
  built image; 4 GB is ample for this definition).

## Step 1 — Get the source

Clone this repository to the build machine and move into it:

```
git clone https://github.com/TheRealJesusTheHacker/Beerus.exe.git
cd Beerus.exe
```

## Step 2 — Install the builder

```
sudo apt-get update
sudo apt-get install live-build python3
```

## Step 3 — Check the definition before building

The definition can be validated without building anything:

```
python3 image/check_definition.py
python3 -m pytest -q
```

The checker confirms the definition is complete and internally
consistent — most importantly that the volume label the build
sets is the same label the protection code looks for. The test
suite re-checks the same ground. If either fails, stop: a build
from an inconsistent definition is not a Live Boot key.

## Step 4 — Build

```
cd image
sudo ./build.sh
```

The script does three things, in order:

1. **Stages** (`stage_image.py`): copies the `beerus` package
   from your checkout into the image tree (fresh from source,
   every build) and generates the image marker —
   `/etc/beerus/live-image.json` inside the image, carrying the
   Beerus version, the volume label, and the job-document
   location. The running console refuses to arm the drive wiper
   unless this marker is present and valid.
2. **Configures** (`lb config`): a Debian bookworm live system,
   hybrid ISO image, volume label **`BEERUS_KEY`** — the exact
   label `beerus.bootmedium` treats as "this disk is a key,
   never a target" — booting with the hostname `beerus-key`.
   The checked-in definition under `image/config/` supplies the
   package list, the files baked into the image (the boot
   service, the console launcher, the hostname), and a build
   hook that proves the staged package and marker are in place
   (and fails the build if they are not), masks the ordinary
   first-console login, and keeps network services off.
3. **Builds** (`lb build`) and writes a SHA-256 checksum beside
   the image: `live-image-amd64.hybrid.iso` (the exact name can
   vary slightly with the builder version) plus
   `<image>.sha256`.

## Step 5 — Write the image to the USB key

A raw write, so the image — including its volume label —
arrives exactly as built.

1. Insert the USB key and find its device name:

   ```
   lsblk
   ```

2. **Check the device name twice.** The next command erases
   everything on the device you name. It must be the USB key
   (for example `/dev/sdb`) — the whole disk, not a partition,
   and never the build machine's own drive.
3. Write the image:

   ```
   sudo dd if=live-image-amd64.hybrid.iso of=/dev/sdX bs=4M status=progress conv=fsync
   ```

   (Replace `live-image-amd64.hybrid.iso` with the built file's
   actual name and `/dev/sdX` with the key's device.)
4. Verify the key carries the label the protection looks for:

   ```
   lsblk --output NAME,LABEL /dev/sdX
   ```

   One of its filesystems must report the label `BEERUS_KEY`.

## Step 6 — Verify the key (first boot)

Boot a test machine from the key and work through this
checklist. **Every line must hold before the key is used on a
job.**

1. The machine boots from the key straight into Beerus — no
   login prompt on the first console, no host system starting.
   On a machine with working graphics, the graphical session
   starts: the boot UI opens on the welcome page. The drive
   list renders the machine's drives, with the key itself
   greyed and its reason shown (see line 2, which applies in
   either presentation).
2. The console lists the machine's drives. **The key itself
   appears marked "cannot be wiped," with the reason that it
   is the drive Beerus booted from.**
3. **The unmounted-partition proof:** switch to a spare console
   (Ctrl+Alt+F2, and log in as the live user `user`, password
   `live` — the Debian live system's standard default; the
   Beerus console itself asks for no password), run `lsblk`, and
   confirm that no partition of the key is mounted at `/`,
   `/boot`, or `/efi` — then confirm at the Beerus console that
   the key is *still* marked as the boot drive. This is the
   image-level protection working: the key is recognised by its
   baked-in label, not by mounts.
4. The marker is in place: `cat /etc/beerus/live-image.json`
   shows the Beerus live image marker, with the version you
   built.
5. **A full sacrificial job:** attach a throwaway drive, run the
   whole flow — choose it, type its serial, acknowledge the
   scope, type the final phrase — and let the wipe and
   verification finish. The certificate (or, if you arranged a
   failing setup, the failure report) is written under
   `/var/log/beerus/certificates`.
6. **Copy the document off, then reboot.** Before shutdown,
   copy the job document to somewhere that survives (another
   USB device, or `scp` from the spare console if the machine
   has a network cable in for the test). After the reboot,
   confirm the document is *gone* from
   `/var/log/beerus/certificates` — documents live in memory
   only, by design.
7. **No network:** unplug any network cable and repeat the
   sacrificial job. Nothing about the flow may need the network.
8. **The fallback, for real:** boot a machine with no working
   graphics or display (or one where the graphical session
   cannot start) and confirm the launcher prints its two notice
   lines — "The graphical session could not start on this
   machine." and the continuation line — and the text console
   then serves the full flow: drive list, gates, wipe,
   verification, document. Documents are still memory-only.

## If something is wrong

| Symptom | First checks |
|---|---|
| `lb` not found | Step 2 — install `live-build` from Debian. |
| Build stops in the Beerus hook | Staging did not run or did not complete — build through `image/build.sh` from a full checkout; do not run `lb` by hand. |
| Key boots to a login prompt, no console | On a spare console: `systemctl status beerus-live`. The service is enabled by the `multi-user.target.wants` link in the definition — if the link is missing, rebuild from a clean checkout after `python3 image/check_definition.py` passes. |
| Console starts but refuses to arm | The console prints the reason. Off the key it always refuses (that is the design). On the key, check the marker (Step 6, line 4) and that the console is the root session the service starts. |
| The key is offered as a wipe target | **Stop. Do not use the key.** Check the label (Step 5, line 4): a key without the `BEERUS_KEY` label was not written raw from this image. Re-write it with Step 5 exactly, and report the failure before any job use. |

## Phase 5 owns the rest

The first build from this definition, the checklist above on
real hardware, sacrificial-drive validation per drive class
(SATA hard drive, SATA SSD, NVMe — destruction, verification,
and certificate checked end to end), and signed release images
are Phase 5. Until Phase 5 records them done, this guide is a
*definition and a procedure*, not a field report.

Phase 5 also verifies the operational details this definition
asserts but a paper definition cannot prove: the spare-console
login works with the Debian live default (`user` / `live`), the
document copy-off paths (a mounted USB device, and `scp` — the
client is in the package list) work from the live system, and
the boot log shows the console service starting once, owning
the first console, and leaving a designed refusal (exit 2) on
the screen instead of restart-looping if one ever occurs on a
field key.
