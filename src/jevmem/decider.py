"""Decision backends. A Decider answers a batch of typed questions over one state."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol


@dataclass(frozen=True)
class NoulQ:
    instructions: str


@dataclass(frozen=True)
class ChoiceQ:
    instructions: str
    criteria: dict[str, str]


Question = NoulQ | ChoiceQ


@dataclass
class Answer:
    p: float = 0.0                       # Noul probability
    choice: str | None = None            # Choice selection
    probs: dict[str, float] = field(default_factory=dict)
    confidence: float | None = None


class DeciderUnavailable(Exception):
    """Jev could not be reached or returned something unusable. Callers degrade."""


class Decider(Protocol):
    calls: int

    def ask(self, state: dict, questions: dict[str, Question]) -> dict[str, Answer]: ...


class JevDecider:
    """Asks a Jev-schema `/v1/systemone` endpoint: hosted Jev (`TYPESAFE_API_KEY`) or any compatible server such as a
    local model (`JEVMEM_BASE_URL`, no Jev key needed). Settings come from the arguments, then these env vars:
    `JEVMEM_BASE_URL`, `JEVMEM_MODEL`, `JEVMEM_API_KEY` (only if that server wants one), `JEVMEM_TIMEOUT` (seconds)."""

    def __init__(self, model: str | None = None, timeout: float | None = None, base_url: str | None = None,
                 api_key: str | None = None):
        from dotenv import load_dotenv

        load_dotenv()
        self._client = None             # created on first use, so commands that never ask Jev need no API key
        self._model, self._base_url, self._api_key, self._timeout = model, base_url, api_key, timeout
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.latency_s = 0.0

    def _make_client(self):
        from typesafe_sdk import TypeSafeClient

        base_url = self._base_url or os.environ.get("JEVMEM_BASE_URL") or None
        if base_url:
            # The SDK insists on a key. A custom endpoint gets JEVMEM_API_KEY or a placeholder, never the hosted
            # TYPESAFE_API_KEY, so a Jev key in .env is not sent to another server.
            key = self._api_key or os.environ.get("JEVMEM_API_KEY") or "unused"
        else:
            key = self._api_key or os.environ.get("TYPESAFE_API_KEY")
            if not key:
                raise DeciderUnavailable("no decision backend configured: set TYPESAFE_API_KEY (hosted Jev) or "
                                         "JEVMEM_BASE_URL (a local or third-party Jev-schema server)")
        return TypeSafeClient(api_key=key, base_url=base_url)

    @property
    def model(self) -> str | None:
        return self._model or os.environ.get("JEVMEM_MODEL") or None

    @property
    def timeout(self) -> float:
        return self._timeout or float(os.environ.get("JEVMEM_TIMEOUT") or 60.0)

    def ask(self, state: dict, questions: dict[str, Question]) -> dict[str, Answer]:
        from typesafe_sdk import Choice, Noul, TypeSafeError

        if not questions:
            return {}
        qs = {k: Noul(instructions=q.instructions) if isinstance(q, NoulQ)
              else Choice(instructions=q.instructions, criteria=q.criteria)
              for k, q in questions.items()}
        t = time.time()
        try:
            if self._client is None:
                self._client = self._make_client()
            r = self._client.system_one(state=state, questions=qs, model=self.model, timeout=self.timeout)
        except TypeSafeError as e:
            raise DeciderUnavailable(str(e)) from e
        self.calls += 1
        self.latency_s += time.time() - t
        self.input_tokens += r.usage.input_tokens
        self.output_tokens += r.usage.output_tokens
        out: dict[str, Answer] = {}
        for k, a in r.answers.items():
            if a.type == "noul":
                out[k] = Answer(p=a.noul)
            elif a.type == "choice":
                out[k] = Answer(choice=a.choice, probs=dict(a.probabilities), confidence=a.confidence)
        missing = set(questions) - set(out)
        if missing:  # fail closed: never treat a missing answer as a pass
            raise DeciderUnavailable(f"invalid_response: missing {sorted(missing)}")
        return out


class FakeDecider:
    """Deterministic decider for tests. `rule(state, key, question) -> float | Answer`."""

    def __init__(self, rule: Callable[[dict, str, Question], float | Answer] | None = None):
        self.rule = rule or (lambda s, k, q: 0.0)
        self.calls = 0
        self.log: list[tuple[dict, dict[str, Question]]] = []
        self.down = False

    def ask(self, state: dict, questions: dict[str, Question]) -> dict[str, Answer]:
        if self.down:
            raise DeciderUnavailable("fake outage")
        self.calls += 1
        self.log.append((state, questions))
        out = {}
        for k, q in questions.items():
            v = self.rule(state, k, q)
            out[k] = v if isinstance(v, Answer) else Answer(p=float(v))
        return out
