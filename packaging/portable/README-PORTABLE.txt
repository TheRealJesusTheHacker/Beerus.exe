BEERUS.EXE - Beerus Portable
Sentinel Safeguard LLC

WHAT THIS IS
------------
Beerus Portable is the app edition of Beerus, Sentinel's secure
drive-destruction tool - the same engine, the same NIST 800-88
methods, the same consent gates, the same verification and
certificates as the Destruct Key Pro boot key, in a folder that
runs on a Windows machine that is already switched on. No
install, no Python on the machine: unzip it anywhere, including
a USB stick, and run Beerus.exe.

Beerus has two forms. The boot key starts a machine that is off
and destroys drives before any system loads. Beerus Portable is
for a machine that is already running - the technician's tool
for wiping a drive attached to a live system.

WHAT IT DESTROYS - AND WHAT IT REFUSES
-------------------------------------
Three modes, chosen on the first screen:

- A whole drive: the drive's own data is destroyed on its
  surface - the drive's built-in sanitize where the drive has
  one (NVMe Sanitize, ATA Secure Erase), a multi-pass overwrite
  for spinning disks - then the surface is read back and the
  result verified before a certificate of destruction is
  issued.
- Files and folders: the files you pick are overwritten in
  full, read back to prove the overwrite, and only then are
  their entries removed. On an SSD this cannot promise the
  drive's memory cells themselves were rewritten - the app
  says so on screen; the whole-drive sanitize is the strong
  guarantee there.
- Free space: the unused space on a volume is filled and
  overwritten so data deleted earlier cannot be recovered.
  Existing files and Windows itself are not touched.

Destruction is never "delete the entry and hope": a Beerus
wipe destroys the data itself, always, in every mode.

On a live Windows system, two drives can never be whole-drive
targets here: the drive Windows is running from, and the drive
Beerus Portable itself is running from. Both are shown in the
list, greyed, with the reason - never hidden. A drive Beerus
cannot identify (no readable model or serial number) is never
wiped either. File and folder selections that reach into
Windows' own folders are refused plainly - file mode is for
your files, never for the operating system.

THE GATES ARE THE PRODUCT
-------------------------
Nothing is one careless click. Every wipe, in every mode,
walks the same consent discipline: the exact target shown up
front, the full scope stated in plain language, an explicit
acknowledgment, and an exact typed confirmation before
anything runs. A wrong serial number cancels the confirmation
on the spot. The scope you confirm is the scope that runs -
nothing expands silently, and links are never followed.

ARMING - READ THIS
------------------
This edition ships DISARMED, the same way the engine ships
everywhere: its real-drive executor refuses to run until it
has been armed by Sentinel at deployment - a deployment build
with the executor constructed armed, plus the
BEERUS_REAL_WIPE_ARMED environment variable set on the
machine. There is no setting inside the app that arms it, and
no way to click your way past it. A disarmed copy walks the
whole gate flow and then refuses, in plain language, before
anything is sent to a drive. Field validation of armed
destruction on real hardware is Phase 5 work and is not
claimed here.

WHERE THINGS ARE WRITTEN (honest list)
--------------------------------------
- Inside this folder, and nowhere else of Beerus's own:
  a "certificates" folder is created beside Beerus.exe and
  holds the documents Beerus issues - certificates of
  destruction for whole-drive wipes, and the completion
  reports for file, folder, and free-space wipes. They are
  your job records; deleting the folder deletes them.
- Nothing is installed and nothing is registered: no Windows
  services, no registry entries, no start-menu items. Deleting
  this folder removes the program.
- Windows itself keeps its own usual records of any program
  running (for example, event logs); Beerus writes none of
  those on purpose and adds nothing beyond them.

THE OTHER FORM
--------------
The Destruct Key Pro - this same repository as a bootable USB
key - remains the tool for machines that are off, unbootable,
or being decommissioned whole. One engine, two forms; the
paperwork standard is the same on both.
