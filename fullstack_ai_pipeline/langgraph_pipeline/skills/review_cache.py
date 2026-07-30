"""
Per-project cache of the LLM code reviewer's last verdict on each file, keyed
by content hash - lets the Testing agent's review_code tool skip re-sending
files that haven't changed since the last testing pass. Without this, every
testing round re-sends every backend/frontend file to a second LLM call
regardless of whether anything actually changed, and that cost scales with
both project size and number of testing rounds (which can run up to 20 times
per build/update).
"""

import hashlib
import json
from pathlib import Path


def _cache_path(project_dir: Path) -> Path:
    return project_dir / ".review_cache.json"


def hash_content(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()


def load_review_cache(project_dir: Path) -> dict:
    """Returns {file_path: {"hash": "...", "issues": [...]}}."""
    path = _cache_path(project_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_review_cache(project_dir: Path, cache: dict) -> None:
    _cache_path(project_dir).write_text(json.dumps(cache, indent=2), encoding="utf-8")


def split_changed_files(all_files: dict, cache: dict) -> tuple[dict, dict, dict]:
    """
    Given {path: content} and the previous cache, returns:
      (changed_files, current_hashes, cached_issues_for_unchanged)
    - changed_files: {path: content} for files that are new or whose content
      hash doesn't match the cache - these are what actually needs sending
      to the LLM reviewer.
    - current_hashes: {path: hash} for every file, used to rebuild the cache.
    - cached_issues_for_unchanged: issues to keep as-is for files that didn't
      change, reused instead of re-asking the LLM about them.
    """
    changed_files = {}
    current_hashes = {}
    cached_issues_for_unchanged = []

    for path, content in all_files.items():
        h = hash_content(content)
        current_hashes[path] = h
        cached_entry = cache.get(path)
        if cached_entry and cached_entry.get("hash") == h:
            cached_issues_for_unchanged.extend(cached_entry.get("issues", []))
        else:
            changed_files[path] = content

    return changed_files, current_hashes, cached_issues_for_unchanged


def rebuild_cache(all_files: dict, current_hashes: dict, cache: dict,
                  changed_paths: set, new_issues_by_file: dict) -> dict:
    """
    Build the cache to save: every file currently present gets an entry
    (files no longer present are dropped automatically, since we only
    iterate all_files). changed_paths must be the exact set of files that
    were actually re-sent to the LLM this round - a changed file that came
    back clean (zero issues) still needs its cache entry overwritten with
    the new hash + empty issue list, not left pointing at its old hash with
    stale issues, so it isn't seen as "changed" again on the next pass for
    no reason.
    """
    updated = {}
    for path in all_files:
        if path in changed_paths:
            updated[path] = {"hash": current_hashes[path], "issues": new_issues_by_file.get(path, [])}
        else:
            updated[path] = cache.get(path, {"hash": current_hashes[path], "issues": []})
    return updated
