#!/usr/bin/env python3
"""Sync a Zotero library into Obsidian notes (metadata tier only).

Two data paths:
  1. Bulk diff via Zotero's local SQLite DB (no Zotero.app required) --
     fast enough to run against the whole library every time.
  2. Per-changed-item detail via `zotero-cli`'s local HTTP API, which
     needs Zotero.app running. Only items whose dateModified changed
     since the last run pay this cost.

Deep-dive structured summaries (theory / prior work / research design /
data / method / results / implications) are NOT written here -- that's
an LLM job. See .claude/skills/process-queue and queue.md.

Requires: https://github.com/ has zotero-mcp-server installed as a
`zotero-cli` command (e.g. `uv tool install zotero-mcp-server`), with
Zotero's "Local API" enabled in Zotero's Advanced preferences.
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ZOTERO_CLI = Path.home() / ".local" / "bin" / "zotero-cli"


def _reexec_under_zotero_cli_interpreter() -> None:
    """`LocalZoteroReader` lives inside zotero-cli's own venv, not on the
    system Python's path. Rather than hardcoding that venv's path (which
    differs per machine), read it off zotero-cli's own shebang line and
    re-exec this script under the same interpreter."""
    if not ZOTERO_CLI.exists():
        sys.exit(f"zotero-cli not found at {ZOTERO_CLI}. Install zotero-mcp-server first.")
    first_line = ZOTERO_CLI.read_text().splitlines()[0]
    if not first_line.startswith("#!"):
        sys.exit(f"Could not determine zotero-cli's Python interpreter from {ZOTERO_CLI}")
    interpreter = first_line[2:].strip()
    os.execv(interpreter, [interpreter, __file__, *sys.argv[1:]])


try:
    from zotero_mcp.local_db import LocalZoteroReader
except ImportError:
    _reexec_under_zotero_cli_interpreter()
    raise  # unreachable; execv replaces the process

VAULT = Path(__file__).resolve().parent.parent
PAPERS_DIR = VAULT / "papers"
STATE_PATH = VAULT / ".zotero-sync" / "state.json"
QUEUE_PATH = VAULT / "queue.md"
METADATA_END_MARKER = "<!-- ZOTERO-SYNC:METADATA:END -->"


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def cli_json(*args: str) -> dict | None:
    try:
        out = subprocess.run(
            [str(ZOTERO_CLI), "--json", *args],
            capture_output=True, text=True, timeout=30, check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return None


def existing_deep_dive_status(path: Path) -> str | None:
    """Read the `deep_dive:` frontmatter value from an existing note, if any."""
    if not path.exists():
        return None
    m = re.search(r"^deep_dive:\s*(\S+)", path.read_text(), re.MULTILINE)
    return m.group(1) if m else None


def yaml_list(values: list[str]) -> str:
    if not values:
        return "[]"
    return "[" + ", ".join(json.dumps(v, ensure_ascii=False) for v in values) + "]"


def render_note(key: str, meta: dict, pdf_path: str | None, deep_dive: str) -> str:
    data = meta["data"]["data"]
    title = data.get("title") or "(no title)"
    creators = [
        " ".join(filter(None, [c.get("lastName"), c.get("firstName")])) or c.get("name", "")
        for c in data.get("creators", [])
    ]
    year = data.get("date", "")
    doi = data.get("DOI", "")
    tags = [t.get("tag", "") for t in data.get("tags", [])]
    collections = data.get("collections", [])
    item_type = data.get("itemType", "")
    date_added = data.get("dateAdded", "")
    date_modified = data.get("dateModified", "")
    zotero_link = f"zotero://select/library/items/{key}"
    synced_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    fm = "\n".join([
        "---",
        f"zotero_key: {key}",
        f'title: "{title}"',
        f"creators: {yaml_list(creators)}",
        f'year: "{year}"',
        f"item_type: {item_type}",
        f'doi: "{doi}"',
        f"tags: {yaml_list(tags)}",
        f"collections: {yaml_list(collections)}",
        f"date_added: {date_added}",
        f"date_modified: {date_modified}",
        f"has_pdf: {'true' if pdf_path else 'false'}",
        f'pdf_path: "{pdf_path or ""}"',
        f'zotero_link: "{zotero_link}"',
        f"deep_dive: {deep_dive}",
        f"synced_at: {synced_at}",
        "---",
    ])

    pdf_line = f"\n**PDF:** `{pdf_path}`" if pdf_path else ""
    abstract = data.get("abstractNote") or "_(none)_"

    body = f"""
# {title}

