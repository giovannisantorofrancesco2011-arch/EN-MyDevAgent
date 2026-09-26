# MyDevAgent's 15 agents

All agents share **the same loaded model** (no 15 models in VRAM): what changes is the
role prompt, the blackboard sections they read, the tools, the model tier and the token budget.
Definitions: [`config/agents.yaml`](../config/agents.yaml) · prompts: [`prompts/agents/`](../prompts/agents) ·
shared persona: [`prompts/system_persona.md`](../prompts/system_persona.md).

## How they work together

```
                         ┌──────────── router (heuristics, 0 tokens) ───────────┐
                         │                                                      │
  FAST  (1 call)         ▼                                                      │
  request ─▶ best-fit specialist + Formatter rules merged in ─▶ answer          │
                                                                                │
  BALANCED / DEEP                                                               ▼
  request ─▶ [11 Research]* ─▶ 1 Architect ─▶ specialists in parallel (2,3,5,6,7,8)
                                                     │
                                                     ▼
                                   4 Debug & Test (tests + self-check in Docker sandbox)
                                                     │
                                                     ▼
                  quality gates in parallel: 12 Reviewer (+ 9 Security, 10 Performance, 14 Edge Cases in deep)
                                                     │
                     BLOCKER/MAJOR? ── yes (max 1 round in balanced, 2 in deep) ──▶ back to the specialists
                                                     │ no
                                                     ▼
                                     13 Documentation (deep or on request)
                                                     │
                                                     ▼
                                15 Output Formatter (streamed to the user)

  * Research only when up-to-date information is needed and you're online; offline the team knows and says so.
```

**Blackboard** (`mydevagent/state.py`): agents don't chat with each other. They write to shared
sections (`plan`, `artifacts`, `test_report`, `issues`, `research`…) and each one receives **only** the sections
listed in its `reads`. This cuts tokens by 50–80% compared to classic multi-agent chats.

**Output protocols** (parsed by the code):
- files: ```` ```python file=app/main.py ```` → collected and runnable in the sandbox
- self-check: ```` ```python run ```` → run by the Debug agent in Docker with no network
- quality gate: `VERDICT: APPROVE|REVISE` + `- [BLOCKER|MAJOR|MINOR] area: problem → fix`

## Agent cards

| # | Agent | Stage | Tier | Reads | Tools | Output |
|---|---|---|---|---|---|---|
| 1 | **Code Architect** | plan | reasoning | request, history, files, rag, research, image_notes | rag_search, read_file, list_dir, grep | Goal · Assumptions · Design · Files · Assignments · Acceptance criteria · Risks |
| 2 | **Algorithms & Data Structures** | specialist | main | request, history, files, plan, research, issues | run_code | Approach with O(·) and invariant · code · edge cases |
| 3 | **Languages & Frameworks** | specialist | main | + rag | rag_search, read_file, web_search | Idiomatic code · run commands. Default agent |
| 4 | **Debugging & Testing** | test | main | + artifacts, test_report, image_notes | run_code, read_file, grep, git_diff, git_log | Root cause · framework tests · 1 `run` self-check |
| 5 | **Frontend** | specialist | main | + rag, image_notes | rag_search, read_file, web_search | Accessible TS components, loading/error/empty states |
| 6 | **Backend & API** | specialist | main | + rag | rag_search, read_file, web_search | Contracts, validation, errors, auth, curl example |
| 7 | **Database & ORM** | specialist | main | + rag | rag_search, read_file | Model, reversible schema/migrations, indexes, queries |
| 8 | **DevOps & CI/CD** | specialist | main | + rag | read_file, web_search | Multi-stage Dockerfile, least-privilege CI, deploy/rollback |
| 9 | **Security** | gate | reasoning | request, plan, artifacts, research | web_search, grep | VERDICT + OWASP/CWE issues (veto on BLOCKERs) |
| 10 | **Performance** | gate | reasoning | request, plan, artifacts, test_report | run_code | VERDICT + issues with estimated impact |
| 11 | **Research** | research | main | request, history, research | web_search, web_fetch, rag_search | Findings with numbered sources `[n]` |
| 12 | **Code Reviewer & Refactor** | gate | reasoning | request, plan, artifacts, test_report, issues | git_diff, read_file | VERDICT + issues on correctness, consistency, acceptance criteria |
| 13 | **Documentation** | docs | main | request, plan, artifacts | read_file | README/section, essential docstrings |
| 14 | **Edge Case & Robustness** | gate | reasoning | request, plan, artifacts, test_report | run_code | VERDICT + inputs/conditions that break the code |
| 15 | **Output Formatter** | final | main | request, history, plan, artifacts, test_report, issues, research | — | Summary · Code · Run · Notes (+ real sandbox result added by the system) |

### Details and interactions

1. **Architect** — first agent in balanced/deep. Decides *what* to do and *who* does it (Assignments
   section), sets the acceptance criteria that Reviewer and Debug will use. Doesn't write implementations.
