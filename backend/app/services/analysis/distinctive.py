"""Distinctive-term extraction - shared by News, Research Gaps and Debate.

Finds the words that actually characterize THIS paper (frequent in its
abstract/title, not common academic boilerplate), so topic sections can be
grounded in the paper's real concepts instead of generic fallbacks.
"""
from __future__ import annotations

import re
from collections import Counter

_TOPIC_BOILERPLATE = {"research", "discussion", "future", "development", "human",
                      "journal", "volume", "copyright", "published", "article",
                      "author", "authors", "science", "international", "university",
                      "information", "national", "administration", "vision",
                      "and", "the", "for", "are", "was", "not", "but", "you",
                      "can", "all", "our", "its", "out", "any", "after", "before",
                      "during", "either", "often", "under", "just", "through",
                      "life", "today", "society", "center", "centre", "program", "programme"}

_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z\-]{3,}")

# generic academic + common English filler - never topic-distinctive
_STOP = {
    "this", "that", "with", "from", "have", "has", "been", "were", "their",
    "which", "about", "into", "than", "then", "them", "they", "there", "these",
    "those", "such", "also", "only", "more", "most", "some", "each", "both",
    "very", "much", "like", "over", "upon", "between", "among", "within",
    "because", "while", "where", "when", "what", "whose", "will", "would",
    "could", "should", "shall", "must", "might", "may", "does", "done",
    "paper", "study", "section", "figure", "table", "results", "result",
    "approach", "method", "methods", "propose", "proposed", "proposes",
    "present", "presented", "shows", "shown", "using", "used", "based",
    "consider", "considered", "given", "case", "first", "second", "third",
    "however", "therefore", "moreover", "furthermore", "conclusion",
    "conclusions", "introduction", "abstract", "related", "work", "well",
    "many", "several", "different", "various", "important", "possible",
    "problem", "problems", "question", "questions", "example", "examples",
    "terms", "term", "notion", "notions", "concept", "concepts", "idea",
    "sense", "meaning", "means", "ways", "way", "things", "thing", "make",
    "makes", "made", "take", "takes", "taken", "get", "gets", "even", "still",
    "here", "being", "does", "cannot", "without", "other", "others", "same",
    "order", "part", "parts", "point", "points", "fact", "facts", "true",
    "since", "would", "could", "also", "thus", "hence", "rather", "quite",
    "false", "real", "nature", "kind", "types", "type", "value", "values",
    "number", "numbers", "large", "small", "high", "low", "new", "old",
}


def distinctive_terms(*texts: str | None, n: int = 5, min_len: int = 5) -> list[str]:
    """Rank words by (frequency × length) across the given texts.

    Longer, repeatedly-mentioned words are the paper's real concepts -
    "spacetime" beats "time". Returns up to n lowercase terms.
    """
    combined = " ".join(t for t in texts if t)
    counts: Counter = Counter()
    for w in _WORD_RE.findall(combined):
        wl = w.lower().strip("-")
        if len(wl) < min_len or wl in _STOP:
            continue
        # collapse plural/possessive noise crudely
        key = wl[:-1] if wl.endswith("s") and len(wl) > 5 else wl
        counts[key] += 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1] * (1 + len(kv[0]) / 14), kv[0]))
    return [w for w, _ in ranked[:n]]


def sentences_with_terms(text: str, terms: list[str], max_len: int = 400) -> list[str]:
    """Sentences from `text` that mention any of the terms - used to quote
    the paper's own words as evidence for a gap or tension."""
    out = []
    for sent in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text or "")):
        s = sent.strip()
        if not (30 < len(s) < max_len):
            continue
        sl = s.lower()
        if any(t in sl for t in terms):
            out.append(s)
    return out


_DOMAIN_PATTERNS = [
    re.compile(r"\b(?:quantum\w*|qubits?|bell inequalit\w*)\b", re.I),
    re.compile(r"\b(?:relativit\w*|space[- ]?time|lorentz\w*|time dilation|arrow of time)\b", re.I),
    re.compile(r"\b(?:space exploration|space ?flight|space ?craft|space station|space shuttle|"
               r"solar system|astronaut\w*|nasa|starship|satellite\w*|lunar|mars|moon)\b", re.I),
    re.compile(r"\b(?:temple\w*|archaeolog\w*|lingam\w*|iconograph\w*|sculpture\w*)\b", re.I),
    re.compile(r"\b(?:machine learning|neural network\w*|language model\w*|transformer\w*|"
               r"reinforcement learning|deep learning)\b", re.I),
]
_PUBLISHER_LINE = re.compile(r"\b(?:copyright|publisher|published by|press|journal|volume|"
                             r"university|pardee|center|centre|papers)\b", re.I)
_GENERIC_SINGLE = {"exploration", "theory", "space", "time", "temple", "policy",
                   "center", "centre", "program", "matter", "state", "states"}
_SPACE_NEWS_SUBJECT = re.compile(
    r"\b(?:space exploration|space ?flight|space ?craft|space station|space shuttle|"
    r"space force|space polic\w*|space regulat\w*|solar system|astronaut\w*|nasa|"
    r"starship|spacex|starlink|rocket\w*|orbit\w*|lunar|mars|moon|"
    r"satellite[- ](?:tracking|connectivity|communication\w*|network\w*|launch\w*|technolog\w*))\b", re.I)


