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

Requires zotero-mcp-server installed as a `zotero-cli` command (e.g.
`uv tool install zotero-mcp-server`), with Zotero's "Local API" enabled
in Zotero's Advanced preferences.
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
_ILLEGAL_FILENAME_CHARS = re.compile(r'[/\\:*?"<>|]')
_KEY_COMMENT_RE = re.compile(r"<!-- key: ([A-Za-z0-9]+) -->")

# Already captured under their own frontmatter keys elsewhere, or too long
# / noisy to belong in frontmatter (abstract goes in the body; file leaks
# an absolute local path and duplicates pdf_path).
BIBTEX_SKIP_FIELDS = {"file", "abstract", "author", "title", "keywords", "year", "doi"}


def load_state() -> dict:
    """State maps key -> {date_modified, filename}. Transparently upgrades
    the older flat {key: date_modified} format some early notes may have
    used (filename == "<key>.md" then)."""
    if not STATE_PATH.exists():
        return {}
    raw = json.loads(STATE_PATH.read_text())
    migrated = {}
    for key, value in raw.items():
        if isinstance(value, str):
            migrated[key] = {"date_modified": value, "filename": f"{key}.md"}
        else:
            migrated[key] = value
    return migrated


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


def sanitize_tag(tag: str) -> str:
    """Obsidian tags can't contain spaces (or they silently stop working)."""
    return re.sub(r"\s+", "-", tag.strip())


def yaml_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def yaml_list(values: list[str]) -> str:
    if not values:
        return "[]"
    return "[" + ", ".join(yaml_str(v) for v in values) + "]"


def _slugify_part(text: str, max_len: int) -> str:
    text = _ILLEGAL_FILENAME_CHARS.sub("-", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len].rstrip() if len(text) > max_len else text


def _author_part(creators: list[str]) -> str:
    # creators are "Lastname Firstname" (see render_note) -> first token = surname
    surnames = [c.strip().split(" ")[0] for c in creators if c.strip()]
    if not surnames:
        return ""
    if len(surnames) == 1:
        return surnames[0]
    if len(surnames) == 2:
        return f"{surnames[0]} and {surnames[1]}"
    return f"{surnames[0]} et al."


def make_filename(key: str, creators: list[str], year: str, title: str, taken: dict[str, str]) -> str:
    """Human-searchable filename, mirroring Zotero's own default attachment
    naming (Author - Year - Title). `taken` maps filename -> owning key, so a
    genuine collision (two items that render to the same name) gets
    disambiguated instead of one silently overwriting the other."""
    year_match = re.match(r"(\d{4})", year or "")
    parts = [p for p in [
        _author_part(creators),
        year_match.group(1) if year_match else "",
        _slugify_part(title or "no title", 120),
    ] if p]
    base = _slugify_part(" - ".join(parts) or key, 180)
    candidate = f"{base}.md"
    if taken.get(candidate, key) == key:
        return candidate
    return f"{base} ({key}).md"


def parse_bibtex(text: str) -> tuple[str, str, dict[str, str]]:
    """Parse a single BibTeX entry. Handles brace-nested values (abstracts
    etc. can contain `{}`) and bare (unbraced) values like `month = jan`."""
    m = re.match(r"@(\w+)\{([^,]+),\s*", text)
    if not m:
        return "", "", {}
    entry_type, citekey = m.group(1), m.group(2).strip()
    rest = text[m.end():]
    fields: dict[str, str] = {}
    pos = 0
    while pos < len(rest):
        fm = re.match(r"\s*(\w+)\s*=\s*", rest[pos:])
        if not fm:
            break
        name = fm.group(1)
        pos += fm.end()
        if pos < len(rest) and rest[pos] == "{":
            depth, start = 0, pos
            while pos < len(rest):
                if rest[pos] == "{":
                    depth += 1
                elif rest[pos] == "}":
                    depth -= 1
                    if depth == 0:
                        pos += 1
                        break
                pos += 1
            value = rest[start + 1:pos - 1]
        else:
            end_m = re.search(r"[,}]", rest[pos:])
            end = pos + end_m.start() if end_m else len(rest)
            value = rest[pos:end]
            pos = end
        fields[name] = value.strip()
        comma_m = re.match(r"\s*,\s*", rest[pos:])
        if comma_m:
            pos += comma_m.end()
        else:
            break
    return entry_type, citekey, fields


def fetch_bibtex(key: str) -> tuple[str, str, dict[str, str], str] | None:
    """Full BibTeX entry for `key` (uses Better BibTeX's citekey if that
    plugin is installed in Zotero, else Zotero's own bibtex export).
    Returns (entry_type, citekey, fields, raw_bibtex_text) or None."""
    res = cli_json("export", "--item-keys", key, "--format", "bibtex")
    if not res or not res.get("ok"):
        return None
    block = res.get("data", {}).get("bibliography", "")
    m = re.search(r"```bibtex\n(.*?)\n```", block, re.DOTALL)
    if not m:
        return None
    raw = m.group(1)
    entry_type, citekey, fields = parse_bibtex(raw)
    if not citekey:
        return None
    return entry_type, citekey, fields, raw


