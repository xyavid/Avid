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

- **Tool use** — built-in tools for reading, writing and editing files, globbing, running shell
  commands and tracking todos, plus MCP tools the workspace declares; every call is validated first.
- **Subagents** — independent subtasks run in parallel, each handed a structured task brief.
- **Skills** — reusable instructions (`SKILL.md`): the catalog shows one line, the full text loads
  when it applies.
- **Context management** — assembled in blocks and compacted into a structured checkpoint that keeps
  verified facts apart from hypotheses, so long tasks stay coherent.
- **Session persistence** — conversations and state live on disk, with branches and a change line;
  `/rewind` rolls back the conversation pointer and the files together. Every workspace's sessions
  share one directory (`~/.avid/sessions` by default, movable to another disk).
- **Model/provider abstraction** — BYOK across four protocols (OpenAI-compatible / Responses /
  Anthropic / Ollama); swapping a model never touches the core.
- **Permissions** — everything runs by default; destructive commands ask first, host credentials are
  refused, sandbox and audit are optional.

## Quick Start

```bash
git clone https://github.com/xyavid/Avid.git && cd Avid
uv sync --extra web                          # kernel + web deps (drop [web] for CLI only)
uv run avid --agent "Read pyproject.toml and tell me the project name."
```

```bash
uv run avid                    # interactive session: /compact, /rewind, /<skill-name>
uv run avid web --port 8765    # web UI → http://127.0.0.1:8765
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
  and then does it (`avid session dir` shows where they live now).
- **Environment variables**: all optional, not needed for day-to-day use.

| Variable | Meaning |
|---|---|
| `AVID_HOME` | move the user-level directory (settings, registry, sessions, audit), default `~/.avid` |
| `AVID_SESSIONS_DIR` | put the session directory somewhere else (wins over the settings file) |
| `AVID_AUDIT_DIR` | put the audit JSONL in its own directory |
| `AVID_MAX_PARALLEL_TOOL_CALLS` | parallel tool calls per step, default 10 |
| `AVID_MODEL_INFO` | `off` disables probing the provider for the model window |
| `AVID_SANDBOX_BIN` | use a different bwrap binary (diagnostics / packaging) |
| `AVID_ALLOWED_HOSTS` | extra trusted host names for the web server (LAN deploys) |

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
