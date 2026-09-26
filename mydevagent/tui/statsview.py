"""/stats in the terminal: session and total side by side, GitHub-style activity graph, day streaks."""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

from rich.console import Group, RenderableType
from rich.markup import escape
from rich.table import Table
from rich.text import Text

from ..stats import Summary
from .mascot import PURPLE

MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
DAYS = ["mon", "", "wed", "", "fri", "", ""]
LEVELS = ["#3b0764", "#6b21a8", "#9333ea", "#c084fc"]  # little → lots of activity
EMPTY = "#3f3f46"
RANGES = {"7": 7, "30": 30, "all": None}


def num(n: float) -> str:
    return f"{int(n):,}"


def tokens(n: int) -> str:
    return num(n) if n < 1_000_000 else f"{n / 1_000_000:.1f} million"


def duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f} s"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60} min" if minutes % 60 else f"{minutes // 60} h"


def level(n: int, peak: int) -> int:
    """0 = nothing, 1…4 = quarters of the busiest day (the busiest day is always the brightest)."""
    return 0 if n <= 0 or peak <= 0 else min(4, -(-4 * n // peak))


def heatmap(per_day: Counter, today: date, weeks: int) -> Text:
    """One column per week (from Monday), one row per day, months on top."""
    first = today - timedelta(days=today.weekday(), weeks=weeks - 1)
    peak = max(per_day.values(), default=0)
    months = [" "] * (weeks * 2)
    free = 0  # first free column for a label (without overlapping them)
    for week in range(weeks):
        monday = first + timedelta(weeks=week)
        starts = next((d for d in (monday + timedelta(days=i) for i in range(7)) if d.day == 1), None)
        label = MONTHS[(starts or monday).month - 1] if starts or week == 0 else ""
        if label and week * 2 >= free and week * 2 + 3 <= len(months):
            months[week * 2: week * 2 + 3] = label
            free = week * 2 + 4
    out = Text()
    out.append("     " + "".join(months).rstrip() + "\n", style="dim")
    for weekday in range(7):
        out.append(f" {DAYS[weekday]:<3} ", style="dim")
        for week in range(weeks):
            day = first + timedelta(weeks=week, days=weekday)
            if day > today:
                break
            lvl = level(per_day.get(day, 0), peak)
            out.append("■ " if lvl else "· ", style=LEVELS[lvl - 1] if lvl else EMPTY)
        out.append("\n")
    out.append("     less ", style="dim")
    for color in LEVELS:
        out.append("■ ", style=color)
    out.append("more", style="dim")
    return out


def _tests(s: Summary) -> str:
    if not s.tests_ok and not s.tests_failed:
        return "—"
    return " ".join(p for p in (f"[green]{s.tests_ok} ✓[/]" if s.tests_ok else "",
                                f"[red]{s.tests_failed} ✗[/]" if s.tests_failed else "") if p)


def _lines(s: Summary) -> str:
    return f"[green]+{num(s.added)}[/] [red]−{num(s.removed)}[/]" if s.added or s.removed else "—"


def _share(counter: Counter, total: int, limit: int = 3, short=lambda k: k) -> str:
    return " · ".join(f"{escape(short(k))} {100 * n // max(1, total)}%" for k, n in counter.most_common(limit))


def render(session: Summary, total: Summary, history: Summary | None = None, *, label: str = "all time",
           width: int = 100, today: date | None = None) -> RenderableType:
    """`total` fills the right column (possibly just the last few days); `history`, all of it, the rest."""
    today = today or date.today()
    history = history or total
    if not history.turns and not session.turns:
        return Text.from_markup("[dim]⎿  Nothing to count yet: make your first request to Vio![/]")
    table = Table(box=None, padding=(0, 2), show_header=True, header_style="bold")
    table.add_column("", style="dim")
    table.add_column("this session", justify="right")
    table.add_column(label, justify="right", style=f"bold {PURPLE}")
    rows = [
        ("requests", num(session.turns), num(total.turns)),
        ("tokens", tokens(session.tokens), tokens(total.tokens)),
        ("agent work time", duration(session.seconds), duration(total.seconds)),
        ("tools used", num(session.tools), num(total.tools)),
        ("files changed", num(session.files), num(total.files)),
        ("lines", _lines(session), _lines(total)),
        ("tests", _tests(session), _tests(total)),
    ]
    for row in rows:
        table.add_row(*row)
    parts: list[RenderableType] = [Text.from_markup(f"[{PURPLE}]⏺[/] [bold]MyDevAgent stats[/]"), table,
                                   Text("")]
    weeks = max(8, min(26, (width - 8) // 2))
    parts += [heatmap(history.per_day, today, weeks), Text("")]
    current, longest = history.streaks(today)
    facts = []
    if current:
        facts.append(f"🔥 [bold]{current} {'day' if current == 1 else 'days in a row'}[/]"
                     + (f" [dim](record {longest})[/]" if longest > current else " [dim](your record!)[/]"))
    days = len(history.per_day)
    facts.append(f"{days} {'active day' if days == 1 else 'active days'}")
    if history.sessions:
        facts.append(f"{history.sessions} {'session' if history.sessions == 1 else 'sessions'}")
    parts.append(Text.from_markup("  " + " · ".join(facts)))
    when, likes = [], []
    if history.per_day:
        best_day, best = max(history.per_day.items(), key=lambda kv: (kv[1], kv[0]))
        when.append(f"record day: {MONTHS[best_day.month - 1]} {best_day.day} ({best} "
                    f"{'request' if best == 1 else 'requests'})")
    if history.hours:
        when.append(f"favorite hour: {history.hours.most_common(1)[0][0]:02d}:00")
    for name, counter in (("favorite model", history.models), ("favorite team", history.teams)):
        if counter:
            likes.append(f"{name}: {escape(counter.most_common(1)[0][0])}")
    for line in (when, likes):
        if line:
            parts.append(Text.from_markup("  [dim]" + " · ".join(line) + "[/]"))
    if history.projects:
        projects = _share(history.projects, history.turns, short=lambda p: p.replace("\\", "/").rstrip("/")
                          .rsplit("/", 1)[-1] or p)
        parts.append(Text.from_markup(f"  [dim]projects: {projects}[/]"))
    return Group(*parts)
