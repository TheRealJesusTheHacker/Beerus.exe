"""Image-level boot-medium protection — the second layer.

Phase 1's boot-device exclusion reads the mount tree: a partition
mounted at ``/``, ``/boot``, ``/boot/efi``, or ``/efi`` marks its
whole disk as the boot device. That layer has a known blind spot,
recorded since Phase 1: a boot partition that is *not mounted* is
invisible to it. The classic case is the Live Boot key itself —
booted from USB, the live system runs from memory and a squashfs
image, and the key's own EFI system partition may never be mounted
at all.

This module is the second, independent layer, and it is part of
the image's design rather than the running system's luck:

* **A label baked into the image.** Every Live Boot image
  is built with the volume label ``BEERUS_KEY`` (see
  ``image/build.sh`` — the label is a build input, not something
  a running system chooses). Filesystem labels are readable from
  ``lsblk`` without mounting anything, so the disk that carries a
  partition with this label is marked as a boot device even when
  every one of its partitions is unmounted. Marking is
  deliberately one-directional: a disk that merely *carries the
  label* (a second, cloned Live Boot key, for example) is also
  never a target. Beerus over-protects keys; it never
  under-protects one.

* **The live medium's own mountpoints.** Debian live-boot mounts
  the boot medium at well-known places (``/run/live/medium`` and
  friends). A partition mounted there marks its disk as the boot
  device through the same layer.

* **A marker file inside the image.** The built image carries
  ``/etc/beerus/live-image.json``, written at build time, stating
  that the running system *is* the Beerus live image and where
  job documents belong. The boot entry point (``beerus.live``)
  refuses to arm the real-drive executor unless this marker is
  present and valid — possession of the Python package alone is
  never enough to arm anything.

The three signals answer different questions and are kept
separate on purpose: the label and the medium mountpoints say
*which disk* must never be wiped, and the marker says *this
system* is a live image at all. Inventory consults the first
two; the boot entry point consults the third.
"""

from __future__ import annotations

import json

# The volume label baked into every Live Boot image at
# build time (image/build.sh passes it to the image builder as
# the ISO volume label, and the flash medium carries it). Exact
# match after stripping surrounding whitespace — labels are
# case-sensitive data, and a near-miss is not the key.
LIVE_VOLUME_LABEL = "BEERUS_KEY"

# Where Debian live-boot mounts (or historically mounted) the
# medium the system booted from. A partition mounted at any of
# these marks its whole disk as the boot device.
LIVE_MEDIUM_MOUNTPOINTS = frozenset(
    {
        "/run/live/medium",
        "/lib/live/mount/medium",
        "/live/medium",
    }
)

# Path, inside a running live image, of the marker the build
# writes to prove this system is the Beerus live image.
IMAGE_MARKER_PATH = "/etc/beerus/live-image.json"

# The value the marker's "image" field must carry. Anything else
# in that file is not a Beerus live image marker.
IMAGE_MARKER_FORMAT = "beerus-live-image"

# Fallback home for job documents inside the live image when a
# marker does not name one. /var/log in a live system is memory:
# documents stored there are gone at shutdown, and the boot
# console says so, every time. They are never written to the
# key itself.
DEFAULT_DOCUMENTS_DIR = "/var/log/beerus/certificates"


def is_boot_medium_label(label) -> bool:
    """True when *label* is exactly the Live Boot label."""
    if label is None:
        return False
    return str(label).strip() == LIVE_VOLUME_LABEL


def is_live_medium_mountpoint(mountpoint) -> bool:
    """True when *mountpoint* is a known live-medium mountpoint."""
    if not mountpoint:
        return False
    mp = str(mountpoint)
    if mp != "/":
        mp = mp.rstrip("/")
    return mp in LIVE_MEDIUM_MOUNTPOINTS


def load_image_marker(path=IMAGE_MARKER_PATH, opener=None) -> dict | None:
    """Read and validate the live-image marker, or return None.

    Returns the marker as a dict only when the file exists, parses
    as a JSON object, declares itself a Beerus live image, AND
    names this image's own volume label. The label is the
    agreement the whole protection design stands on — the build
    stamps it into the marker and onto the medium — so a marker
    that omits it, or names a different label, is not a marker
    for a key this code will arm beside. Every other outcome —
    missing file, unreadable file, malformed JSON, a JSON value
    that is not an object, a marker for some other image —
    returns None. Callers treat None as "this is not a Beerus
    live image," which the boot entry point answers by refusing
    to arm.
    """
    if opener is None:
        opener = open
    try:
        with opener(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("image") != IMAGE_MARKER_FORMAT:
        return None
    if data.get("volume_label") != LIVE_VOLUME_LABEL:
        return None
    return data


# Prefixes a marker may name for job documents, beyond the
# default itself. In a live system these are memory-backed
# (the writable overlay, /run, /tmp) — which is the property the
# documents rule needs: records live in memory, never on the
# key, never on a drive being serviced.
_DOCUMENTS_PREFIXES = ("/var/log/", "/run/", "/tmp/")


def marker_documents_dir(marker) -> str:
    """Where job documents go for this boot, per the marker.

    The marker written at build time names the directory — but
    only within the places a live system keeps in memory: the
    default, or a path under ``/var/log``, ``/run``, or ``/tmp``.
    Anything else — a path on the key itself (a live-medium
    mountpoint), a path with a ``..`` in it, a device path, a
    mounted drive's directory — falls back to the in-memory
    default rather than writing a client's business record
    somewhere it does not belong.
    """
    if isinstance(marker, dict):
        value = marker.get("documents_dir")
        if (
            isinstance(value, str)
            and value == value.strip()
            and value.startswith("/")
            and ".." not in value.split("/")
            and not any(
                value == mp or value.startswith(mp + "/")
                for mp in LIVE_MEDIUM_MOUNTPOINTS
            )
            and (
                value == DEFAULT_DOCUMENTS_DIR
                or value.startswith(_DOCUMENTS_PREFIXES)
            )
        ):
            return value
    return DEFAULT_DOCUMENTS_DIR
