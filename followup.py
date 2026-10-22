"""
followup.py — keep SLM follow-up questions atomic.

The SLM is asked for one symptom/fact per follow-up question, but older
cached outputs and occasional live generations still combine several items
("nausea, vision changes, or dizziness?").  This module rewrites those into
separate questions with a shared grammatical stem, while leaving ambiguous
or non-parallel questions untouched.
"""

import re

_AUX_WH = {
    "do", "does", "did", "are", "is", "was", "were", "have", "has", "had",
    "can", "could", "will", "would", "shall", "should", "may", "might", "must",
    "how", "when", "where", "why", "what", "which", "who", "whom", "whose",
    "kya", "क्या", "कब", "कहाँ", "कैसे", "कितना", "कितने", "कौन",
}

# Prepositions / possessives reliably introduce a list ("pain in your legs").
_EN_STRONG = [
    "of", "in", "on", "at", "to", "with", "for", "from", "about", "regarding",
    "during", "after", "before", "between", "your", "my", "his", "her", "their",
    "our", "its",
]
# Determiners and verbs can also introduce a list, but only when the text
# after them is a single word ("any nausea", "have fever").
_EN_WEAK = [
    "any", "a", "an", "the", "this", "that", "these", "those",
    "have", "has", "had", "having", "feel", "feels", "feeling", "notice",
    "notices", "noticed", "experience", "experiences", "experienced", "get",
    "gets", "getting", "suffer", "suffering", "take", "taking", "use", "using",
    "drink", "drinking", "eat", "eating", "been",
]

# Hindi/Hinglish questions need an explicit "you" marker before the list;
# otherwise splitting "Kya dard left haath ya jabde..." produces fragments.
_HI_ANCHORS = [
    "aap", "aapko", "aapka", "aapki", "aapke", "tum", "tumhe", "tumhara",
    "tumhari", "tumhare",
    "आप", "आपको", "आपका", "आपकी", "आपके", "तुम", "तुम्हें", "तुम्हारा",
    "तुम्हारी", "तुम्हारे",
]

_HI_MARKERS = re.compile(
    r"[\u0900-\u097F]|\b(?:aap|aapko|kya|hai|hain|ya|aur|ko|mein|raha|rahi|rahe|nahi|dard|bukhar)\b",
    re.I,
)

# Split clauses joined by ", and did you ..." / " or do you ..." into
# independent questions.  A conjunction followed by a plain noun is a list,
# not a new clause.
_CLAUSE_SPLIT = re.compile(
    r"\s*(?:;\s*|(?:,)?\s+(?:and|or|ya|aur|या|और)\s+(?=(?:"
    + "|".join(sorted(_AUX_WH, key=len, reverse=True))
    + r")(?:\s|$)))",
    re.I,
)

_LIST_SEP = re.compile(
    r"\s*(?:,\s*(?:(?:or|and|ya|aur|या|और)\s+)?|\s+(?:or|and|ya|aur|या|और)\s+)",
    re.I,
)

_EN_WH = ["how", "when", "where", "why", "what", "which", "who", "whom", "whose"]
_EN_AUX = [
    "do", "does", "did", "are", "is", "was", "were", "have", "has", "had",
    "can", "could", "will", "would", "shall", "should", "may", "might", "must",
]
_HI_WH = [
    "kya", "kab", "kahan", "kaise", "kitna", "kitne", "kaun", "kyon",
    "क्या", "कब", "कहाँ", "कैसे", "कितना", "कितने", "कौन", "क्यों",
]
_SUBJECT_PRONOUNS = {
    "i", "you", "he", "she", "it", "we", "they",
    "me", "him", "her", "us", "them",
}
_DETERMINERS = {
    "the", "a", "an", "this", "that", "these", "those",
    "your", "my", "his", "her", "their", "our", "its",
}
_CONJUNCTIONS = ["and", "or", "ya", "aur", "या", "और"]

# A polite question can contain several embedded questions:
# "Could you please tell me when it started, how long..., and how severe...".
_EN_POLITE_RE = re.compile(
    r"^(?P<prefix>(?:"
    r"(?:could|can|would|will)\s+you(?:\s+please)?\s+"
    r"(?:tell|describe|explain)(?:\s+me)?"
    r"|please\s+(?:tell|describe|explain)(?:\s+me)?"
    r"|i(?:'d| would)\s+like\s+to\s+know"
    r"))\s+(?P<body>.+)$",
    re.I,
)
_HI_POLITE_RE = re.compile(
    r"^(?P<prefix>(?:"
    r"(?:कृपया\s+)?(?:बताइए|बताएं|बता\s+दीजिए|बता\s+सकते\s+हैं|"
    r"बता\s+सकती\s+हैं|बता\s+सकता\s+हूँ)\s+कि"
    r"|(?:kripya\s+)?(?:bataiye|bataye|bata\s+dijiye|bata\s+sakte\s+hain|"
    r"bata\s+sakti\s+hain)\s+ki"
    r"))\s+(?P<body>.+)$",
    re.I,
)


