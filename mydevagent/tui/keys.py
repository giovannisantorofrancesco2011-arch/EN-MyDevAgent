"""Esc key while the agent is working (when prompt_toolkit is not reading input)."""

from __future__ import annotations

import os
import sys


class EscWatcher:
    """Reads the keyboard without blocking: `pressed()` is True if the user pressed Esc.

    POSIX: cbreak mode (Ctrl+C keeps working). Windows: msvcrt. If stdin is not a
    terminal (pipe, tests) it does nothing.
    """

    def __init__(self) -> None:
        self.enabled = sys.stdin is not None and sys.stdin.isatty()
        self._old = None

    def __enter__(self) -> EscWatcher:
        if self.enabled and os.name == "posix":
            try:
                import termios
                import tty

                fd = sys.stdin.fileno()
                self._old = termios.tcgetattr(fd)
                tty.setcbreak(fd)
            except Exception:
                self.enabled = False
        return self

    def __exit__(self, *exc) -> None:
        if self._old is not None:
            import termios

            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._old)
            self._old = None

    def pressed(self) -> bool:
        if not self.enabled:
            return False
        try:
            if os.name == "nt":
                import msvcrt

                hit = False
                while msvcrt.kbhit():
                    if msvcrt.getwch() == "\x1b":
                        hit = True
                return hit
            import select

            hit = False
            while select.select([sys.stdin], [], [], 0)[0]:
                data = os.read(sys.stdin.fileno(), 64)
                if not data:
                    break
                # Esc alone (arrow keys send Esc + "[" … and must be ignored)
                if data == b"\x1b" or (data.startswith(b"\x1b") and not data.startswith((b"\x1b[", b"\x1bO"))):
                    hit = True
            return hit
        except Exception:
            return False