2. **Algorithms** — triggered by words like *complexity, graph, DP, sorting*. Must state O(·)
   and an invariant: the Performance gate checks it.
3. **Languages & Frameworks** — the "wildcard" specialist: if no domain is recognized, it takes the job.
   Detects the stack from attached files/RAG and writes idiomatic code.
4. **Debug & Test** — in fast it answers bugs directly (pasted tracebacks). In the team it writes the tests and
   **a self-check that actually runs** in Docker (`--network none`, read-only fs, nobody user, CPU/RAM
   limits). A failed self-check becomes an automatic BLOCKER → revision.
5. **Frontend** — receives the `image_notes` from the vision model if you attach a screenshot/mockup.
6. **Backend & API** / 7. **Database** / 8. **DevOps** — work **in parallel** on the same plan;
   consistency between their files is checked by the Reviewer.
9. **Security** / 10. **Performance** / 14. **Edge Cases** / 12. **Reviewer** — the quality gates run in
   parallel. In balanced only the Reviewer (+ those triggered by keywords, e.g. "secure" → Security).
   BLOCKER/MAJOR issues send the work back to the specialists along with their previous version.
11. **Research** — generates 1–2 queries with the `fast` model, searches (Tavily → Firecrawl → SearXNG →
    DuckDuckGo), reads the best page, summarizes with sources. In fast mode search results
    are injected directly into the context (no extra LLM call).
13. **Documentation** — only in deep or when you ask for documentation.
15. **Formatter** — merges everything, applies the remaining fixes, streams the answer. In fast its
    output rules are merged into the specialist's prompt (saving a whole call).

## Steering the team from your message

| Type | Effect |
|---|---|
| `/fast` `/balanced` `/deep` | forces the mode |
| `@sec` `@security` | adds Security to the quality gate (or makes it primary in fast) |
| `@perf` `@edge` `@review` | same for Performance, Edge Cases, Reviewer |
| `@web` `@research` | forces web search |
| `@db` `@be` `@fe` `@devops` `@algo` `@lang` | forces the specialist |
| `@docs` | adds documentation |

`mydevagent route "your request"` shows the router's decision without calling the model.

## Agent mode: how they work on files
In the interface (`mydevagent`) the team doesn't just answer: it **edits the project**.

```
fast      specialist chosen by the router ── tool loop: read → edit → test → answer
balanced  1 Architect (plan) → specialist in tool loop → 12 Reviewer on the REAL DIFF → fixes (1 round)
deep      like balanced + 9 Security, 10 Performance, 14 Edge Cases on the diff (2 rounds)
```
- The loop (`mydevagent/agent/loop.py`) uses native function calling with models that support it
  (`native_tools: true` in the profile) and a text protocol `<tool name="…">{json}</tool>` with 7B models,
  with an example in the prompt and an automatic reminder if the model pastes code instead of using the tools.
- The agent receives **project memory** (`MYDEVAGENT.md`), a **repo map** (files + classes/functions),
  code snippets from semantic search and the Architect's plan.
- The quality gates receive the **real diff** and the result of the **tests actually run**; if they find
  BLOCKER/MAJOR issues the agent fixes them in the same conversation.

## `/ultra-deep`: the 35 agents
The 20 extended agents are in [`config/agents_ultra.yaml`](../config/agents_ultra.yaml) and
[`prompts/agents/16_…md` – `35_…md`](../prompts/agents). The pipeline is in [`mydevagent/ultra.py`](../mydevagent/ultra.py):

| Phase | Agents |
|---|---|
| 1 · Research (only if needed, decided by the `fast` model; flagged when offline) | 11 Research |
| 2 · Requirements and plan with debate | 16 Analyst → 1 Architect → 34 Devil's advocate → 1 (revision if ADJUST/REPLACE) |
| 3 · Test strategy | 29 Test Strategist |
| 4 · Implementation | relevant specialists (core + extended) — file agent, or artifacts in chat mode |
| 5 · Tests | real execution + 4 Debug & Test |
| 6 · Mega quality gate in parallel | 9 Security · 10 Performance · 12 Reviewer · 14 Edge · 24 A11y/i18n · 26 Observability · 30 Threat model · 31 Scalability · 33 Fact-checker (web) · 27 Dependencies (web) |
| 6b · Short lens reviews (~180 tokens) | all unused specialists: 2 3 5 6 7 8 17 18 19 20 21 22 23 25 28 — they can say "not relevant" |
| 7 · Integration | 35 Lead Integrator: merges, discards false positives, decides SHIP / FIX → back to phase 5 (max 3 rounds) |
| 8 · Docs and release | 13 Documentation + 32 Release (applied to the files if the project has a README/CHANGELOG) |
| 9 · Delivery | 15 Formatter |

The Fact-checker and the Dependencies agent receive targeted web searches on the libraries imported in the
modified code (if you're online). All 35 agents take part; the final summary shows how many
worked (`ultra-deep · 35 agents · …`). The router never turns on `/ultra-deep` by itself: for very
large requests it **suggests** it.
