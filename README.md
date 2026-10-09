<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="web/src/assets/avid-logo-dark.svg">
    <img src="web/src/assets/avid-logo-light.svg" alt="Avid" width="294">
  </picture>
</p>

# Avid

> An extensible agent runtime: you own the model calls, tool execution, the multi-step loop,
> context, memory and permissions — swappable and debuggable. The CLI and the local web server
> share one kernel.

English · [中文](README.zh-CN.md)

## What it does

- **Tool use** — built-in tools for reading, writing and editing files, globbing and grepping
  (structured `path:line:text` hits), running shell commands and tracking todos, plus MCP tools the
  workspace declares and `ask_user` when only you know the answer; every call is validated first.
  Read-only calls in one step run in parallel; writes and shell commands stay exclusive.
- **Subagents** — independent subtasks run in parallel, each handed a structured task brief.
- **Skills** — reusable instructions (`SKILL.md`): the catalog shows one line, the full text loads
  when it applies.
- **Context management** — assembled in blocks and compacted into a structured checkpoint that keeps
  verified facts apart from hypotheses, so long tasks stay coherent.
- **Session persistence** — conversations and state live on disk, with branches and a change line;
  `/rewind` rolls back the conversation pointer and the files together. Every workspace's sessions
  share one directory (`~/.avid/sessions` by default, movable to another disk).
- **Content search** — a local SQLite index (FTS5, trigram) over every session's text: find the
  conversation where you said something, open it at that point (message hits are highlighted;
  tool-output hits land in the same conversation). The index is derived data — delete it and it
  rebuilds from the JSONL files.
- **Model/provider abstraction** — BYOK across four protocols (OpenAI-compatible / Responses /
  Anthropic / Ollama); swapping a model never touches the core.
- **Permissions** — everything runs by default; destructive commands ask first, host credentials are
  refused, sandbox and audit are optional.

## Requirements

| | Requirement | Notes |
|---|---|---|
| Python | **3.12 or newer** | `requires-python >= 3.12`. The kernel's only runtime dependency is `httpx`; the web stack (fastapi / uvicorn / pydantic / websockets) sits in the optional `[web]` extra, so a CLI-only install never pulls it |
| uv | any recent version | Creates the environment, installs dependencies and the `avid` command (`uv sync` / `uv tool install`). Not mandatory: `python -m venv` + `pip install -e ".[web]"` works too |
| Node + pnpm | Node **≥ 22.22.2**, pnpm **10** | Needed **only for the browser UI** (the exact pins live in `web/package.json`: `engines` and `packageManager`) |
| OS | Linux, macOS, Windows (incl. WSL) | Kernel and CLI are pure Python and run on all three; **the sandbox exists on Linux only** (bubblewrap) and degrades visibly elsewhere, with the reason reported in the run state |
| Optional external commands | `bwrap`, `rg`, a system folder picker (`zenity` / `kdialog` / `powershell.exe` / `osascript`) | Everything still runs, one capability level lower: no bwrap → the sandbox is not enforced (child environments are still scrubbed of credential-shaped variables); no ripgrep → `grep_search` falls back to Python (an order of magnitude slower on large trees); no picker → type the path by hand in the UI |

## Quick Start

```bash
git clone https://github.com/xyavid/Avid.git && cd Avid
uv sync --extra web                          # kernel + web deps (drop [web] for CLI only)
uv run avid --agent "Read pyproject.toml and tell me the project name."
```

```bash
uv run avid                    # interactive session: /compact, /rewind, /<skill-name>
uv run avid web --port 8765    # web UI → http://127.0.0.1:8765
uv run avid session search "something you discussed"   # content search over past sessions
uv run avid index check --fix  # verify the search index; repair or rebuild it
```

Build the frontend once before `avid web`:

```bash
pnpm -C web install && pnpm -C web run copy:dist
```

To have `avid` on your PATH anywhere: `uv tool install --editable ".[web]"`.
All flags: `avid --help`, `avid web --help`, `avid workspace --help`, `avid session --help`.

## Concepts

- **Agent** — one run: model call → tool execution → results fed back, looping until it answers or stops.
- **Tools** — the actions the model may call; declaration, implementation and argument validation live
  in one place.
- **Skills** — reusable how-to text; loaded into context on demand, or kept resident.
- **Subagents** — separate child agents: they see none of your conversation, only a task brief, and
  hand back a summary.
- **Sessions** — the on-disk unit of conversation and state: resumable, branchable, rewindable.

## Configuration

- **Project instructions**: put an `AGENTS.md` in the workspace root — it enters the model's resident
  context automatically.
- **Model connections (BYOK)**: `~/.avid/models.json` (providers and bindings, secret references
  only) + `~/.avid/secrets.json` (plaintext keys, mode 0600). Maintain them from Settings → Models
  in the UI, or by hand; with nothing configured, sending a message says what is missing.
- **Approvals**: `--yes` answers the destructive-command prompt in non-interactive runs;
  `--allow-full-access` skips the prompt and the sandbox (credential refusal still applies).
- **Session storage**: one directory holds every workspace's sessions, one subdirectory per
  workspace id (a workspace's name lives in the registry, not in the path). Default
  `~/.avid/sessions`; change it in Settings → Session storage or with `AVID_SESSIONS_DIR`.
  Changing it does not move existing sessions — `avid session migrate` prints what it would move
  and then does it (`avid session dir` shows where they live now; after a move it also looks in the
  previous directory recorded by the index, so nothing is stranded). Changing it is refused while a
  run is in flight, because that run writes to the old location and must finish first.
- **Search index**: `~/.avid/index/sessions.sqlite`, built from the session files and rebuilt
  automatically as they grow (a run's messages are indexed right after they are written).
  It is disposable: `avid index check` says what is stale or broken, `avid index rebuild`
  rebuilds it, and deleting the file costs nothing but the next build. Nothing in it is
  required to read your sessions.
- **Environment variables**: all optional, not needed for day-to-day use.

| Variable | Meaning |
|---|---|
| `AVID_HOME` | Where the whole user-level directory lives (settings, registry, sessions, audit); default `~/.avid` |
| `AVID_SESSIONS_DIR` | Put the session store elsewhere (wins over the settings file) |
| `AVID_INDEX_DIR` | Put the derived search index elsewhere |
| `AVID_AUDIT_DIR` | Put the audit JSONL in a directory of its own |
| `AVID_MAX_PARALLEL_TOOL_CALLS` | Parallel tool calls per step; default 10 |
| `AVID_MODEL_INFO` | Set to `off` to stop asking the provider for the model's context window |
| `AVID_SANDBOX_BIN` | Use a different bwrap executable (diagnostics / packaging) |
| `AVID_ALLOWED_HOSTS` | Extra hosts the web server trusts (LAN deployments) |
| `AVID_BYOK_CONFIG` | Use a different `models.json` (default `~/.avid/models.json`) |
| `AVID_BYOK_SECRETS` | Use a different `secrets.json` (default `~/.avid/secrets.json`) |

## Development

```bash
uv sync --extra web                          # dependencies
uv run pytest                                # tests
uv run ruff check avid tests && uv run mypy   # static checks
pnpm -C web run verify                       # frontend: types + tests + build + size budget
```

Development conventions (commit format, testing and comment discipline) live in
[AGENTS.md](AGENTS.md) (Chinese).

## License

[MIT](LICENSE) © 2026 xyavid
