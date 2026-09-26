"""/ultra-deep: the 35-agent pipeline.

    1. Research (if needed and online)   11 Research
    2. Requirements and plan, debated    16 Analyst → 1 Architect → 34 Devil's advocate → (1 revision)
    3. Test strategy                     29 Test Strategist
    4. Implementation                    relevant specialists (agent working on files, or chat artifacts)
    5. Tests                             real execution + 4 Debug & Test
    6. Parallel mega quality gate        9 10 12 14 24 26 30 31 33 + 27 Dependencies (with web)
                                         + a short "lens review" from every unused specialist
    7. Integration                       35 Integrator → fixes (max N rounds) → 5-7 again
    8. Documentation and release         13 Docs + 32 Release
    9. Delivery                          15 Formatter (run by the caller, streaming)

Every agent takes part: the non-relevant ones do a short review (~150 tokens) and may
answer "not relevant".
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol

from .graph import Team, collect_files
from .reasoning import ISSUE_RE, parse_review, strip_thinking
from .state import TeamState, latest_issues, render_artifacts, truncate
from .tools.web_search import format_results

GATES = ["security", "performance", "edge_cases", "reviewer", "a11y_i18n", "observability", "threat_model",
         "scalability", "fact_checker", "dependencies"]
SPECIALISTS = ["algorithms", "language", "frontend", "backend", "database", "devops", "api_design", "mobile",
               "cloud", "data", "ai_ml", "concurrency", "systems", "ux_ui", "migration"]
LENS_TOKENS = 180
PARALLEL = 4
IMPORT_RE = re.compile(r"^\+?\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+)|.*?require\(['\"]([@\w/.-]+)['\"]\)"
                       r"|.*?from\s+['\"]([@\w/.-]+)['\"])", re.MULTILINE)
STDLIB_HINT = {"os", "sys", "re", "json", "time", "typing", "pathlib", "dataclasses", "collections", "math",
               "random", "datetime", "itertools", "functools", "subprocess", "logging", "unittest", "asyncio",
               "abc", "enum", "io", "copy", "string", "hashlib", "uuid", "threading", "tempfile", "shutil",
               "argparse", "csv", "sqlite3", "http", "urllib", "contextlib", "inspect", "fs", "path", "react"}


class Implementer(Protocol):
    def implement(self, task: str, specialists: list[str]) -> str: ...
    def subject(self) -> str: ...
    def run_tests(self) -> str: ...
    def fix(self, fixes: str) -> str: ...
    def apply_docs(self, notes: str) -> str: ...


class UltraPipeline:
    def __init__(self, team: Team, route, implementer: Implementer, *, online: bool, web=None) -> None:
        self.team = team
        self.route = route
        self.impl = implementer
        self.online = online
        self.web = web
        self.registry = team.registry
        self.max_rounds = team.settings.mode("ultra-deep").max_review_rounds or 3
        self.participants: set[str] = set()

    # ------------------------------------------------------------ helpers
    def _emit(self, **event: Any) -> None:
        self.team.emit(event)

    def _phase(self, text: str) -> None:
        self._emit(type="info", text=text)

    def _agent(self, key: str, state: TeamState, task: str, max_tokens: int | None = None) -> str:
        text, entry = self.team.run_agent(key, state, task=task, max_tokens=max_tokens)
        state.setdefault("trace", []).append(entry)
        self.participants.add(key)
        return text

    def _parallel(self, jobs: list[tuple[str, TeamState, str, int | None]]) -> dict[str, str]:
        with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
            futures = {key: pool.submit(self.team.run_agent, key, st, task=task, max_tokens=mt)
                       for key, st, task, mt in jobs}
            out = {}
            for key, future in futures.items():
                text, entry = future.result()
                out[key] = text
                self.participants.add(key)
        return out

    def _skip(self, key: str, reason: str) -> None:
        self._emit(type="agent_skip", agent=key, name=self.registry[key].name, reason=reason)
        self.participants.add(key)

    # ---------------------------------------------------------------- run
    def run(self, state: TeamState) -> TeamState:
        state.setdefault("trace", [])

        # 1. research, only if needed
        self._phase("phase 1/8 · research")
        if self._needs_research(state["request"]):
            if self.online:
                state.update(self.team.research_node(state))
                self.participants.add("research")
            else:
                state["research"] = "OFFLINE: web research unavailable — flag what needs verification."
                self._skip("research", "offline")
        else:
            self._skip("research", "not needed")

        # 2. requirements + plan + debate
        self._phase("phase 2/8 · requirements and plan, debated")
        requirements = self._agent("requirements", state, "Write the requirements.")
        specialists = self._implementers()
        plan_task = ("Produce the plan. Requirements from the analyst:\n" + requirements +
                     "\nSpecialists available: " + ", ".join(specialists) + ".")
        plan = self._agent("architect", state, plan_task)
        state["plan"] = plan
        critique = self._agent("devils_advocate", state, "Critique the plan.")
        if re.search(r"\b(ADJUST|REPLACE)\b", critique):
            plan = self._agent("architect", state, plan_task + "\n\nRevise your plan considering this "
                               "critique (keep what is right, change what is wrong):\n" + critique)
        state["plan"] = f"{plan}\n\n## Requirements\n{requirements}"

        # 3. test strategy
        self._phase("phase 3/8 · test strategy")
        test_plan = self._agent("test_strategy", state, "Design the test strategy for this plan.")

        # 4. implementation
        self._phase("phase 4/8 · implementation")
        task = (f"# Task\n{state['request']}\n\n# Plan (Architect, reviewed by Devil's advocate)\n{state['plan']}"
                f"\n\n# Test strategy (write these tests too)\n{test_plan}")
        if state.get("research"):
            task += f"\n\n# Web research\n{state['research']}"
        summary = self.impl.implement(task, specialists)
        self.participants.update(specialists)

        round_ = 0
        while True:
            state["round"] = round_
            # 5. test
            self._phase(f"phase 5/8 · tests{f' (round {round_ + 1})' if round_ else ''}")
            state["test_report"] = self.impl.run_tests()
            state["artifacts"] = {"implementation": f"Implementer summary: {summary}\n\n{self.impl.subject()}"}
            debug = self._agent("debug_test", state, "Analyse the test report: root cause of any failure "
                                "and the exact fix. If everything passes, say so in one line.", max_tokens=500)
            issues: list[dict[str, Any]] = []
            expects = getattr(self.impl, "expects_changes", lambda: False)()
            if expects and "(no file changes)" in state["artifacts"]["implementation"]:
                issues.append({"agent": "integrator", "severity": "BLOCKER", "round": round_,
                               "text": "no file was changed although the request asks for changes: apply them "
                                       "with edit_file/write_file"})
            if state["test_report"].startswith(("FAIL", "TIMEOUT", "exit code")) and not \
                    state["test_report"].startswith("exit code 0"):
                issues.append({"agent": "debug_test", "severity": "BLOCKER", "round": round_,
                               "text": "tests failing: " + truncate(debug, 800)})

            # 6. mega gate
            self._phase("phase 6/8 · quality gate (all agents)")
            issues += self._gates(state, specialists, round_)
            state["issues"] = [i for i in state.get("issues", []) if i.get("round", 0) != round_] + issues

            # 7. integrator
            self._phase("phase 7/8 · integrating the reviews")
            decision, fixes = self._integrate(state)
            forced = [f"- [BLOCKER] {i['text']}" for i in issues if i["agent"] == "integrator"]
            if forced:  # deterministic check: no changes = always needs fixing
                decision, fixes = "FIX", forced + fixes
            if decision == "SHIP" or round_ >= self.max_rounds:
                if decision != "SHIP":
                    self._phase(f"limit of {self.max_rounds} fix rounds reached")
                break
            round_ += 1
            self._phase(f"fixes, round {round_}: {len(fixes)} items")
            summary = self.impl.fix("\n".join(fixes))

        # 8. documentation and release
        self._phase("phase 8/8 · documentation and release")
        state["artifacts"] = {"implementation": f"Implementer summary: {summary}\n\n{self.impl.subject()}"}
        notes = self._parallel([
            ("docs", state, "Write the minimal documentation updates for this change.", None),
            ("release", state, "Prepare version bump, changelog entry and upgrade notes.", None),
        ])
        self.impl.apply_docs(f"{notes['docs']}\n\n{notes['release']}")
        state["artifacts"] = {"implementation": f"Implementer summary: {summary}\n\n{self.impl.subject()}",
                              "docs": notes["docs"], "release": notes["release"]}
        return state

    # ------------------------------------------------------------ phases
    def _needs_research(self, request: str) -> bool:
        if self.route.research:
            return True
        prompt = ("Does answering this programming request require up-to-date information from the web "
                  "(recent library versions, new APIs, changelogs, CVEs, current best practice)? "
                  "Reply only YES or NO.\nRequest: " + request[:1500])
        try:
            out = self.team.llm.complete([{"role": "user", "content": prompt}], tier="fast", max_tokens=5,
                                         temperature=0.0).text
            return "YES" in strip_thinking(out).upper()
        except Exception:
            return False

    def _implementers(self) -> list[str]:
        chosen = list(dict.fromkeys(self.route.specialists + [
            k for k in self.route.ultra_relevant if self.registry[k].stage == "specialist"]))
        return chosen or ["language"]

    def _web_facts(self, subject: str) -> str:
        """Targeted search on the libraries used in the code (for the Fact-checker and Dependencies)."""
        if not (self.online and self.web is not None):
            return "OFFLINE or web disabled: verify library APIs and versions manually."
        packages: list[str] = []
        for groups in IMPORT_RE.findall(subject):
            name = next((g for g in groups if g), "")
            root = name.lstrip("@").split("/")[0].split(".")[0]
            if root and root.lower() not in STDLIB_HINT and root not in packages and not root.startswith("_"):
                packages.append(root)
        if not packages:
            return "No third-party packages detected in the change."
        chunks = []
        for pkg in packages[:3]:
            chunks.append(f"## {pkg}\n" + format_results(self.web.search(f"{pkg} latest version official "
                                                                         "documentation changelog")))
        return "\n\n".join(chunks)

    def _gates(self, state: TeamState, implementers: list[str], round_: int) -> list[dict[str, Any]]:
        subject = state["artifacts"]["implementation"]
        facts = self._web_facts(subject)
        gate_state: TeamState = {**state, "research": f"{state.get('research', '')}\n\n# Library facts (web)\n{facts}"}
        jobs = [(key, gate_state, "Review the implementation now.", None)
                for key in GATES if key in self.registry.agents]
        lens_keys = [k for k in SPECIALISTS if k in self.registry.agents and k not in implementers]
        jobs += [(key, state, "Quick lens review of the implementation from your area of expertise. "
                  "If your area is not relevant, say so.", LENS_TOKENS) for key in lens_keys]
        outputs = self._parallel(jobs)
        issues = []
        for key, text in outputs.items():
            for sev, body in parse_review(text).issues:
                if body.lower().strip(" .").startswith("none"):
                    continue
                issues.append({"agent": key, "severity": sev, "text": body, "round": round_})
        return issues

    def _integrate(self, state: TeamState) -> tuple[str, list[str]]:
        text = self._agent("integrator", state, "Integrate all review issues into one prioritised fix list.")
        fixes = [f"- [{sev.upper()}] {body.strip()}" for sev, body in ISSUE_RE.findall(text)]
        blocking = [f for f in fixes if f.startswith(("- [BLOCKER]", "- [MAJOR]"))]
        match = re.search(r"DECISION\s*:\s*(SHIP|FIX)", text, re.IGNORECASE)
        decision = match.group(1).upper() if match else ("FIX" if blocking else "SHIP")
        if decision == "FIX" and not blocking:
            decision = "SHIP"
        if decision == "SHIP" and blocking:  # the integrator cannot ignore BLOCKER/MAJOR issues it lists itself
            decision = "FIX"
        return decision, blocking


# ---------------------------------------------------------------- implementer
class ChatImplementer:
    """Chat mode: the specialists produce artifacts (code in blocks), tests run in the sandbox."""

    def __init__(self, team: Team, state: TeamState) -> None:
        self.team = team
        self.state = state
        self.specialists: list[str] = []

    def implement(self, task: str, specialists: list[str]) -> str:
        self.specialists = specialists
        self.state["route"] = {**self.state["route"], "specialists": specialists}
        self._run_specialists(lambda key: "Implement your part of the plan.\n\n" + task)
        return "specialists produced the artifacts"

    def _run_specialists(self, task_for) -> None:
        with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
            results = list(pool.map(lambda key: (key, self.team.run_agent(key, self.state, task=task_for(key))[0]),
                                    self.specialists))
        arts = dict(self.state.get("artifacts_impl", {}))
        arts.update({k: v for k, v in results if v})
        self.state["artifacts_impl"] = arts

    def subject(self) -> str:
        return render_artifacts({"artifacts": self.state.get("artifacts_impl", {})}, self.team.registry)

    def run_tests(self) -> str:
        arts = self.state.get("artifacts_impl", {})
        if not collect_files(arts, self.team.registry):
            return "NOT RUN: no code artifacts."
        result = self.team.test_node({**self.state, "artifacts": arts})
        if "debug_test" in result.get("artifacts", {}):
            self.state.setdefault("artifacts_impl", {})["debug_test"] = result["artifacts"]["debug_test"]
        return result.get("test_report", "NOT RUN")

    def fix(self, fixes: str) -> str:
        previous = self.state.get("artifacts_impl", {})
        self._run_specialists(lambda key: (
            "Revise your previous output to fix the issues that concern your part. Output the complete "
            "corrected files.\n## Issues\n" + fixes + "\n\n## Your previous output\n"
            + truncate(previous.get(key, "(none)"), 8000)))
        return "specialists revised the artifacts"

    def apply_docs(self, notes: str) -> str:
        return notes


def issues_summary(state: TeamState) -> str:
    issues = latest_issues(state)
    if not issues:
        return "✅ Ultra-deep review: no blocking issues"
    blocking = [i for i in issues if i["severity"] in ("BLOCKER", "MAJOR")]
    return (f"⚠️ Ultra-deep review: {len(blocking)} blocking issues remaining" if blocking
            else f"✅ Ultra-deep review: {len(issues)} minor notes")
