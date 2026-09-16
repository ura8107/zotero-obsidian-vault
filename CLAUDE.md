# Zotero → Obsidian Vault

Mirrors an entire Zotero library into an Obsidian vault (`papers/`, one note per item), then lets an LLM
fill in a structured reading summary for whichever items actually have a PDF.

## Two-tier pipeline

1. **Metadata tier (every item, fully automatic, no LLM)**: `scripts/sync.py` diffs the whole library
   fast via Zotero's local SQLite DB, then fetches proper metadata for changed items through `zotero-cli`'s
   local API (Zotero.app must be running for that part) and writes `papers/<zotero_key>.md`.
   Run it on a schedule — see `launchd/` for a macOS example — or just run it by hand.
2. **Deep-dive tier (PDF-bearing items only, needs an LLM)**: reading the full text and writing a structured
   summary (Theory / Prior Work / Research Design / Data / Method / Results / Implications) is real judgment,
   not something a cron job should do. Items land in `queue.md`; run the `.claude/skills/process-queue`
   skill from Claude Code (or adapt it to whatever agent you use) to work through it.

## Note anatomy (`papers/<zotero_key>.md`)

```
---
frontmatter (sync.py overwrites every run)
---
Title / authors / DOI / Zotero link / Abstract (sync.py overwrites every run)

<!-- ZOTERO-SYNC:METADATA:END -->

## Zotero Notes (verbatim)   <- process-queue skill writes this; sync.py never touches it
## Structured Summary        <- same
## My Notes                  <- yours. Nothing automated ever touches this.
```

**Everything above `<!-- ZOTERO-SYNC:METADATA:END -->` belongs to `sync.py`. Everything below belongs to
you or the deep-dive skill.** Breaking that boundary means re-syncing silently destroys hand-written content.

## PDFs

PDFs are never copied into the vault. `zotero-cli path <key>` resolves the real file location on disk —
works the same whether Zotero stores the file itself or it's a linked file synced elsewhere (Google Drive,
Dropbox, a network share...) — and that path is recorded in the note's `pdf_path` frontmatter field.

## Setup

See [README.md](README.md).

## Known limitations

- The metadata-fetch half of `sync.py` needs Zotero.app running (it talks to `localhost:23119`). If it
  isn't, that run's changed items simply aren't marked as synced and get picked up the next time it runs
  with Zotero open — no data is lost.
- Most Zotero libraries are mostly bare bibliographic records with no attached PDF. Those get a metadata-only
  note and stay out of the deep-dive queue until a PDF shows up for them.
