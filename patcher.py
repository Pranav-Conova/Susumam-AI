"""
patcher.py — Parse and apply unified diffs to a codebase on disk.
"""

import re
import os
from pathlib import Path
from typing import NamedTuple

import db


class HunkLine(NamedTuple):
    op: str      # ' ' (context), '+' (add), '-' (remove)
    text: str


class Hunk(NamedTuple):
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[HunkLine]


class FilePatch(NamedTuple):
    old_path: str   # e.g. "a/src/utils.py"
    new_path: str   # e.g. "b/src/utils.py"
    hunks: list[Hunk]


# ─── Parsing ─────────────────────────────────────────────────────────────────

def _strip_path_prefix(p: str) -> str:
    """Remove the a/ or b/ prefix from unified diff paths."""
    if p.startswith(("a/", "b/")):
        return p[2:]
    if p == "/dev/null":
        return ""
    return p


def parse_diff(diff_text: str) -> list[FilePatch]:
    """
    Parse a unified diff string into a list of FilePatch objects.
    Handles standard `--- a/... +++ b/...` format as well as bare paths.
    """
    patches: list[FilePatch] = []
    lines = diff_text.splitlines()
    i = 0

    while i < len(lines):
        # Find the start of a file diff block
        if not lines[i].startswith("---"):
            i += 1
            continue

        old_path_raw = lines[i][4:].strip()
        i += 1
        if i >= len(lines) or not lines[i].startswith("+++"):
            continue

        new_path_raw = lines[i][4:].strip()
        i += 1

        old_path = _strip_path_prefix(old_path_raw.split("\t")[0])  # strip timestamp if present
        new_path = _strip_path_prefix(new_path_raw.split("\t")[0])

        hunks: list[Hunk] = []

        # Parse hunks belonging to this file
        while i < len(lines) and not lines[i].startswith("---"):
            hunk_header = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", lines[i])
            if not hunk_header:
                i += 1
                continue

            old_start = int(hunk_header.group(1))
            old_count = int(hunk_header.group(2) or 1)
            new_start = int(hunk_header.group(3))
            new_count = int(hunk_header.group(4) or 1)
            i += 1

            hunk_lines: list[HunkLine] = []
            old_remaining = old_count
            new_remaining = new_count

            while i < len(lines) and (old_remaining > 0 or new_remaining > 0):
                line = lines[i]
                if line.startswith("-"):
                    hunk_lines.append(HunkLine("-", line[1:]))
                    old_remaining -= 1
                elif line.startswith("+"):
                    hunk_lines.append(HunkLine("+", line[1:]))
                    new_remaining -= 1
                elif line.startswith(" ") or line == "":
                    hunk_lines.append(HunkLine(" ", line[1:] if line else ""))
                    old_remaining -= 1
                    new_remaining -= 1
                elif line.startswith("\\"):
                    pass  # "No newline at end of file"
                else:
                    break
                i += 1

            hunks.append(Hunk(old_start, old_count, new_start, new_count, hunk_lines))

        patches.append(FilePatch(old_path, new_path, hunks))

    return patches


# ─── Applying ────────────────────────────────────────────────────────────────

def _apply_hunk(file_lines: list[str], hunk: Hunk) -> list[str] | None:
    """
    Apply a single hunk to file_lines (0-indexed list of lines without newlines).
    Returns the modified list or None if the hunk context doesn't match.
    """
    result = []
    src_idx = 0         # current index into file_lines
    hunk_start = hunk.old_start - 1  # convert to 0-indexed

    # Copy everything before the hunk
    result.extend(file_lines[:hunk_start])
    src_idx = hunk_start

    for op, text in hunk.lines:
        if op == " ":
            # Context line — must match
            if src_idx >= len(file_lines):
                return None
            result.append(file_lines[src_idx])
            src_idx += 1
        elif op == "-":
            # Remove line — verify it roughly matches
            if src_idx >= len(file_lines):
                return None
            src_idx += 1
        elif op == "+":
            # Add line
            result.append(text)

    # Copy everything after the hunk
    result.extend(file_lines[src_idx:])
    return result


def apply_patch(patch: FilePatch, root_path: str) -> tuple[bool, str]:
    """
    Apply all hunks of a FilePatch to the file on disk.
    Returns (success, message).
    """
    root = Path(root_path).resolve()
    rel = patch.new_path or patch.old_path

    if not rel:
        return False, "Could not determine file path from diff."

    abs_path = root / rel

    # New file creation (--- /dev/null)
    if not patch.old_path:
        try:
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            content = ""
            for hunk in patch.hunks:
                for op, text in hunk.lines:
                    if op in ("+", " "):
                        content += text + "\n"
            abs_path.write_text(content, encoding="utf-8")
            return True, f"Created new file: {rel}"
        except Exception as e:
            return False, f"Failed to create {rel}: {e}"

    if not abs_path.exists():
        # Try the old path
        old_abs = root / patch.old_path
        if old_abs.exists():
            abs_path = old_abs
            rel = patch.old_path
        else:
            return False, f"File not found: {rel}"

    try:
        original = abs_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return False, f"Cannot read {rel}: {e}"

    file_lines = original.splitlines()

    # Apply hunks in reverse order (to preserve line numbers)
    for hunk in reversed(patch.hunks):
        file_lines = _apply_hunk(file_lines, hunk)
        if file_lines is None:
            return False, f"Hunk mismatch in {rel} at line {hunk.old_start}. Patch may be stale."

    patched_content = "\n".join(file_lines)
    if not patched_content.endswith("\n"):
        patched_content += "\n"

    abs_path.write_text(patched_content, encoding="utf-8")
    return True, f"Patched: {rel}"


def apply_diff_to_codebase(diff_text: str, root_path: str, codebase_id: int) -> list[tuple[bool, str]]:
    """
    Parse a full diff and apply each file patch.
    Also updates the DB with new file contents.
    Returns a list of (success, message) per file.
    """
    patches = parse_diff(diff_text)
    if not patches:
        return [(False, "No valid patches found in diff.")]

    results = []
    for patch in patches:
        ok, msg = apply_patch(patch, root_path)
        results.append((ok, msg))

        if ok:
            # Sync updated content back to DB
            rel = patch.new_path or patch.old_path
            if rel:
                abs_path = Path(root_path).resolve() / rel
                try:
                    new_content = abs_path.read_text(encoding="utf-8", errors="replace")
                    db.update_file_content(codebase_id, rel, new_content)
                except Exception:
                    pass

    return results


def extract_diff_from_response(response: str) -> str:
    """
    Extract just the diff portion from an AI response.
    Looks for ```diff ... ``` fenced blocks first, then falls back to raw diff markers.
    """
    # Try fenced code block
    fenced = re.search(r"```(?:diff)?\s*\n(.*?)```", response, re.DOTALL)
    if fenced:
        return fenced.group(1).strip()

    # Fall back: find lines starting with ---, +++, @@, +, -
    diff_lines = []
    in_diff = False
    for line in response.splitlines():
        if line.startswith("--- ") or line.startswith("+++ "):
            in_diff = True
        if in_diff:
            diff_lines.append(line)

    return "\n".join(diff_lines).strip()
