"""
Question-aware chunking.

The old implementation cut text every 2000 characters, which routinely split
a question from its options (the LLM then dropped or invented parts of it).

Here the text is first split into *question blocks* at lines that look like
the start of a question ("Q.12", "Q12)", "12.", "Question 12:"). Whole
blocks are then packed into chunks up to `max_chars`, and the last
`overlap` blocks of each chunk are repeated at the start of the next one so
the LLM always sees complete questions. Duplicates created by the overlap are
removed later by the validation step.
"""

import re
import unicodedata

_MATH_ALNUM = re.compile("[\U0001D400-\U0001D7FF]")

QUESTION_START = re.compile(
    r"""^\s*(?:
        (?:Q|Q\.|Qn\.?|Que\.?|Ques\.?|Question)\s*(?:No\.?\s*)?\d{1,3}\b\s*[.):\-]?
      | \d{1,3}\s*[.)]\s+(?=\S)
    )""",
    re.IGNORECASE | re.VERBOSE,
)


def _line_key(line):
    # "Page 2 of 64" and "Page 3 of 64" should count as the same line.
    return re.sub(r"\d+", "#", " ".join(line.split()).lower())


def strip_repeated_lines(page_texts, min_pages=3, ratio=0.5, edge_lines=3, min_len=8):
    """
    Remove running headers/footers: lines that occur on more than `ratio` of
    pages. They waste LLM tokens and leak into question text.
    """
    if len(page_texts) < min_pages:
        return page_texts
    counts = {}
    for text in page_texts:
        lines = [l for l in text.splitlines() if l.strip()]
        # Only the top/bottom of a page can hold running headers/footers, and
        # short lines ("4", "True") are likely option values - never strip them.
        edge = lines[:edge_lines] + lines[-edge_lines:]
        for key in {_line_key(l) for l in edge if len(_line_key(l)) >= min_len}:
            counts[key] = counts.get(key, 0) + 1
    threshold = ratio * len(page_texts)
    repeated = {
        key for key, n in counts.items()
        # never treat a question/option line as a header
        # (keys have digits replaced by '#', so restore one before matching)
        if n > threshold
        and not QUESTION_START.match(key.replace("#", "1"))
        and not re.match(r"^\(?[a-d#]\)", key)
    }
    if not repeated:
        return page_texts
    return [
        "\n".join(l for l in text.splitlines() if _line_key(l) not in repeated)
        for text in page_texts
    ]


def normalize_layout(text):
    """
    Deterministic clean-up before the LLM sees the text:
    * "(A)\\nvisionary" -> "(A) visionary" (PDF text layers often put the
      option label and its value on separate lines)
    * collapse runs of blank lines
    """
    # Math-italic/bold letters (U+1D400-U+1D7FF, e.g. "𝑡𝑒𝑎𝑚") are styled
    # copies of plain letters; small LLMs cannot read them. Map just this block
    # to plain text - NOT full NFKC, which would also turn "x²" into "x2".
    text = _MATH_ALNUM.sub(lambda m: unicodedata.normalize("NFKC", m.group(0)), text)
    text = re.sub(r"(?m)^[ \t]*(\([A-Da-d]\))[ \t]*\n[ \t]*(?=\S)", r"\1 ", text)
    return re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", text)


def split_into_blocks(text):
    """Return (preamble, [question_block, ...])."""
    lines = text.splitlines()
    preamble, blocks, current = [], [], None
    for line in lines:
        if QUESTION_START.match(line):
            if current is not None:
                blocks.append("\n".join(current).strip())
            current = [line]
        elif current is None:
            preamble.append(line)
        else:
            current.append(line)
    if current is not None:
        blocks.append("\n".join(current).strip())
    return "\n".join(preamble).strip(), [b for b in blocks if b]


_HAS_OPTIONS = re.compile(r"(?m)^\s*[\(\[]?[A-Da-d][\)\].]")


def _merge_headers(blocks, max_len=120):
    """
    Merge section headers that look like questions ("Q.11 - Q.35 Carry ONE
    mark Each") into the following block, so they don't take a slot that a
    real question should use.
    """
    merged, carry = [], ""
    for block in blocks:
        if len(block) < max_len and not _HAS_OPTIONS.search(block):
            carry += block + "\n"
            continue
        merged.append(carry + block)
        carry = ""
    if carry:
        if merged:
            merged[-1] += "\n" + carry.strip()
        else:
            merged.append(carry.strip())
    return merged


def _split_long(text, max_chars, overlap_chars):
    """Fallback: line-boundary windows with character overlap."""
    lines = text.splitlines(keepends=True)
    chunks, current = [], ""
    for line in lines:
        if current and len(current) + len(line) > max_chars:
            chunks.append(current.strip())
            current = current[-overlap_chars:] if overlap_chars else ""
        # A single absurdly long line (no newlines in OCR output) is hard-cut.
        while len(line) > max_chars:
            chunks.append((current + line[:max_chars]).strip())
            line = line[max_chars - overlap_chars:]
            current = ""
        current += line
    if current.strip():
        chunks.append(current.strip())
    return chunks


def chunk_text(text, max_chars=3500, overlap=0, max_blocks=None):
    """
    max_blocks caps questions per chunk: small local models extract reliably
    from a few questions at a time but silently skip items in long lists.
    """
    text = (text or "").strip()
    if not text:
        return []

    preamble, blocks = split_into_blocks(text)
    if len(blocks) < 2:
        # No recognizable question numbering: fall back to overlapping windows
        # (here overlap IS necessary - a question may straddle a window edge).
        if len(text) <= max_chars:
            return [text]
        return _split_long(text, max_chars, overlap_chars=max_chars // 5)

    blocks = _merge_headers(blocks)
    if preamble:
        # Never drop text before the first number - it may hold an unnumbered question.
        blocks[0] = preamble + "\n\n" + blocks[0]

    max_blocks = max_blocks or len(blocks)
    overlap = min(overlap, max_blocks - 1)
    if len(text) <= max_chars and len(blocks) <= max_blocks:
        return [text]

    # Very long blocks (e.g. a passage + question) are split on their own.
    expanded = []
    for block in blocks:
        if len(block) > max_chars:
            expanded.extend(_split_long(block, max_chars, overlap_chars=max_chars // 5))
        else:
            expanded.append(block)

    chunks, current = [], []
    size = 0
    for block in expanded:
        if current and (size + len(block) > max_chars or len(current) >= max_blocks):
            chunks.append("\n\n".join(current))
            current = current[-overlap:] if overlap else []
            size = sum(len(b) + 2 for b in current)
        current.append(block)
        size += len(block) + 2
    if current:
        chunks.append("\n\n".join(current))
    return chunks
