"""
The waste feature package: find abandoned resources with evidence.

A sibling of ``lib/`` and ``services/`` — a feature domain, not shared
plumbing — so the dependency arrow stays one-way (waste imports lib and
the scanners' data shapes; nothing imports waste except the CLI).
Providers and rules are pure functions over data the scan already
fetched: nothing in this package ever calls AWS (PRODUCT.md decision
13). Rendering stays in ``cli.py``.
"""
