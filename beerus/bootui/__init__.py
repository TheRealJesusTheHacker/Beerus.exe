"""The Beerus boot-environment UI (the Phase 4 graphical session).

This package is the graphical face of the Live Boot
line: the window the live image opens when its graphical session
starts. It is a presentation layer only, and one law governs
everything in it:

**Inheritance law: this package contains NO arming code and
never constructs a RealExecutor.** The only arming site in
Beerus remains ``beerus.live.build_live_engine``. This UI
consumes the ``(engine, authority, documents_dir)`` triple that
builder returns — the same engine, the same gate authority, the
same documents directory the text console runs on — and drives
them through the same session API the console and the portable
app use. If the builder refuses (not a live image, not the root
session), there is no engine, and this UI shows the refusal and
exits; it has no path around that verdict and no executor of
its own to reach for.
"""
