"""Text-only section/paragraph boundaries, math-aware cuts and explicit context windows."""

from __future__ import annotations

import re

HEADING = re.compile(
    r"(?m)^(?:\\(?:sub)*section\*?\{[^\n}]+\}|#{1,6} [^\n]+|"
    r"(?:\d+(?:\.\d+)*\.?\s+)[A-Z][^\n]{1,100}|"
    r"(?:Abstract|Introduction|Methods|Methodology|Results|Discussion|Conclusions?|"
    r"Assumptions|Definitions|Limitations|Appendix)[^\n]{0,80})$"
)
MATH = re.compile(
    r"\$\$.*?\$\$|\\\[.*?\\\]|\\begin\{(equation\*?|align\*?|gather\*?)\}.*?\\end\{\1\}|(?<!\$)\$(?!\$).*?(?<!\$)\$(?!\$)",
    re.S,
)


def sections(record):
    text = record["text"]
    headings = list(HEADING.finditer(text))
    starts = sorted({0, *[m.start() for m in headings]})
    titles = {m.start(): m.group().strip() for m in headings}
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        yield start, end, titles.get(start, record.get("section", "unidentified section"))


def section_chunks(records, chars, context_chars=0):
    for record in records:
        text = record["text"]
        spans = [(m.start(), m.end()) for m in MATH.finditer(text)]
        for begin, finish, section in sections(record):
            cursor = begin
            while cursor < finish or (not text and cursor == 0):
                end = min(cursor + chars, finish)
                warnings = list(record.get("extraction_warnings", []))
                if end < finish:
                    # Prefer a paragraph boundary without throwing away whitespace.
                    boundary = text.rfind("\n\n", cursor + max(1, chars // 3), end)
                    if boundary >= 0:
                        end = boundary + 2
                    for a, b in spans:
                        if a < end < b:
                            if a > cursor:
                                end = a
                            elif b - cursor <= chars:
                                end = b
                            else:
                                warnings.append(
                                    "Mathematical expression exceeds the chunk budget; split text is uncertain, consult the original source."
                                )
                            break
                end = max(cursor + 1, end) if finish > cursor else finish
                chunk = {
                    **record,
                    "text": text[cursor:end],
                    "character_offset": cursor,
                    "section": section,
                    "extraction_warnings": warnings,
                }
                if context_chars:
                    chunk.update(
                        context_before=text[max(begin, cursor - context_chars) : cursor],
                        context_after=text[end : min(finish, end + context_chars)],
                    )
                yield chunk
                if end >= finish:
                    break
                cursor = end
