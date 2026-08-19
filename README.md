# Ubuntu Control Deck

**Run your Ubuntu box from your phone.** Apps, terminals, remote desktop, files, automation, and local LLMs —
all from one browser tab, whether that tab is on your desktop or in your pocket.

One script to set it all up. No Docker, no cloud, no accounts. Your machine stays yours.

```bash
git clone https://github.com/souten-yd/ControlDeck.git && cd ControlDeck
./deck.sh
```

That's it. `deck.sh` installs what's missing, builds the frontend, creates your admin user, and starts the server
at `http://127.0.0.1:8765`.

## What you get

**📊 Watch everything, from anywhere**
CPU, RAM, GPU, VRAM, temperature, disk, network — live over WebSocket, with AMD GPU support via `amd-smi`.
Set thresholds and get pinged on Discord, Slack, or any webhook.

**📦 Launch apps that outlive your session**
Register Python scripts, shell scripts, binaries, existing systemd services, or URLs. Start, stop, tail logs,
health-check. Everything runs as a systemd user unit, so closing your browser — or your SSH session — doesn't kill it.

**⌨️ A terminal that never forgets**
tmux-backed sessions survive page reloads, network drops, and even a backend restart. On iPhone you get a helper
key bar, copy/paste sheets, and a scroll-history rail along the edge.

**🖥 Your desktop in a browser tab**
Headless RDP through guacd + xrdp. On touch devices it becomes a trackpad: tap to click, hold and drag,
two fingers for right-click and scroll, three to summon the keyboard.

**🔀 Build automations you can actually trust**
A React Flow canvas with **61 node types** — app control, branching, loops, HTTP, scraping, browser automation,
LLM, RAG, Deep Research, OCR, databases, SSH, Git, human approval, and more. Then the part that matters:
pre-flight checks before anything executes, draft runs separate from your published version, single-node replays,
per-node timing and token traces, and regression tests you can re-run after every change.

**🤖 Local LLMs, properly managed**
Ollama, llama.cpp, and any OpenAI-compatible endpoint in one screen. Each GGUF gets its own systemd unit with
independent context, K/V cache, GPU offload, and sampling settings. Chat, workflows, and coding agents all connect
through **one gateway** (`/api/v1/llm/v1`) that resolves models, starts them on demand, and holds requests when the
KV pool is full — so three tabs generating at once won't take each other down.

**📚 Research that cites its sources**
Six chunking strategies, vector / full-text / hybrid / graph search, HyDE and multi-query. Deep Research plans,
asks sub-questions, searches in rounds, re-checks coverage, and writes a report with citations — pulling from the web,
PDFs, academic databases, GitHub, your own RAG collections, local code, patents, and market filings.

**📦 Ship a workflow as a single file**
App Studio exports any workflow to a `.pyz` or a standalone binary in a second or two. The only thing the target
machine needs is `python3`. Project Lab picks up your Python / Node / static web / CMake / Rust / .NET projects,
previews the artifacts they produce, and runs only the profiles you declared yourself.

**🔒 Locked down by default**
Never runs as root. File access is confined to roots you allow. Arbitrary command execution is off unless you turn it on.
Role-based access, TOTP two-factor, audit logs, CSRF and origin checks, and secrets encrypted at rest.

## Requirements

- Ubuntu 24.04+ (needs a systemd user session — 22.04 ships Python and Node too old)
- Python 3.11+ and Node.js 18+ — `deck.sh` offers to apt-install them if they're missing

## Everyday commands

```bash
./deck.sh service            # register as a systemd user service (starts on boot)
./deck.sh status             # is it running?
./deck.sh stop               # stop it
./deck.sh admin <name>       # add an administrator
./deck.sh passwd <name>      # change a password
./deck.sh reset-totp <name>  # recover from a 2FA lockout
./deck.sh backup             # back up DB, config, and units
./deck.sh restore <file>     # restore from a backup
./deck.sh enable-desktop     # turn on headless remote desktop for this machine
./deck.sh searxng            # install SearXNG for Deep Research
./deck.sh test               # run the backend test suite
```

Run `./deck.sh` again after changing code and it reloads the running service.

## Configuration

Everything lives in `config/config.yaml` (see `config/config.example.yaml`).

| Key | What it does |
|---|---|
| `server.host` / `server.port` | Where to listen. Defaults to `127.0.0.1:8765` |
| `files.allowed_roots` | The only paths file operations can touch |
| `data_dir` | Where the database, logs, and models live |
| `codedev_dir` | Project folder for OpenCode and Project Lab. Defaults to `CodeDEV` next to `data_dir` |
| `git_apps_dir` | Where GitHub-managed repositories are cloned |

**Reaching it from your phone:** change `server.host`, then connect over Tailscale or WireGuard.
Don't expose this to the open internet.

**Postgres instead of SQLite:** `./deck.sh database postgresql`. It verifies the connection first, backs up your
current SQLite database, and stores credentials in `config/database.env` (mode 0600) — never in a unit file, YAML, or log.

## Optional: coding agents

OpenCode is off until you ask for it. When you do, it wires itself up — gateway endpoint, API key, and all.

```bash
./deck.sh feature install opencode
./deck.sh feature enable opencode
```

It shows up as its own screen and as a `code.agent` workflow node. The default model is `auto`, meaning requests
go to whichever model is already loaded instead of spinning up a second one. `implement` and `fix` rewrite files,
so commit first. Turn it off with `feature disable`, remove it with `feature uninstall`.

You can also register standalone web apps as GUI plugins — see the [Plugin SDK](docs/plugin-sdk.md).

## Layout

| Directory | Contents |
|---|---|
| `backend/` | FastAPI backend (auth, apps, monitoring, files, terminals, workflows, remote, GitHub, LLM) |
| `frontend/` | React + TypeScript + Vite frontend (PWA) |
| `scripts/` | Helper scripts (compatibility wrappers for `deck.sh`) |
| `deploy/` | systemd units and reverse proxy examples |
| `docs/` | Requirements, design notes, and implementation status |

## Docs

Written in Japanese. [Implementation status](docs/implementation-status.md) is the most current record of what works.

- [Requirements](docs/requirements.md) · [Architecture](docs/architecture.md) · [Implementation plan](docs/implementation-plan.md)
- [Security model](docs/security-model.md) · [UI/UX guidelines](docs/ui-ux-guidelines.md) · [Mobile layout](docs/mobile-layout.md)
- Workflows: [node catalog](docs/design-workflow-node-catalog.md) · [dry-run & metadata](docs/design-workflow-dry-run-metadata.md) · [integrated IDE](docs/design-workflow-integrated-ide.md)
- [Published apps & Project Lab](docs/design-workflow-runner-project-lab.md) · [App Studio](docs/design-application-builder.md) · [OpenCode feature](docs/design-opencode-feature.md)

## License

MIT
