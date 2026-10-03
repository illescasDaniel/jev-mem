"""Deterministic write screens. Cheap, no Jev call; they run before (and in addition to) the injection screen."""
from __future__ import annotations

import re

_SECRETS = [
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("AWS access key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("API token", re.compile(r"\b(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}"
                             r"|AIza[0-9A-Za-z_-]{30,}|glpat-[A-Za-z0-9_-]{15,})")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("credential assignment", re.compile(
        r"(?i)\b(password|passwd|passphrase|secret|api[_ -]?key|access[_ -]?token|auth[_ -]?token|token)\b"
        r"\s*(?:is|=|:)\s*[\"']?(?!not\b|set\b|stored\b|in\b|the\b|a\b|an\b|required\b|configured\b)[^\s\"']{4,}")),
    ("connection string with password", re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s:/@]+:[^\s@/]+@")),
]
# only for text captured automatically from prompts (a person's casual wording is not a vetted memory)
_RELATIVE_TIME = re.compile(r"(?i)\b(yesterday|today|tomorrow|tonight|last (week|month|year|night)|next (week|month)"
                            r"|this (morning|afternoon|week)|earlier|recently|just now)\b")
_REMOTE_EXEC = re.compile(r"(?i)(\b(curl|wget)\b[^\n]*\|\s*(sudo\s+)?(ba|z)?sh\b|\bbase64\s+-d\b[^\n]*\|"
                          r"|\beval\s*\(|\bchmod\s+\+x\b[^\n]*&&|\bssh-(rsa|ed25519)\s+AAAA)")


MAX_CAPTURE_CHARS = 300      # a standing preference fits in a sentence or two; longer prompts are requests or discussion
MAX_CAPTURE_SENTENCES = 2


def capture_shape_problem(text: str) -> str | None:
    """Why a prompt is not shaped like a standing preference (None = fine). A whole chat message (several
    paragraphs, a question, a list of requests) was once stored verbatim and then injected at every session."""
    if len(text) > MAX_CAPTURE_CHARS:
        return "too long to be one preference"
    if re.search(r"\n\s*\n", text) or "\n" in text.strip():
        return "spans several lines"
    if "?" in text:
        return "asks a question"
    if len([s for s in re.split(r"(?<=[.!])\s+", text) if s.strip()]) > MAX_CAPTURE_SENTENCES:
        return "too many sentences"
    return None


def secret_kind(text: str) -> str | None:
    for kind, rx in _SECRETS:
        if rx.search(text):
            return kind
    return None


def autocapture_problem(text: str) -> str | None:
    """Why a prompt must not be stored automatically (None = fine)."""
    if secret_kind(text):
        return "contains a secret"
    if _REMOTE_EXEC.search(text):
        return "contains a remote-execution command"
    if _RELATIVE_TIME.search(text):
        return "uses relative dates (notes need absolute dates)"
    return capture_shape_problem(text)


def normalize(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()
