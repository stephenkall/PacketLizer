"""Generated at build time by the CI release job (see build_exe.py / ci.yml).

Holds the exact GitHub release tag (e.g. "v1.0.0.42") that this build was
published as, so the running app can compare itself against
https://github.com/<repo>/releases/latest. Left as ``None`` in a source
checkout / dev run, which disables the auto-update check (there is nothing
meaningful to compare a source run's version against).
"""

BUILD_TAG: str | None = None
