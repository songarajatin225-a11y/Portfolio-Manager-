"""Deterministic financial-news sentiment and impact classification.

A compact finance lexicon (in the spirit of Loughran-McDonald, extended with Indian market terms)
with negation handling. Context-dependent words ("profit", "growth", "record", "cut", "lower") are
deliberately excluded because their direction depends on the verb that follows.

The method is transparent and reproducible, and it reports low confidence rather than forcing a
label: weak or mixed evidence is classified ``neutral``. Claude-based classification can be
switched on in settings; the method used is stored with each article.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

POSITIVE = {
    "beat",
    "beats",
    "surge",
    "surges",
    "surged",
    "soar",
    "soars",
    "soared",
    "jump",
    "jumps",
    "jumped",
    "rally",
    "rallies",
    "rallied",
    "gain",
    "gains",
    "gained",
    "rise",
    "rises",
    "rose",
    "upgrade",
    "upgrades",
    "upgraded",
    "outperform",
    "outperforms",
    "strong",
    "stronger",
    "robust",
    "boost",
    "boosts",
    "boosted",
    "expansion",
    "expands",
    "win",
    "wins",
    "won",
    "bags",
    "secures",
    "secured",
    "approval",
    "approved",
    "approves",
    "buyback",
    "upbeat",
    "optimistic",
    "recovery",
    "recovers",
    "rebound",
    "rebounds",
    "positive",
    "improve",
    "improves",
    "improved",
    "improvement",
    "margin expansion",
    "inflows",
    "bullish",
    "accelerate",
    "accelerates",
    "raises guidance",
    "beats estimates",
    "all-time high",
    "multibagger",
    "rerating",
    "debt-free",
}
NEGATIVE = {
    "miss",
    "misses",
    "missed",
    "plunge",
    "plunges",
    "plunged",
    "slump",
    "slumps",
    "slumped",
    "fall",
    "falls",
    "fell",
    "drop",
    "drops",
    "dropped",
    "decline",
    "declines",
    "declined",
    "loss",
    "losses",
    "weak",
    "weaker",
    "downgrade",
    "downgrades",
    "downgraded",
    "underperform",
    "slash",
    "slashes",
    "probe",
    "fraud",
    "default",
    "defaults",
    "penalty",
    "penalised",
    "penalized",
    "fined",
    "ban",
    "banned",
    "lawsuit",
    "resigns",
    "resignation",
    "concern",
    "concerns",
    "warning",
    "warns",
    "crash",
    "crashes",
    "selloff",
    "sell-off",
    "bearish",
    "outflows",
    "pressure",
    "slowdown",
    "slows",
    "delay",
    "delayed",
    "halt",
    "halted",
    "scam",
    "raid",
    "raids",
    "insolvency",
    "bankruptcy",
    "pledge",
    "pledged",
    "negative",
    "worst",
    "margin pressure",
    "lowers guidance",
    "misses estimates",
    "52-week low",
    "write-off",
    "impairment",
    "recall",
    "strike",
    "show-cause",
    "default risk",
}
NEGATIONS = {"not", "no", "never", "without", "neither", "nor", "fails", "failed", "despite"}
INTENSIFIERS = {"sharply": 1.5, "significantly": 1.4, "strongly": 1.3, "massive": 1.5, "huge": 1.4, "biggest": 1.4}

HIGH_IMPACT = (
    r"\bresults?\b",
    r"\bq[1-4]\b",
    r"\bearnings\b",
    r"\bprofit\b",
    r"\bmerger\b",
    r"\bacquisition\b",
    r"\bacquire",
    r"\bdemerger\b",
    r"\bsebi\b",
    r"\brbi\b",
    r"\bpolicy rate\b",
    r"\brepo\b",
    r"\bfraud\b",
    r"\bdefault\b",
    r"\bdowngrade",
    r"\bupgrade",
    r"\bbuyback\b",
    r"\bbonus\b",
    r"\bsplit\b",
    r"\bdividend\b",
    r"\bguidance\b",
    r"\bblock deal\b",
    r"\bbulk deal\b",
    r"\bopen offer\b",
    r"\bdelist",
    r"\binsolvency\b",
    r"\bresign",
    r"\bceo\b",
    r"\braid\b",
    r"\bban\b",
    r"\bbudget\b",
    r"\bgdp\b",
    r"\binflation\b",
    r"\bcpi\b",
)

TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9\-']*")


@dataclass
class SentimentResult:
    label: str  # positive | negative | neutral
    score: float  # -1 .. 1
    confidence: float  # 0 .. 1
    impact: str  # high | normal
    method: str = "lexicon"


def _phrases(tokens: list[str]) -> list[str]:
    return [f"{a} {b}" for a, b in zip(tokens, tokens[1:], strict=False)]


def classify(title: str, summary: str = "") -> SentimentResult:
    text = f"{title}. {title}. {summary}".lower()  # headline counted twice: it carries most of the signal
    tokens = TOKEN_RE.findall(text)
    pos = neg = 0.0
    for i, tok in enumerate(tokens):
        weight = 1.0
        if i > 0 and tokens[i - 1] in INTENSIFIERS:
            weight = INTENSIFIERS[tokens[i - 1]]
        negated = any(t in NEGATIONS for t in tokens[max(0, i - 3) : i])
        if tok in POSITIVE:
            if negated:
                neg += weight  # "not strong" reads negative
            else:
                pos += weight
        elif tok in NEGATIVE:
            if negated:
                pos += weight * 0.5  # "no default" is only mildly positive
            else:
                neg += weight
    for ph in _phrases(tokens):
        if ph in POSITIVE:
            pos += 1.5
        elif ph in NEGATIVE:
            neg += 1.5
    total = pos + neg
    score = 0.0 if total == 0 else (pos - neg) / total
    # confidence grows with evidence and with how one-sided it is
    confidence = 0.0 if total == 0 else min(1.0, abs(score) * min(1.0, total / 4))
    if confidence < 0.25 or abs(score) < 0.2:
        label = "neutral"
    else:
        label = "positive" if score > 0 else "negative"
    impact = "high" if any(re.search(p, text) for p in HIGH_IMPACT) else "normal"
    return SentimentResult(label=label, score=round(score, 3), confidence=round(confidence, 3), impact=impact)