def render_note(key: str, meta: dict, pdf_path: str | None, deep_dive: str) -> tuple[str, list[str], str, str]:
    data = meta["data"]["data"]
    title = data.get("title") or "(no title)"
    creators = [
        " ".join(filter(None, [c.get("lastName"), c.get("firstName")])) or c.get("name", "")
        for c in data.get("creators", [])
    ]
    year = data.get("date", "")
    doi = data.get("DOI", "")
    tags = [sanitize_tag(t.get("tag", "")) for t in data.get("tags", [])]
    collections = data.get("collections", [])
    item_type = data.get("itemType", "")
    date_added = data.get("dateAdded", "")
    date_modified = data.get("dateModified", "")
    zotero_link = f"zotero://select/library/items/{key}"
    synced_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    bib = fetch_bibtex(key)
    if bib:
        bibtex_type, citekey, bibtex_fields, raw_bibtex = bib
    else:
        bibtex_type, citekey, bibtex_fields, raw_bibtex = item_type, "", {}, ""

    fm_lines = [
        "---",
        f"zotero_key: {key}",
        f"citekey: {citekey}",
        f'title: "{title}"',
        f"creators: {yaml_list(creators)}",
        f'year: "{year}"',
        f"item_type: {item_type}",
        f"bibtex_type: {bibtex_type}",
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
    ]
    # Citation fields a .bib entry would carry (journal, volume, pages,
    # publisher, isbn, url, ...) -- whatever Zotero/Better BibTeX gave us,
    # minus what's already captured above under its own key.
    for field_name in sorted(bibtex_fields):
        if field_name in BIBTEX_SKIP_FIELDS:
            continue
        fm_lines.append(f"{field_name}: {yaml_str(bibtex_fields[field_name])}")
    fm_lines.append("---")
    fm = "\n".join(fm_lines)

    pdf_line = f"\n**PDF:** `{pdf_path}`" if pdf_path else ""
    abstract = data.get("abstractNote") or "_(none)_"
    citation_block = (
        f"\n## Citation (BibTeX)\n\n```bibtex\n{raw_bibtex}\n```\n"
        if raw_bibtex else
        "\n## Citation (BibTeX)\n\n_(BibTeX unavailable)_\n"
    )

    body = f"""
# {title}

**Authors:** {', '.join(creators) or '-'}
**Year:** {year or '-'}
**DOI:** {doi or '-'}
**Open in Zotero:** [{key}]({zotero_link}){pdf_line}

## Abstract

{abstract}
{citation_block}
{METADATA_END_MARKER}
"""
    return fm + body, creators, year, title


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


def upsert_note(key: str, meta: dict, pdf_path: str | None, state: dict, taken: dict[str, str]) -> tuple[str, str]:
    """Write/update the note for `key`. Returns (filename, deep_dive_status)."""
    prev = state.get(key, {})
    old_filename = prev.get("filename")
    old_path = PAPERS_DIR / old_filename if old_filename else None

    old_status = existing_deep_dive_status(old_path) if old_path else None
    if old_status in ("pending", "done"):
        deep_dive = old_status
    else:
        deep_dive = "pending" if pdf_path else "n/a"

    head, creators, year, title = render_note(key, meta, pdf_path, deep_dive)
    new_filename = make_filename(key, creators, year, title, taken)
    new_path = PAPERS_DIR / new_filename

    if old_path and old_path.exists():
        old_text = old_path.read_text()
        idx = old_text.find(METADATA_END_MARKER)
        tail = old_text[idx + len(METADATA_END_MARKER):] if idx != -1 else new_file_tail(bool(pdf_path))
        if old_path != new_path:
            old_path.unlink()
    else:
        tail = new_file_tail(bool(pdf_path))

    new_path.write_text(head + tail)
    taken[new_filename] = key
    return new_filename, deep_dive


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


def update_queue(pending: dict[str, str]) -> None:
    """pending: key -> filename, for items newly promoted to the queue."""
    existing_keys = set()
    lines: list[str] = []
    if QUEUE_PATH.exists():
        for line in QUEUE_PATH.read_text().splitlines():
            m = _KEY_COMMENT_RE.search(line)
            if m:
                existing_keys.add(m.group(1))
            lines.append(line)
    else:
        lines.append("# Deep-Dive Queue")
        lines.append("")
        lines.append("Papers with a PDF that don't have a structured summary yet.")
        lines.append("Remove a line once `.claude/skills/process-queue` has processed it.")
        lines.append("")

    new_keys = {k: f for k, f in pending.items() if k not in existing_keys}
    for key, filename in sorted(new_keys.items(), key=lambda kv: kv[1]):
        lines.append(f"- [ ] [[papers/{filename}]] <!-- key: {key} -->")

    if new_keys:
        QUEUE_PATH.write_text("\n".join(lines) + "\n")


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None

    reader = LocalZoteroReader()
    all_items = reader.get_items_with_text(limit=None)
    reader.close()

    state = load_state()
    taken = {v["filename"]: k for k, v in state.items() if v.get("filename")}
    changed = [
        it for it in all_items
        if state.get(it.key, {}).get("date_modified") != it.date_modified
    ]
    if limit:
        changed = changed[:limit]

    print(f"library items: {len(all_items)} | changed since last sync: {len(changed)}")

    pending_for_queue: dict[str, str] = {}
    ok, failed = 0, 0
    for i, item in enumerate(changed, 1):
        meta = cli_json("get", "metadata", item.key)
        if not meta or not meta.get("ok"):
            failed += 1
            continue
        pdf_path = resolve_pdf_path(item.key)
        filename, deep_dive = upsert_note(item.key, meta, pdf_path, state, taken)
        if deep_dive == "pending":
            pending_for_queue[item.key] = filename
        state[item.key] = {"date_modified": item.date_modified, "filename": filename}
        ok += 1
        if i % 50 == 0:
            print(f"  ...{i}/{len(changed)}")
            save_state(state)

    update_queue(pending_for_queue)
    save_state(state)
    print(f"done: {ok} synced, {failed} failed (failures usually mean Zotero.app isn't running)")


if __name__ == "__main__":
    main()
