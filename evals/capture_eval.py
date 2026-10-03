"""Auto-capture eval: run the real UserPromptSubmit hook with autocapture on against an empty store and see which
prompts get stored. Positives are standing preferences/rules/decisions (some synthetic, marked); everything else is a
real chat prompt that must NOT be captured. Target: 0 false captures on holdout.
Usage: JEVMEM_ENV_FILE=.env uv run python evals/capture_eval.py evals/capture_prompts.json
Rebuild the data with: uv run python evals/capture_eval.py --build  (needs evals/data/prompts.json)"""
import argparse, json, os

from jevmem.config import Config
from jevmem.hooks import user_prompt
from jevmem.service import Service

POSITIVE_REAL = {"we always squash merge pull requests"}
SYNTHETIC = [
    "From now on we always squash merge pull requests.",
    "Convention: all API handlers live in src/api and are named <resource>_handler.py.",
    "We decided to use Postgres instead of MySQL for the shop database.",
    "Never commit directly to main in this repo; always open a pull request.",
    "Our team standard is 4-space indentation and a 100-character line limit for Python.",
    "Remember that we deploy only on Tuesdays and never on Fridays.",
    "Always run the type checker before opening a PR.",
    "Use pnpm, not npm, for every JavaScript package in this project.",
    "Decision: error messages shown to users must never include stack traces.",
    "For this project, dates are stored in UTC and only converted in the UI.",
    "We never mock the database in integration tests; we use a real test container.",
    "Going forward all new endpoints need an OpenAPI description before review.",
]


def build():
    rows = json.load(open("evals/data/prompts.json"))
    seen, out = set(), []
    for r in rows:
        t = r["text"].strip()
        if t in seen or len(t) < 12 or len(t) > 700:
            continue
        seen.add(t); out.append({"prompt": t, "label": "skip", "source": "real"})
    out += [{"prompt": t, "label": "capture", "source": "synthetic"} for t in SYNTHETIC]
    for i, r in enumerate(out):
        r["split"] = "holdout" if i % 3 == 2 else "tune"
    json.dump({"prompts": out}, open("evals/capture_prompts.json", "w"), indent=1)
    print(len(out), "prompts written")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data", nargs="?"); ap.add_argument("--build", action="store_true")
    ap.add_argument("--standing", type=float); ap.add_argument("--out")
    a = ap.parse_args()
    if a.build:
        return build()
    os.environ["JEVMEM_AUTOCAPTURE"] = "1"
    cfg = Config(**({"capture_standing": a.standing} if a.standing is not None else {}))
    rows = []
    for p in json.load(open(a.data))["prompts"]:
        svc = Service(":memory:", config=cfg)
        user_prompt(svc, {"cwd": "/x/demo", "prompt": p["prompt"]})
        got = svc.store.count() > 0
        rows.append({**p, "captured": got})
        if got != (p["label"] == "capture"):
            print("BAD", p["split"], p["label"], p["prompt"][:90].replace("\n", " "))
    for split in ("tune", "holdout"):
        rs = [r for r in rows if r["split"] == split]
        pos = [r for r in rs if r["label"] == "capture"]; neg = [r for r in rs if r["label"] == "skip"]
        print(f"{split}: captured {sum(r['captured'] for r in pos)}/{len(pos)} standing, "
              f"false captures {sum(r['captured'] for r in neg)}/{len(neg)}")
    if a.out:
        json.dump(rows, open(a.out, "w"), indent=1)


if __name__ == "__main__":
    main()
