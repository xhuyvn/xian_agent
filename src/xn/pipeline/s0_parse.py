"""Stage S0: Parse and clean raw chapter files."""

from __future__ import annotations

import collections
from dataclasses import dataclass
import hashlib
import logging
from pathlib import Path
import re
from typing import Any
from markdown_it import MarkdownIt

from xn.storage.repository import Repository
from xn.text import estimate_tokens, normalize_text

logger = logging.getLogger(__name__)


class ChapterSetError(ValueError):
    """Raised when chapter files have missing or duplicate chapter numbers."""

    def __init__(self, missing: list[int], duplicates: list[int]) -> None:
        self.missing = missing
        self.duplicates = duplicates
        super().__init__(
            f"Invalid chapter set - missing: {missing}, duplicates: {duplicates}"
        )


@dataclass(frozen=True)
class S0Result:
    """Counts returned by run_s0."""

    processed: int
    written: int
    skipped: int


def _extract_title(body: str, chapter_id: int) -> str:
    """Extract chapter title from stripped body using fallback rules.

    1. First heading matching ^#{1,3}\s*(.+)$ in multiline mode.
    2. Else first line matching ^Chương\s+\d+.
    3. Else Chương N.
    """
    m = re.search(r"^#{1,3}\s*(.+)$", body, flags=re.MULTILINE)
    if m:
        return m.group(1).strip()

    m = re.search(r"^(Chương\s+\d+.*)$", body, flags=re.MULTILINE)
    if m:
        return m.group(1).strip()

    return f"Chương {chapter_id}"


def _markdown_to_plain_text(markdown_text: str) -> str:
    """Convert Markdown to plain text with markdown-it-py token walking.

    - Keep heading and paragraph text.
    - Separate blocks with a blank line.
    - Drop images, links (with their text) and HTML blocks / inline HTML.
    - Treat soft breaks as newlines.
    """
    md = MarkdownIt()
    tokens = md.parse(markdown_text)

    blocks: list[str] = []
    current_block_parts: list[str] = []
    in_block = False
    link_depth = 0

    for token in tokens:
        if token.type in ("heading_open", "paragraph_open"):
            in_block = True
            current_block_parts = []
            link_depth = 0
        elif token.type in ("heading_close", "paragraph_close"):
            if in_block:
                block_text = "".join(current_block_parts).strip()
                if block_text:
                    blocks.append(block_text)
                in_block = False
                current_block_parts = []
                link_depth = 0
        elif in_block and token.type == "inline" and token.children:
            for child in token.children:
                if child.type == "link_open":
                    link_depth += 1
                elif child.type == "link_close":
                    link_depth = max(0, link_depth - 1)
                elif link_depth > 0:
                    continue
                elif child.type == "image":
                    continue
                elif child.type == "html_inline":
                    continue
                elif child.type in ("softbreak", "hardbreak"):
                    current_block_parts.append("\n")
                elif child.type in ("text", "code_inline"):
                    current_block_parts.append(child.content)

    return "\n\n".join(blocks)


