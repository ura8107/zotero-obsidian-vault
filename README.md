# zotero-obsidian-vault

Mirror your whole Zotero library into an Obsidian vault, one note per item, kept in sync automatically.
Papers that actually have a PDF get queued for an LLM (via a Claude Code skill included here) to fill in
a structured reading summary — theory, prior work, research design, data, method, results, implications.

Why this exists: most "Zotero + Obsidian" plugins either duplicate PDFs into your vault or only sync
metadata. This keeps your vault as the single place you read/search/link your library from, while leaving
Zotero as the source of truth for the actual files — and treats "summarize this paper" as what it is,
an LLM judgment call, not something a sync script should fake.

## How it works

Two tiers, see [CLAUDE.md](CLAUDE.md) for the full design:

1. **Metadata** for every item — fully automatic, no LLM, safe to run on a timer.
2. **Structured summary** for items with a PDF — queued in `queue.md`, processed by an LLM
   (`.claude/skills/process-queue` if you use Claude Code) in batches you control.

Re-syncing never destroys your own notes or a completed summary: every note has a clear boundary
(`<!-- ZOTERO-SYNC:METADATA:END -->`) between the part the sync script owns and the part you own.

## Setup

1. Install [zotero-mcp-server](https://github.com/) so you have a `zotero-cli` command:
   ```
   uv tool install zotero-mcp-server
   ```
   (or however that project currently recommends — check its own docs; this repo just consumes the CLI).
2. In Zotero: Settings → Advanced → check "Allow other applications on this computer to communicate with
   Zotero" (this is what lets `zotero-cli`/`sync.py` talk to `localhost:23119`).
3. Clone this repo as your vault, or copy its contents into an existing Obsidian vault:
   ```
   git clone <this-repo> ~/my-papers-vault
   cd ~/my-papers-vault
   ```
4. Run a small test sync first (Zotero.app must be open):
   ```
   python3 scripts/sync.py 5
   ```
   Check `papers/` — you should see 5 new notes.
5. Run the full sync (can take a while for a large library — it's one API call per new/changed item):
   ```
   python3 scripts/sync.py
   ```
6. (macOS) Install the daily auto-sync:
   ```
   ./launchd/install.sh
   ```
   On Linux, add the equivalent as a cron job or systemd timer calling `scripts/sync.py`.
7. Open the folder in Obsidian.

## Processing the deep-dive queue

With Claude Code, open this vault's folder and say something like "process the queue" — the
`process-queue` skill will work through `queue.md` a few papers at a time. Using a different agent?
`.claude/skills/process-queue/SKILL.md` is plain instructions; adapt it to whatever you use.

## License

MIT — see [LICENSE](LICENSE).
