"""Run the Live Boot boot UI: ``python3 -m beerus.bootui``.

A thin shim over ``beerus.bootui.app.main`` so the live image's
graphical launcher has a single, obvious entry point. All of the
behavior — including the refusal window shown when the one
arming site (``beerus.live.build_live_engine``) refuses to build
an engine — lives in ``beerus.bootui.app``.
"""

from .app import main

if __name__ == "__main__":
    raise SystemExit(main())
