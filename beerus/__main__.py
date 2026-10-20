"""Run the Live Boot console: ``python3 -m beerus``.

A thin shim over ``beerus.live.main`` so the live image's
launcher has a single, obvious entry point. All of the behavior
— including the refusal to arm anything when this is not a
running live image — lives in ``beerus.live``.
"""

from .live import main

if __name__ == "__main__":
    raise SystemExit(main())
