"""Vio, MyDevAgent's little purple octopus (lots of tentacles, like its agents).

14×8 pixel art drawn with half blocks (▀ ▄): each character holds two pixels, so Vio takes up
14 columns and 4 rows. It sits above the input bar: changes expression with the mode (Shift+Tab),
wiggles its tentacles, blinks, smiles when work is done, gets X eyes on errors and hearts with /vio.
"""

from __future__ import annotations

from rich.text import Text

NAME = "Vio"
PURPLE = "#a855f7"
COLORS = {
    "P": PURPLE,  # body
    "D": "#581c87",  # outline and tentacles
    "L": "#d8b4fe",  # highlight
    "K": "#1a0b2e",  # eyes and mouth
    "W": "#ffffff",  # eye sparkle
    "C": "#f472b6",  # cheeks and hearts
    "Y": "#facc15",  # star eyes
    "B": "#67e8f9",  # glasses
    "R": "#f43f5e",  # error
}

HEAD = [".....DDDD.....", "...DDPPPPDD...", "..DPLPPPPPPD.."]
EYES = {
    "open": [".DPPWKPPWKPPD.", ".DPPKKPPKKPPD."],
    "happy": [".DPPKPPPPKPPD.", ".DPKPKPPKPKPD."],
    "closed": [".DPPPPPPPPPPD.", ".DPKKKPPKKKPD."],
    "look": [".DPPPWKPPWKPD.", ".DPPPKKPPKKPD."],
    "glasses": [".DPBBBPPBBBPD.", ".DPBWKBBWKBPD."],
    "stars": [".DPPYWPPYWPPD.", ".DPPYYPPYYPPD."],
    "cross": [".DPRPRPPRPRPD.", ".DPPRPPPPRPPD."],
    "hearts": [".DPCPCPPCPCPD.", ".DPPCPPPPCPPD."],
}
MOUTHS = {
    "smile": ".DPCPPKKPPCPD.",
    "open": ".DPCPKKKKPCPD.",
    "flat": ".DPCPPDDPPCPD.",
}
TENTACLES = [  # two poses: they alternate to make them wave
    ["DPDPDPPPPDPDPD", "D.P.P.DD.P.P.D"],
    ["DPDPDPPPPDPDPD", ".D.P.PDDP.P.D."],
]
# name → (eyes, mouth)
EXPRESSIONS = {
    "ask": ("open", "smile"),  # curious: asks before touching files
    "auto-edit": ("happy", "open"),  # eager: edits on its own
    "plan": ("glasses", "flat"),  # studious: reads and plans
    "auto": ("stars", "open"),  # full throttle
    "chat": ("look", "smile"),  # chatting
    "fast": ("happy", "smile"),
    "balanced": ("open", "smile"),
    "deep": ("glasses", "smile"),
    "ultra-deep": ("stars", "smile"),
    "auto-team": ("look", "smile"),
    "think": ("look", "flat"),
    "blink": ("closed", "smile"),
    "done": ("happy", "smile"),
    "error": ("cross", "flat"),
    "love": ("hearts", "smile"),
}
SAYS = {
    "ask": "I'll ask you before every change.",
    "auto-edit": "I edit files on my own, and ask you before commands.",
    "plan": "I read and propose a plan, without touching anything.",
    "auto": "I do everything on my own, inside this folder.",
    "chat": "I just answer: you save the files with /apply.",
    "fast": "Going fast: a single agent.",
    "balanced": "Standard team: plan, edits, tests and review.",
    "deep": "Full team: security, performance, edge cases.",
    "ultra-deep": "35 agents at work: slow, but for the big jobs.",
    "auto-team": "I pick the right team for each request.",
}
PATS = ["Thank you! ♥", "Octopus purring in progress… ♥", "Eight tentacles ready to code! ♥", "More, more! ♥"]
THINK_FACES = ["(•_•)", "( •_•)", "(•_• )", "(-_-)", "(•_•)", "(•_•)ゞ"]
WIDTH = 14


def sprite(expression: str = "ask", frame: int = 0) -> list[str]:
    eyes, mouth = EXPRESSIONS.get(expression, EXPRESSIONS["ask"])
    return HEAD + EYES[eyes] + [MOUTHS[mouth]] + TENTACLES[frame % len(TENTACLES)]


def cells(expression: str = "ask", frame: int = 0) -> list[list[tuple[str, str | None, str | None]]]:
    """Rows of (character, foreground color, background color): two pixels per character."""
    rows = sprite(expression, frame)
    out = []
    for top, bottom in zip(rows[0::2], rows[1::2], strict=True):
        line = []
        for a, b in zip(top, bottom, strict=True):
            ca, cb = COLORS.get(a), COLORS.get(b)
            if ca:
                line.append(("▀", ca, cb))
            elif cb:
                line.append(("▄", cb, None))
            else:
                line.append((" ", None, None))
        out.append(line)
    return out


def render(expression: str = "ask", frame: int = 0) -> Text:
    """For rich (preview and tests)."""
    out = Text()
    for line in cells(expression, frame):
        for char, fg, bg in line:
            out.append(char, style=f"{fg} on {bg}" if bg else (fg or ""))
        out.append("\n")
    out.rstrip()
    return out


def fragments(expression: str = "ask", frame: int = 0) -> list[list[tuple[str, str]]]:
    """For prompt_toolkit: a list of (style, text) fragments for each row."""
    return [[(f"fg:{fg}" + (f" bg:{bg}" if bg else ""), char) if fg else ("", char) for char, fg, bg in line]
            for line in cells(expression, frame)]


def think_face(elapsed: float) -> str:
    return THINK_FACES[int(elapsed * 2) % len(THINK_FACES)]


if __name__ == "__main__":  # preview: python -m mydevagent.tui.mascot
    from rich.console import Console

    console = Console()
    for name in EXPRESSIONS:
        console.print(f"[bold]{name}[/]")
        console.print(render(name))