**Authors:** {', '.join(creators) or '-'}
**Year:** {year or '-'}
**DOI:** {doi or '-'}
**Open in Zotero:** [{key}]({zotero_link}){pdf_line}

## Abstract

{abstract}

{METADATA_END_MARKER}
"""
    return fm + body


def new_file_tail(has_pdf: bool) -> str:
    summary_placeholder = (
        "_(pending — see queue.md)_" if has_pdf else
        "_(no PDF, so out of scope for now. If a PDF is added in Zotero later, "
        "this is promoted to the queue automatically.)_"
    )
    return f"""
## Zotero Notes (verbatim)

_(not fetched yet)_

## Structured Summary

Theory / Prior Work / Research Design / Data / Method / Results / Implications
are filled in by the deep-dive pass (see queue.md and the process-queue skill).

{summary_placeholder}

## My Notes

<!-- Everything below this line survives re-sync. Write freely. -->
"""


def upsert_note(key: str, meta: dict, pdf_path: str | None) -> tuple[bool, str]:
    """Write/update papers/<key>.md. Returns (is_new, deep_dive_status)."""
    path = PAPERS_DIR / f"{key}.md"
    is_new = not path.exists()
    old_status = existing_deep_dive_status(path)
    if old_status in ("pending", "done"):
        deep_dive = old_status
    else:
        deep_dive = "pending" if pdf_path else "n/a"

    head = render_note(key, meta, pdf_path, deep_dive)

    if path.exists():
        old_text = path.read_text()
        idx = old_text.find(METADATA_END_MARKER)
        if idx != -1:
            tail = old_text[idx + len(METADATA_END_MARKER):]
            path.write_text(head + tail)
            return False, deep_dive
    path.write_text(head + new_file_tail(bool(pdf_path)))
    return is_new, deep_dive


_PDF_PATH_RE = re.compile(
    r"\(application/pdf\)\n- Zotero path: `[^`]*`\n- Local path: `([^`]*)`"
)


def resolve_pdf_path(key: str) -> str | None:
    """`zotero-cli path` only returns markdown text, even with --json, so
    scrape the local path for the first PDF attachment out of it. Works
    regardless of whether Zotero stores the file itself, syncs it via
    Zotero's own storage, or links to a file synced elsewhere (Google
    Drive, Dropbox, etc.) -- `zotero-cli path` already resolves that."""
    res = cli_json("path", key)
    if not res or not res.get("ok"):
        return None
    text = res.get("data", {}).get("text", "")
    m = _PDF_PATH_RE.search(text)
    return m.group(1) if m else None


def update_queue(pending_keys: set[str]) -> None:
    existing = set()
    lines: list[str] = []
    if QUEUE_PATH.exists():
        for line in QUEUE_PATH.read_text().splitlines():
            m = re.search(r"papers/([A-Za-z0-9]+)\.md", line)
            if m:
                existing.add(m.group(1))
            lines.append(line)
    else:
        lines.append("# Deep-Dive Queue")
        lines.append("")
        lines.append("Papers with a PDF that don't have a structured summary yet.")
        lines.append("Remove a line once `.claude/skills/process-queue` has processed it.")
        lines.append("")

    new_keys = pending_keys - existing
    for key in sorted(new_keys):
        lines.append(f"- [ ] [[papers/{key}.md]]")

    if new_keys:
        QUEUE_PATH.write_text("\n".join(lines) + "\n")


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None

    reader = LocalZoteroReader()
    all_items = reader.get_items_with_text(limit=None)
    reader.close()

    state = load_state()
    changed = [
        it for it in all_items
        if state.get(it.key) != it.date_modified
    ]
    if limit:
        changed = changed[:limit]

    print(f"library items: {len(all_items)} | changed since last sync: {len(changed)}")

    pending_for_queue: set[str] = set()
    ok, failed = 0, 0
    for i, item in enumerate(changed, 1):
        meta = cli_json("get", "metadata", item.key)
        if not meta or not meta.get("ok"):
            failed += 1
            continue
        pdf_path = resolve_pdf_path(item.key)
        _, deep_dive = upsert_note(item.key, meta, pdf_path)
        if deep_dive == "pending":
            pending_for_queue.add(item.key)
        state[item.key] = item.date_modified
        ok += 1
        if i % 50 == 0:
            print(f"  ...{i}/{len(changed)}")
            save_state(state)

    update_queue(pending_for_queue)
    save_state(state)
    print(f"done: {ok} synced, {failed} failed (failures usually mean Zotero.app isn't running)")


if __name__ == "__main__":
    main()