def _topic_texts(paper, db=None):
    from app.models.entities import PaperSection
    title = paper.title or ""
    body = paper.abstract or ""
    if db is not None and getattr(paper, "id", None):
        sections = db.query(PaperSection).filter(PaperSection.paper_id == paper.id) \
            .order_by(PaperSection.position).all()
        body += " " + " ".join((s.text or "")[:5000] for s in sections
                                if s.name not in {"references", "acknowledgements", "front matter"})[:30000]
    keywords = [str(k) for k in (paper.keywords or []) if k]
    topics = [str(t) for t in (paper.meta or {}).get("topics", []) if t]
    body = " ".join(line for line in re.split(r"[\n\r]|(?<=[.!?])\s+", body)
                    if not _PUBLISHER_LINE.search(line))
    return title, keywords, topics, body


def _subject_pattern(title, keywords, topics, body):
    scored = []
    for index, pattern in enumerate(_DOMAIN_PATTERNS):
        score = (len(pattern.findall(title)) * 12 + len(pattern.findall(" ".join(keywords))) * 6
                 + min(len(pattern.findall(" ".join(topics))), 2)
                 + min(len(pattern.findall(body)), 3))
        if score:
            scored.append((score, -index, pattern))
    return max(scored, key=lambda hit: hit[:2])[2] if scored else None


def topic_relevance_filter(paper, db=None, *, headline=False):
    """Require the document's subject, not an incidental matching phrase.

    For example, time-flow language in EEG or software does not establish a
    relationship with a document whose keywords specify special relativity.
    """
    pattern = _subject_pattern(*_topic_texts(paper, db))
    if headline and pattern is _DOMAIN_PATTERNS[2]:
        pattern = _SPACE_NEWS_SUBJECT
    return lambda title, summary="": bool(pattern is None or pattern.search(title + " " + summary))


def paper_topic_queries(paper, db=None, *, limit: int = 3) -> list[str]:
    """Prefer coherent title/keyword concepts over frequent body bigrams."""
    title, keywords, topics, body = _topic_texts(paper, db)
    subject = _subject_pattern(title, keywords, topics, body)
    stop = _STOP | _TOPIC_BOILERPLATE

    def words(text):
        return [w.lower().strip("-") for w in re.findall(r"[A-Za-z][A-Za-z-]{2,}", text)]

    def valid(w):
        return 3 <= len(w) <= 24 and w not in stop

    candidates = {}

    def add(phrase, score, *, named_title=False):
        phrase = " ".join(word.lower().strip("-")
                          for word in re.findall(r"[A-Za-z][A-Za-z-]*", phrase))
        tokens = phrase.split()
        meaningful = [word for word in tokens if valid(word)]
        if not meaningful or len(tokens) > 5 or _PUBLISHER_LINE.search(phrase):
            return
        if len(tokens) == 1 and (tokens[0] in _GENERIC_SINGLE or len(tokens[0]) < 5):
            return
        if subject and not subject.search(phrase) and not named_title:
            return
        candidates.setdefault(phrase, score)
        candidates[phrase] = max(candidates[phrase], score)

    # Multiword author keywords retain their phrase structure, including
    # meaningful stop words in concepts such as "arrow of time".
    for index, phrase in enumerate(keywords):
        if len(words(phrase)) >= 2:
            add(phrase, 120 - index)
        elif subject and subject.search(phrase):
            add(phrase, 105 - index)
    # Remote topic labels contextualize the document; they do not override
    # the document's own title and keywords when building searches.
    for index, phrase in enumerate(topics):
        add(phrase, 60 - index)
    title_tokens = words(title)
    for segment in re.split(r"[–—:|]|\s-\s", title):
        segment_tokens = words(segment)
        for left, right in zip(segment_tokens, segment_tokens[1:]):
            if valid(left) and valid(right):
                add(left + " " + right, 110)
    for word in title_tokens:
        if valid(word) and word not in _GENERIC_SINGLE and len(word) >= 6:
            corroborated = any(word in words(keyword) for keyword in keywords)
            if not subject or subject.search(word) or corroborated:
                add(word, 95 + min(words(body).count(word), 10), named_title=True)

    pairs = Counter()
    anchors = {word for text in [title, *keywords] for word in words(text)
               if valid(word) and word not in _GENERIC_SINGLE}
    for segment in re.split(r"[.?!;\n\r\u2022]", body):
        tokens = words(segment)
        for left, right in zip(tokens, tokens[1:]):
            if valid(left) and valid(right) and left != right:
                phrase = left + " " + right
                if subject or {left, right} & anchors:
                    pairs[phrase] += 1
    for phrase, count in pairs.items():
        if count >= 2:
            add(phrase, 10 + min(count, 10))
    out = []
    for phrase in sorted(candidates, key=lambda value: (-candidates[value], value)):
        tokens = set(phrase.split())
        if any(tokens <= set(query.split()) or set(query.split()) <= tokens for query in out):
            continue
        out.append(phrase)
        if len(out) >= limit:
            break
    return out
