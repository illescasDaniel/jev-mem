"""Decision backends. A Decider answers a batch of typed questions over one state."""
from __future__ import annotations

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
    def __init__(self, model: str | None = None, timeout: float = 60.0):
        from dotenv import load_dotenv

        load_dotenv()
        self._client = None             # created on first use, so commands that never ask Jev need no API key
        self._model, self._timeout = model, timeout
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.latency_s = 0.0

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
                from typesafe_sdk import TypeSafeClient
                self._client = TypeSafeClient()
            r = self._client.system_one(state=state, questions=qs, model=self._model,
                                        timeout=self._timeout)
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
