"""The local launcher: one command that starts the API, serves the built UI and opens a window.

Stdlib only, and deliberately no import from `avid.web`: this package is reachable from a CLI-only
install, where the web extra is absent.
"""

from .launch import launch_ui

__all__ = ["launch_ui"]