def run_s0(
    repo: Repository,
    *,
    novel_id: str,
    raw_dir: str | Path,
    config: dict[str, Any],
    chapter_limit: int | None = None,
) -> S0Result:
    """Run stage S0 parse and clean on chapter files in raw_dir."""
    if chapter_limit is None:
        chapter_limit = config.get("total_chapters")

    if chapter_limit is None:
        raise ValueError("chapter_limit is required when total_chapters is not in config")

    raw_path = Path(raw_dir)
    file_pattern = re.compile(r"^chapter_(\d+)\.md$")

    non_matching: list[str] = []
    chapters_by_num: dict[int, list[Path]] = collections.defaultdict(list)

    if raw_path.is_dir():
        for entry in sorted(raw_path.iterdir(), key=lambda p: p.name):
            m = file_pattern.match(entry.name)
            if not m:
                non_matching.append(entry.name)
                continue
            n = int(m.group(1))
            if n > chapter_limit:
                continue
            chapters_by_num[n].append(entry)

    if non_matching:
        logger.warning("Skipping non-matching files: %s", sorted(non_matching))

    missing = [n for n in range(1, chapter_limit + 1) if n not in chapters_by_num]
    duplicates = [
        n for n in range(1, chapter_limit + 1) if len(chapters_by_num.get(n, [])) > 1
    ]

    if missing or duplicates:
        raise ChapterSetError(missing=missing, duplicates=duplicates)

    # Contiguity check passed. Now verify or create novel record.
    if repo.get_novel(novel_id=novel_id) is None:
        repo.add_novel(
            novel_id=novel_id,
            title=config["title"],
            genre_profile=config["genre"],
            total_chapters=config["total_chapters"],
            author=config.get("author"),
        )

    cleaning_patterns = [
        re.compile(p) if isinstance(p, str) else p
        for p in (config.get("cleaning_patterns") or [])
    ]

    # Pre-process each chapter to lines
    intermediate: list[dict[str, Any]] = []
    for n in range(1, chapter_limit + 1):
        file_path = chapters_by_num[n][0]
        raw_content = file_path.read_text(encoding="utf-8-sig")
        content_lf = raw_content.replace("\r\n", "\n").replace("\r", "\n")

        # Strip leading YAML front matter block
        body = re.sub(r"^---\n.*?\n---(?:\n|$)", "", content_lf, count=1, flags=re.DOTALL)
        title = _extract_title(body, n)
        plain = _markdown_to_plain_text(body)
        norm = normalize_text(plain)

        lines = norm.split("\n")
        if cleaning_patterns:
            cleaned_lines = []
            for line in lines:
                for pat in cleaning_patterns:
                    line = pat.sub("", line)
                cleaned_lines.append(line)
            lines = cleaned_lines

        intermediate.append({
            "chapter_id": n,
            "title": title,
            "lines": lines,
        })

    # Watermark rule across all chapters in this run
    total_chapters_run = len(intermediate)
    line_chapter_counts: dict[str, int] = collections.defaultdict(int)

    for item in intermediate:
        seen_in_chapter: set[str] = set()
        for line in item["lines"]:
            stripped = line.strip()
            if stripped:
                seen_in_chapter.add(stripped)
        for s in seen_in_chapter:
            line_chapter_counts[s] += 1

    watermarks = {
        line
        for line, count in line_chapter_counts.items()
        if (count > 0.05 * total_chapters_run) and (count >= 3)
    }

    # Finalize clean_text and save or skip
    processed = 0
    written = 0
    skipped = 0

    for item in intermediate:
        processed += 1
        chapter_id = item["chapter_id"]
        title = item["title"]

        filtered_lines = [
            line for line in item["lines"]
            if line.strip() not in watermarks
        ]

        # Collapse runs of more than one blank line and strip ends
        joined = "\n".join(line.rstrip() for line in filtered_lines)
        collapsed = re.sub(r"\n{3,}", "\n\n", joined)
        clean_text = collapsed.strip()

        sha256 = hashlib.sha256(clean_text.encode("utf-8")).hexdigest()
        token_count = estimate_tokens(clean_text)

        existing_job = repo.get_job(novel_id=novel_id, chapter_id=chapter_id, stage="s0")
        if (
            existing_job is not None
            and existing_job["status"] == "done"
            and existing_job["input_hash"] == sha256
        ):
            skipped += 1
            continue

        repo.upsert_job(
            novel_id=novel_id,
            chapter_id=chapter_id,
            stage="s0",
            status="running",
            input_hash=sha256,
        )
        repo.save_chapter(
            novel_id=novel_id,
            chapter_id=chapter_id,
            title=title,
            clean_text=clean_text,
            sha256=sha256,
            token_count=token_count,
            stage="s0",
            input_hash=sha256,
        )
        written += 1

    return S0Result(processed=processed, written=written, skipped=skipped)
