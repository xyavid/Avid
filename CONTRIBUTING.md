# Contributing

For anyone changing this repository. Avid is a self-built agent runtime: a Python 3.12 kernel
(`uv` for dependencies, `httpx` as its only runtime dependency) with a browser UI under `web/`
(React 18 + Vite + pnpm).

English · [中文](CONTRIBUTING.zh-CN.md)

## 1. Setup and gates

```bash
uv sync && uv sync --extra web       # kernel deps / add the web deps (fastapi·uvicorn·pydantic)
pnpm -C web install                  # frontend deps

uv run pytest                        # everything (stress tests are skipped by default, see pyproject)
uv run pytest -m stress              # complexity and long-session gates (a separate CI job)
uv run ruff check avid tests && uv run mypy        # static checks, the same set CI runs
pnpm -C web run verify               # frontend: typecheck + vitest + build + size budget
```

To work on the UI, run the two processes side by side — `avid web` is an API server and holds no
page, so they stay on different origins exactly as a deployment does:

```bash
uv run avid web --port 8765          # the API
pnpm -C web dev                      # the UI on :5173, pointed at :8765 by web/.env.development
```

To look at a built bundle the way a user would — same command, same origins, same cleanup:

```bash
pnpm -C web build && uv run avid ui --no-open
```

All of these must be green before you open a pull request.

## 2. Commit conventions

Commit once per verifiable step; don't pile one piece of work into a single large commit.

```
<type>(<scope>): <summary>

<body: why this change>
```

- **type** is one of `feat` / `fix` / `refactor` / `test` / `docs` / `chore` / `perf`.
- **scope** names the affected module, e.g. `loop`, `tools`, `context`, `perm`.
- **summary** is a Chinese imperative sentence saying what was done: under 50 characters, no
  trailing period.
- **body** is only for what the diff cannot show: the reason, the tradeoff, the rejected
  alternative.
- **granularity**: one thing per commit; the project stays runnable after each one.

## 3. Comment discipline

- **Comments are in English**, and only say what the code cannot.
- Keep: numeric thresholds, failure semantics, cross-module invariants, external contracts, and
  "this is the only place" single-point declarations.
- Length: module docstring ≤ 2 sentences (what it owns, plus one invariant if needed), functions
  and classes ≤ 1 sentence (delete it when the name already says it), inline comments ≤ 1 line.
- Don't write: history, comparisons with other projects, design retrospectives, examples,
  tables, bulleted lists.
- Chinese stays only in **strings meant for people or models** (UI copy, error messages, prompt
  text, logs).
- Changing code means changing its comments; when a comment lies, fix it on the spot.

## 4. Testing

- **Write unit tests first**: if a change needs a unit test, write it before the implementation.
  A test added afterwards does not count.
- **E2E is the default**: complex features are verified end to end, not by unit tests standing in.
  An E2E run must leave behind a **reproducible artifact** — the same command repeated gives the
  same conclusion, and the artifact lives in the repository so anyone can open and check it.
- **List the failure modes first**: when a system has to be tested in isolation, write down
  everything that can go wrong before writing the code.
- Run the minimum relevant checks while working; run the full suite once before opening the PR.

## 5. Runtime dependencies

The kernel allows exactly one third-party runtime dependency, `httpx` (enforced by
`tests/test_runtime_deps.py`); the web stack lives in the `[web]` extra, so a CLI-only install
never pulls it. Widening this (a new dependency, or a privileged import) means **acknowledging it
explicitly** in `pyproject.toml`, in this file, and in that gate.

## Where things live

- Install, configuration and the full CLI reference: `README.md` / `README.zh-CN.md`.
- The project's own architecture lives in the code: module docstrings state what each module owns,
  `tests/test_web_boundaries.py` pins the cross-package dependency directions, and
  `tests/test_wire_contract.py` (REST field names) / `test_event_contract.py` (event names and
  tiers) / `test_event_payload_contract.py` (event payload field names, checked against real
  events) pin the frontend contracts.
