# The Live Boot image — definition

This directory is the complete, versioned definition of the
Live Boot bootable image. The key is reproducible from
source: everything the image contains and does is decided here
or by `build.sh`, and nothing is assembled by hand.

**Building the image is hardware work.** Live-image builds do
not run on a small development VM (filesystem and chroot limits),
so the image is assembled on a real build machine by following
**[docs/IMAGE-BUILD.md](../docs/IMAGE-BUILD.md)** — host setup,
the build, writing the image to a USB key, and the verification
checklist. This directory defines; it does not contain a built
image, and no built image is committed to this repository.

## Layout

```
image/
  build.sh                  the build: stage, configure, build, checksum
  stage_image.py            stages the beerus package + image marker
  check_definition.py       consistency check for this definition
  config/
    package-lists/          what the image installs
    includes.chroot/        files baked into the image's filesystem
      etc/hostname
      etc/systemd/system/   the beerus-live boot-console service
      usr/local/bin/        the console launcher
    hooks/live/             checks + lockdown run inside the build
```

Two pieces of the definition are generated at build time, on
purpose, so they can never drift from the code they describe:

- `stage_image.py` copies the `beerus` package from this checkout
  into the image (at `/opt/beerus`) — a fresh copy from source at
  every build.
- The same step writes the image marker
  (`/etc/beerus/live-image.json` inside the image) from the
  package's own constants: the Beerus version, the volume label,
  and where job documents live.

Both generated pieces are ignored by git; the definition above is
what is reviewed and versioned.

## How the key protects itself

The image-level layer, backing the engine's own boot-device
exclusion (which reads the mount tree):

- **The label.** `build.sh` builds the image with the volume
  label `BEERUS_KEY` — the exact label `beerus.bootmedium`
  protects. Filesystem labels are readable without mounting
  anything, so the disk carrying that label is marked as a boot
  device even when none of its partitions is mounted (the live
  key's own EFI partition is the case that matters). A second,
  cloned key is protected by the same rule: Beerus over-protects
  keys, never under-protects one.
- **The medium's mountpoints.** Where live-boot mounts the boot
  medium (`/run/live/medium` and friends), those mountpoints
  mark their disk as the boot device too.
- **The marker.** The running boot console
  (`python3 -m beerus`, started by the `beerus-live`
  service) refuses to arm the real-drive executor unless the
  marker staged at build time is present and valid in the
  running system. Having the Python package is never enough to
  arm anything.

## Validating this definition (no build machine needed)

```
python3 image/check_definition.py
```

checks the definition is complete and internally consistent —
required files present, the build's volume label matching the
protection code's label, the package list carrying the engine's
tools, the service starting the launcher, the launcher starting
the console. The test suite runs the same check. Neither this
check nor the tests build the image or prove it boots; that is
Phase 5's hardware validation, and the build guide says so.