def _alternation(words: list[str]) -> str:
    return "|".join(sorted((re.escape(word) for word in words), key=len, reverse=True))


_DIRECT_STARTERS = _alternation(_EN_WH + _EN_AUX + _HI_WH)
_BODY_STARTERS = _alternation(_EN_WH + _EN_AUX + _HI_WH + ["if", "whether"])
_CONJ_ALT = _alternation(_CONJUNCTIONS)

# Split at a comma/conjunction only when the next clause actually starts a
# new question; this keeps noun lists and "such as ..." phrases intact.
_DIRECT_BOUNDARY_RE = re.compile(
    r"\s*(?:,\s*(?:(?:" + _CONJ_ALT + r")\s+)?"
    r"|\s+(?:" + _CONJ_ALT + r")\s+)"
    r"(?=(?:" + _DIRECT_STARTERS + r")(?!\w))",
    re.I,
)
_BODY_BOUNDARY_RE = re.compile(
    r"\s*(?:,\s*(?:(?:" + _CONJ_ALT + r")\s+)?"
    r"|\s+(?:" + _CONJ_ALT + r")\s+)"
    r"(?=(?:" + _BODY_STARTERS + r")(?!\w))",
    re.I,
)

_PAREN_RE = re.compile(r"\([^)]*\)")

_EN_SUFFIX_RE = re.compile(r"\s+(along with|together with|accompanied by)\s+.+$", re.I)
_EN_TAIL_PREP_RE = re.compile(
    r"\s+(in|on|at|with|along with|around|near|under|behind)\s+.+$", re.I
)
_HI_SUFFIX_RE = re.compile(
    r"\s+((?:ho\s+)?(?:raha|rahi|rahe)\s+(?:hai|hain)|"
    r"(?:रहा|रही|रहे)\s+(?:है|हैं)|(?:हो\s+)?(?:रहा|रही|रहे)\s+(?:है|हैं)|"
    r"(?:hua|hui|hue)\s+(?:hai|hain)|(?:हुआ|हुई|हुए)\s+(?:है|हैं))\s*$",
    re.I,
)
_HI_VERB_TAIL_RE = re.compile(
    r"\s+([A-Za-z\u0900-\u097F]+(?:te|ti|ta|ते|ती|ता)\s+(?:hai|hain|है|हैं))\s*$",
    re.I,
)
_HI_BARE_HAI_RE = re.compile(r"\s+(hai|hain|है|हैं)\s*$", re.I)

# A shared stem ending in one of these only works for simple parallel nouns
# ("pain in the chest, arm, or jaw"), not for a phrase like "shortness of breath".
_GUARDED_STEM_RE = re.compile(
    r"(?:^|\s)(?:in|on|at|of|to|with|for|from|about|your|my|his|her|their|our|the|a|an)\s*$",
    re.I,
)
_COMPLEX_ITEM_RE = re.compile(r"\s(?:of|in|on|at|with|for|from|about|to)\s", re.I)

# Only copy a trailing location ("... redness in your legs") onto earlier
# items when those items are parallel local symptoms.  Otherwise the location
# belongs only to the last item ("fever, cough, or pain in the chest").
_LOCAL_SYMPTOMS = {
    "pain", "swelling", "redness", "warmth", "numbness", "tingling",
    "stiffness", "discomfort", "tenderness", "bruising", "rash", "itching",
    "soreness", "lump", "bleeding",
}


