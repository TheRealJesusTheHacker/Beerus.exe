#!/bin/sh
# build.sh — build the Live Boot live image.
#
# Run this ON A REAL BUILD MACHINE (a Debian bookworm host with
# room to work), as root:
#
#     sudo ./build.sh
#
# Live-image builds do not run on a small development VM — the
# filesystem and chroot limits are documented in
# docs/IMAGE-BUILD.md, which also carries the full step-by-step
# (host setup, writing the image to a USB key, and the
# verification checklist). This script is the build itself:
#
#   1. Stage the beerus package and the image marker into the
#      image tree (stage_image.py).
#   2. Configure the live image (lb config) from the flags below
#      plus the checked-in definition under config/.
#   3. Build (lb build) and checksum the result.
#
# The volume label below is load-bearing: it is the label the
# running Beerus protects itself by (beerus.bootmedium). If it
# ever changes, it changes in both places, and
# check_definition.py fails until they agree again.
set -eu

IMAGE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$IMAGE_DIR"

if [ "$(id -u)" != "0" ]; then
  echo "build.sh: run this as root (sudo ./build.sh) — assembling a live image needs root." >&2
  exit 1
fi

if ! command -v lb >/dev/null 2>&1; then
  echo "build.sh: Debian live-build ('lb') was not found on this machine." >&2
  echo "build.sh: install it first — on Debian bookworm:  apt-get install live-build" >&2
  echo "build.sh: see docs/IMAGE-BUILD.md for the full build-machine setup." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "build.sh: python3 was not found — staging the Beerus package needs it." >&2
  exit 1
fi

# 1. Stage the package + marker (a fresh copy from this checkout).
python3 "$IMAGE_DIR/stage_image.py" "$IMAGE_DIR"

# 2. Configure. The checked-in definition lives in config/
#    (package lists, files baked into the image, build hooks);
#    the flags here are the rest of the definition — both halves
#    are versioned in this repository, so the key is reproducible
#    from source.
lb config \
  --distribution bookworm \
  --archive-areas "main contrib non-free non-free-firmware" \
  --binary-images iso-hybrid \
  --iso-application "Beerus Live Boot" \
  --iso-volume "BEERUS_KEY" \
  --iso-publisher "Sentinel Safeguard LLC" \
  --bootappend-live "boot=live components hostname=beerus-key username=user locales=en_US.UTF-8 keyboard-layouts=us beerus.live=1" \
  --debian-installer none \
  --memtest none \
  --source false

# 3. Build, then checksum whatever image comes out.
lb build

ISO=$(ls -t live-image-*.hybrid.iso live-image-*.iso 2>/dev/null | head -n 1 || true)
if [ -z "$ISO" ]; then
  echo "build.sh: the build reported success but no image file was found." >&2
  echo "build.sh: check the build output above before trying again." >&2
  exit 1
fi

sha256sum "$ISO" > "$ISO.sha256"
echo ""
echo "Built:    $IMAGE_DIR/$ISO"
echo "Checksum: $IMAGE_DIR/$ISO.sha256"
echo ""
echo "Next: write it to a USB key and run the verification"
echo "checklist in docs/IMAGE-BUILD.md — an unverified key does"
echo "not go into the field."
