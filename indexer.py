"""
indexer.py — Walks a codebase directory, reads source files, and builds a context string.
"""

import os
import re
from pathlib import Path

import db

# Extensions considered as source/config files
SUPPORTED_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".java", ".c", ".cpp", ".h", ".cs",
    ".go", ".rs", ".rb", ".php",
    ".html", ".css", ".scss", ".sass",
    ".md", ".json", ".yaml", ".yml",
    ".toml", ".sh", ".bat", ".env",
    ".sql", ".graphql", ".proto",
}

# Directories to always skip
SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".pytest_cache",
    "dist", "build", "out", "venv", ".venv", "env",
    ".next", ".nuxt", "coverage", ".turbo", ".cache",
    "target",  # Rust/Maven
}

# Max total characters stored per codebase (≈ 300k chars ≈ 75k tokens)
MAX_CONTEXT_CHARS = 300_000

# Max chars per single file
MAX_FILE_CHARS = 50_000


def _detect_language(ext: str) -> str:
    return {
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".jsx": "jsx", ".tsx": "tsx", ".java": "java",
        ".c": "c", ".cpp": "cpp", ".h": "c",
        ".cs": "csharp", ".go": "go", ".rs": "rust",
        ".rb": "ruby", ".php": "php", ".html": "html",
        ".css": "css", ".scss": "scss", ".sh": "bash",
        ".bat": "batch", ".sql": "sql", ".md": "markdown",
        ".json": "json", ".yaml": "yaml", ".yml": "yaml",
        ".toml": "toml", ".graphql": "graphql", ".proto": "protobuf",
    }.get(ext, "text")


def _extract_symbols(content: str, language: str) -> list[str]:
    """Simple regex-based symbol extraction (functions, classes) for context summary."""
    symbols = []
    patterns = {
        "python": [
            r"^class\s+(\w+)",
            r"^def\s+(\w+)",
            r"^async\s+def\s+(\w+)",
        ],
        "javascript": [
            r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)",
            r"^(?:export\s+)?class\s+(\w+)",
            r"^(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s+)?\(",
        ],
        "typescript": [
            r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)",
            r"^(?:export\s+)?class\s+(\w+)",
            r"^(?:export\s+)?interface\s+(\w+)",
            r"^(?:export\s+)?type\s+(\w+)",
            r"^(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s+)?\(",
        ],
        "java": [r"^\s*(?:public|private|protected)?\s*(?:static\s+)?(?:\w+\s+)?(\w+)\s*\("],
        "go": [r"^func\s+(?:\([\w\s*]+\)\s+)?(\w+)"],
        "rust": [r"^(?:pub\s+)?fn\s+(\w+)"],
    }.get(language, [])

    for line in content.splitlines():
        for pat in patterns:
            m = re.match(pat, line.strip())
            if m:
                symbols.append(m.group(1))
    return symbols[:30]  # Cap at 30 symbols per file to keep context lean


def walk_codebase(root_path: str) -> list[dict]:
    """
    Walk the directory tree and collect all readable source files.
    Returns a list of dicts: {rel_path, content, language}.
    """
    root = Path(root_path).resolve()
    collected = []

    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skipped dirs in-place so os.walk doesn't recurse into them
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]

        for filename in sorted(filenames):
            filepath = Path(dirpath) / filename
            ext = filepath.suffix.lower()

            if ext not in SUPPORTED_EXTENSIONS:
                continue

            rel_path = str(filepath.relative_to(root)).replace("\\", "/")

            try:
                content = filepath.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            if len(content) > MAX_FILE_CHARS:
                content = content[:MAX_FILE_CHARS] + "\n... [truncated]"

            collected.append({
                "rel_path": rel_path,
                "content": content,
                "language": _detect_language(ext),
            })

    return collected


def build_context(root_path: str, files: list[dict]) -> str:
    """
    Build a rich context string from the collected files.
    This becomes the AI system prompt backdrop.
    """
    root = Path(root_path).resolve()
    lines = []

    # ── Header ───────────────────────────────────────────────
    lines.append(f"# Codebase: {root.name}")
    lines.append(f"Root path: {root}")
    lines.append(f"Total indexed files: {len(files)}\n")

    # ── File tree ─────────────────────────────────────────────
    lines.append("## File Tree")
    for f in files:
        lines.append(f"  {f['rel_path']}")
    lines.append("")

    # ── Per-file symbol summaries ──────────────────────────────
    lines.append("## Symbol Index")
    for f in files:
        symbols = _extract_symbols(f["content"], f["language"])
        if symbols:
            lines.append(f"### {f['rel_path']}")
            lines.append(", ".join(symbols))
    lines.append("")

    # ── Full file contents (within budget) ────────────────────
    lines.append("## File Contents")
    total_chars = len("\n".join(lines))

    for f in files:
        block = f"\n### {f['rel_path']}\n```{f['language']}\n{f['content']}\n```"
        if total_chars + len(block) > MAX_CONTEXT_CHARS:
            lines.append(f"\n[... remaining files omitted — context budget reached ...]")
            break
        lines.append(block)
        total_chars += len(block)

    return "\n".join(lines)


def index_codebase(codebase_id: int, root_path: str) -> tuple[int, str]:
    """
    Full indexing pipeline:
      1. Walk directory
      2. Store files in DB
      3. Build and store context
    Returns (file_count, context_summary).
    """
    files = walk_codebase(root_path)

    # Persist files
    db.clear_files(codebase_id)
    for f in files:
        db.add_file(codebase_id, f["rel_path"], f["content"], f["language"])

    # Build and persist context
    context = build_context(root_path, files)
    db.save_context(codebase_id, context)

    return len(files), context
