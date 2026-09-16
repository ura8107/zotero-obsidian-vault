---
name: process-queue
description: Process papers synced from Zotero that have a PDF but no structured summary yet. Use for "process the queue", "summarize these papers", "run the deep dive".
---

Work through `queue.md`, turning each queued paper into a note with a structured summary.

## Steps

1. Read `queue.md` and pick a `- [ ] [[papers/<KEY>.md]]` line (process however many the user asked for; default to a handful at a time and check in before continuing).
2. Open `papers/<KEY>.md` and read `zotero_key` from its frontmatter.
3. Get the full text: `zotero-cli read <KEY>` or `zotero-cli get fulltext <KEY>` (use page ranges for long PDFs).
4. Get any existing Zotero notes: `zotero-cli notes get <KEY>` (check `zotero-cli notes --help` for the exact subcommand). If there's content, transcribe it into "## Zotero Notes (verbatim)". If there's nothing, leave the placeholder.
5. Read the paper and fill in "## Structured Summary" with these seven sections. Write "not applicable" rather than forcing content into a section that doesn't fit the paper:
   - Theory
   - Prior Work
   - Research Design
   - Data
   - Method
   - Results
   - Implications
6. **Never edit anything above `<!-- ZOTERO-SYNC:METADATA:END -->`** (frontmatter included) — `sync.py` regenerates that block on every run and will discard hand edits there. Likewise never touch anything under "## My Notes" — that's the user's space. Only "## Zotero Notes (verbatim)" and "## Structured Summary" are yours to write.
7. Flip the note's frontmatter `deep_dive:` from `pending` to `done` by hand. `sync.py` never downgrades a `done` back to `pending`, but it also never sets it to `done` for you -- if you skip this step the paper stays queued forever.
8. Delete the line for this paper from `queue.md`.

## Notes

- Don't process the whole queue in one pass unless asked; work in batches (default ~5) and check in.
- If full-text extraction fails (e.g. a scanned PDF with no OCR layer), leave the line in `queue.md` and tell the user why instead of silently skipping it.