def _question_mark(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip(" ,;")
    if not text.endswith(("?", "।")):
        text += "?"
    return text


def _capitalise(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _protect_parentheses(text: str) -> tuple[str, list[str]]:
    saved: list[str] = []

    def repl(match: re.Match) -> str:
        saved.append(match.group(0))
        return f"§{len(saved) - 1}§"

    return _PAREN_RE.sub(repl, text), saved


def _restore_parentheses(text: str, saved: list[str]) -> str:
    for i, value in enumerate(saved):
        text = text.replace(f"§{i}§", value)
    return text


def _last_anchor(text: str, anchors: list[str]) -> re.Match | None:
    pattern = (
        r"(?<!\w)(?:"
        + "|".join(sorted((re.escape(a) for a in anchors), key=len, reverse=True))
        + r")(?!\w)"
    )
    last = None
    for match in re.finditer(pattern, text, re.I):
        last = match
    return last


def _choose_anchor(text: str, hindi: bool) -> re.Match | None:
    strong = _last_anchor(
        text, _HI_ANCHORS + _EN_STRONG if hindi else _EN_STRONG
    )
    weak = None
    if not hindi:
        pattern = (
            r"(?<!\w)(?:"
            + "|".join(
                sorted((re.escape(a) for a in _EN_WEAK), key=len, reverse=True)
            )
            + r")(?!\w)"
        )
        for match in re.finditer(pattern, text, re.I):
            if len(text[match.end():].strip().split()) == 1:
                weak = match
    if strong is None:
        return weak
    if weak is None:
        return strong
    return strong if strong.end() > weak.end() else weak


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[?।])\s+", text.strip())
    out = []
    for part in parts:
        part = re.sub(r"^(?:and|or|ya|aur|या|और)\s+", "", part.strip(), flags=re.I)
        if part:
            out.append(part)
    return out or [text]


def _words(text: str) -> list[str]:
    return re.findall(r"[\w\u0900-\u097F']+", text.lower())


def _is_direct_question(part: str) -> bool:
    """True if ``part`` is a standalone question, not an embedded clause."""
    words = _words(part)
    if not words:
        return False

    first = words[0]
    if first in _HI_WH:
        return True
    if first in _EN_AUX:
        return True

    if first in _EN_WH:
        aux_idx = next(
            (i for i, word in enumerate(words) if word in _EN_AUX), None
        )
        if aux_idx is None:
            return False
        # In "how long it has been", the subject precedes the auxiliary:
        # that is an embedded clause, not a direct question.
        if any(word in _SUBJECT_PRONOUNS for word in words[:aux_idx]):
            return False
        after = words[aux_idx + 1] if aux_idx + 1 < len(words) else ""
        return after in _SUBJECT_PRONOUNS or after in _DETERMINERS

    return False


def _split_direct_clause_list(text: str) -> list[str]:
    """Split "When did it start, how long..., how severe...?" safely."""
    parts = [
        part.strip(" ,;")
        for part in _DIRECT_BOUNDARY_RE.split(text)
        if part.strip(" ,;")
    ]
    if len(parts) < 2:
        return [text]

    parts = [
        re.sub(r"^(?:and|or|ya|aur|या|और)\s+", "", part, flags=re.I).strip()
        for part in parts
    ]
    if not all(_is_direct_question(part) for part in parts):
        return [text]
    return parts


def _split_polite(text: str) -> list[str]:
    """Rewrite "tell me X, Y, and Z" as one polite question per clause."""
    for regex in (_EN_POLITE_RE, _HI_POLITE_RE):
        match = regex.match(text.strip())
        if not match:
            continue
        prefix = match.group("prefix").strip() + " "
        body = match.group("body").strip()
        body = re.sub(r"^that\s+", "", body, flags=re.I)
        parts = [
            part.strip(" ,;")
            for part in _BODY_BOUNDARY_RE.split(body)
            if part.strip(" ,;")
        ]
        if len(parts) >= 2:
            return [prefix + part for part in parts]
        return [text]
    return [text]


def _is_local_symptom(text: str) -> bool:
    words = _words(text)
    return bool(words) and words[-1] in _LOCAL_SYMPTOMS


def _finish(
    items: list[str], stem: str, suffix: str, hindi: bool
) -> list[str] | None:
    """Attach a trailing shared clause to each item and build the questions."""
    last = items[-1]
    match = _EN_SUFFIX_RE.search(last)
    if match:
        suffix = last[match.start():].strip()
        items[-1] = last[: match.start()].strip()
    else:
        match = _EN_TAIL_PREP_RE.search(last)
        if match and items[:-1] and all(
            _is_local_symptom(item) for item in items[:-1]
        ):
            suffix = last[match.start():].strip()
            items[-1] = last[: match.start()].strip()
        else:
            match = _HI_VERB_TAIL_RE.search(last)
            if match:
                suffix = match.group(1)
                items[-1] = last[: match.start()].strip()
            else:
                match = _HI_SUFFIX_RE.search(last)
                if match:
                    suffix = last[match.start():].strip()
                    items[-1] = last[: match.start()].strip()
                elif hindi and len(last.split()) <= 3:
                    match = _HI_BARE_HAI_RE.search(last)
                    if match:
                        suffix = match.group(1)
                        items[-1] = last[: match.start()].strip()

    out = []
    for item in items:
        item = item.strip(" ,;")
        if not item or len(item) < 2:
            continue
        text = _question_mark(
            f"{stem}{item}{(' ' + suffix) if suffix else ''}"
        )
        out.append(_capitalise(text))
    out = list(dict.fromkeys(out))
    return out if len(out) >= 2 else None


def _starts_clause(text: str) -> bool:
    """True if text starts like a new question/clause rather than a list item."""
    words = _words(text)
    if not words:
        return False
    return (
        words[0] in _EN_WH
        or words[0] in _EN_AUX
        or words[0] in _HI_WH
        or words[0]
        in {
            "if", "whether", "so", "yes", "no", "then", "otherwise",
            "because", "although", "though",
        }
    )


def _split_list_chunk(chunk: str) -> list[str]:
    hindi = bool(_HI_MARKERS.search(chunk))
    protected, saved = _protect_parentheses(chunk)
    protected = protected.rstrip(" ?।")
    parts = [
        part.strip(" ,;")
        for part in _LIST_SEP.split(protected)
        if part.strip(" ,;")
    ]
    if len(parts) < 2:
        return [chunk]

    # "Do you have fever, and if so, how high?" is not a noun list.  If the
    # direct/clause splitters could not handle it, leave it intact rather
    # than producing fragments such as "Do you have if so?".
    if any(_starts_clause(part) for part in parts[1:]):
        return [chunk]

    chosen = _choose_anchor(parts[0], hindi)
    if chosen is None:
        if hindi:
            return [chunk]
        # Without a reliable shared stem, only split simple one-word lists
        # ("Nausea, vomiting, or dizziness?").  If the first segment is
        # longer ("Is the pain constant or intermittent?"), split off its
        # final word.  Multi-word later items are left alone to avoid
        # generating meaningless fragments.
        if not all(len(part.split()) == 1 for part in parts[1:]):
            return [chunk]
        first_words = parts[0].split()
        if len(first_words) == 1:
            stem = ""
            items = [_restore_parentheses(part, saved) for part in parts]
        else:
            stem = (
                parts[0][: len(parts[0]) - len(first_words[-1])].rstrip() + " "
            )
            items = [first_words[-1]] + [
                _restore_parentheses(part, saved) for part in parts[1:]
            ]
        result = _finish(items, stem, "", hindi)
        return result if result else [chunk]

    stem = _restore_parentheses(parts[0][: chosen.end()].rstrip() + " ", saved)
    first_item = _restore_parentheses(parts[0][chosen.end():].strip(), saved)
    items = [first_item] + [
        _restore_parentheses(part, saved) for part in parts[1:]
    ]

    # A stem ending in a preposition/article ("pain in the ...") only works
    # for simple parallel nouns (chest/arm/jaw).  A longer item such as
    # "shortness of breath" would be distorted, so keep the question intact.
    if _GUARDED_STEM_RE.search(stem):
        if any(
            len(item.split()) > 2 or _COMPLEX_ITEM_RE.search(f" {item} ")
            for item in items
        ):
            return [chunk]

    result = _finish(items, stem, "", hindi)
    return result if result else [chunk]


def split_question_text(text: str) -> list[str]:
    """Split one follow-up question into atomic questions.

    Returns a one-item list when the question is already atomic or when a
    safe split is not possible.
    """
    final: list[str] = []
    if not text or not text.strip():
        return final

    for sentence in _split_sentences(text):
        for polite in _split_polite(sentence):
            for direct in _split_direct_clause_list(polite):
                for chunk in _CLAUSE_SPLIT.split(direct):
                    chunk = chunk.strip()
                    if not chunk:
                        continue
                    for piece in _split_list_chunk(chunk):
                        final.append(_capitalise(_question_mark(piece)))

    seen: set[str] = set()
    out: list[str] = []
    for question in final:
        key = re.sub(r"\s+", " ", question).strip().lower()
        if key not in seen:
            seen.add(key)
            out.append(question)
    return out


def split_followup_questions(questions: list) -> list[dict]:
    """Normalise SLM follow-up question objects into atomic questions.

    Accepts dicts (``{"question": ...}``) or plain strings and always returns
    dicts matching :class:`schemas.FollowUpQuestion`.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for question in questions or []:
        text = question.get("question", "") if isinstance(question, dict) else str(question)
        for atomic in split_question_text(text):
            key = atomic.strip().lower()
            if key and key not in seen:
                seen.add(key)
                out.append({"question": atomic})
    return out
