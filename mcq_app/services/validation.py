"""
Validation and de-duplication of LLM output.

The LLM is treated as an untrusted source: every item is normalized and
checked before it can reach the database.

Rules
* exactly four options labelled A, B, C, D, all non-empty
* non-empty question text
* grounding: most words of the question/options must appear in the source
  text the LLM was given - this rejects hallucinated (fabricated) questions
* duplicates (from overlapping chunks or repeated pages) are merged
"""

import hashlib
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

LABELS = ("A", "B", "C", "D")
_LABEL_PREFIX = re.compile(r"^\s*[\(\[]?\s*([A-Da-d])\s*[\)\]\.:]\s*")
_WORD = re.compile(r"[a-z0-9]+")


@dataclass
class ValidatedQuestion:
    question_number: str
    question_text: str
    options: dict
    fingerprint: str = ""

    def combined_text(self):
        return self.question_text + " " + " ".join(self.options[label] for label in LABELS)


@dataclass
class ValidationReport:
    accepted: list = field(default_factory=list)
    rejected: list = field(default_factory=list)  # (reason, raw item)
    duplicates: int = 0


def normalize(text):
    return " ".join(_WORD.findall((text or "").lower()))


def fingerprint(question_text, options):
    basis = normalize(question_text) + "|" + "|".join(normalize(options[l]) for l in LABELS)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _clean(value):
    if value is None:
        return ""
    return re.sub(r"[ \t]+", " ", str(value)).strip()


def _normalize_options(raw):
    """Accept {"A": ..} / {"a": ..} / {"(A)": ..} / [..4 items..] / [{"label":..,"text":..}]."""
    options = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            m = re.search(r"[A-Da-d]", str(key))
            if m and len(re.sub(r"[^A-Za-z]", "", str(key))) == 1:
                options[m.group(0).upper()] = _clean(value)
    elif isinstance(raw, list):
        if all(isinstance(o, dict) for o in raw):
            for o in raw:
                label = str(o.get("label") or o.get("option_label") or "").strip("() .").upper()
                if label in LABELS:
                    options[label] = _clean(o.get("text"))
        elif len(raw) == 4:
            options = {label: _clean(v) for label, v in zip(LABELS, raw)}

    # Remove a repeated label the model sometimes leaves in the text: "(A) 42".
    for label, text in list(options.items()):
        m = _LABEL_PREFIX.match(text)
        if m and m.group(1).upper() == label:
            options[label] = text[m.end():].strip()
    return options


def grounding_score(candidate, source):
    """Fraction of the candidate's words that occur in the source text."""
    words = _WORD.findall(candidate.lower())
    if not words:
        return 0.0
    source_words = set(_WORD.findall(source.lower()))
    return sum(1 for w in words if w in source_words) / len(words)


def validate_item(raw, source_text=None, min_grounding=0.6):
    """Return (ValidatedQuestion, None) or (None, reason)."""
    if not isinstance(raw, dict):
        return None, "not an object"

    question_text = _clean(raw.get("question_text") or raw.get("question"))
    if len(normalize(question_text)) < 3:
        return None, "missing question text"

    options = _normalize_options(raw.get("options"))
    missing = [label for label in LABELS if not options.get(label)]
    if missing:
        return None, f"missing option(s) {', '.join(missing)}"
    if len(options) != 4:
        return None, "more than four options"

    number = _clean(raw.get("question_number") or raw.get("number"))
    if source_text and number.isdigit():
        # Small models often drop the prefix ("Q.12" -> "12"); restore the
        # numbering exactly as printed in the source when we can find it.
        printed = re.search(rf"(?m)^\s*(Q(?:ue(?:stion)?)?\.?\s*{number})\b", source_text, re.I)
        if printed:
            number = " ".join(printed.group(1).split())
    vq =ValidatedQuestion(number, question_text, {l: options[l] for l in LABELS})

    if source_text is not None:
        score = grounding_score(vq.combined_text(), source_text)
        if score < min_grounding:
            return None, f"not found in source text (grounding {score:.2f})"

    vq.fingerprint = fingerprint(vq.question_text, vq.options)
    return vq, None


def _number_key(number):
    digits = re.findall(r"\d+", number or "")
    return digits[0].lstrip("0") if digits else ""


def _is_duplicate(a, b):
    if a.fingerprint == b.fingerprint:
        return True
    ratio = SequenceMatcher(None, normalize(a.combined_text()), normalize(b.combined_text())).ratio()
    same_number = _number_key(a.question_number) and (
        _number_key(a.question_number) == _number_key(b.question_number)
    )
    # Same number + very similar text = same question read twice (OCR/LLM
    # noise). Without a matching number require near-identical text, because
    # real papers often reuse stems like "Which of the following is true?".
    return ratio >= (0.85 if same_number else 0.97)


def validate_and_dedupe(items_with_source, min_grounding=0.6):
    """
    `items_with_source` is an iterable of (raw_item, source_chunk_text).
    Returns a ValidationReport with questions in first-seen order.
    """
    report = ValidationReport()
    for raw, source in items_with_source:
        vq, reason = validate_item(raw, source, min_grounding)
        if vq is None:
            report.rejected.append((reason, raw))
            continue
        duplicate_of = next((q for q in report.accepted if _is_duplicate(q, vq)), None)
        if duplicate_of is not None:
            report.duplicates += 1
            # Keep the more complete reading of the same question.
            if len(vq.combined_text()) > len(duplicate_of.combined_text()):
                index = report.accepted.index(duplicate_of)
                if not vq.question_number:
                    vq.question_number = duplicate_of.question_number
                report.accepted[index] = vq
            continue
        report.accepted.append(vq)
    return report
