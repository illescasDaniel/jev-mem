"""Convert a LoCoMo conversation (evals/data/locomo10.json, snap-research/locomo) into our eval format.
Usage: python evals/make_locomo.py [conv_index=0] [sessions=4] > evals/locomo_c0.json
Each dialogue turn becomes one note (key = dia_id) stamped with its session date; questions are kept only
when all evidence turns fall inside the chosen sessions. Category 5 (adversarial) questions whose evidence is in later sessions (absent from the notes)
become unanswerable (max 10)."""
import json, re, sys
from datetime import datetime

KINDS = {1: "multi-hop", 2: "temporal", 3: "inference", 4: "single", 5: "unanswerable"}
conv_i = int(sys.argv[1]) if len(sys.argv) > 1 else 0
n_sess = int(sys.argv[2]) if len(sys.argv) > 2 else 4
d = json.load(open("evals/data/locomo10.json"))[conv_i]
c = d["conversation"]
notes, keys = [], set()
for n in range(1, n_sess + 1):
    when = datetime.strptime(re.sub(r"^\d+:\d+ [ap]m on ", "", c[f"session_{n}_date_time"]).replace(",", ""), "%d %B %Y")
    for t in c[f"session_{n}"]:
        txt = t["text"] + (f" (shares a photo: {t['blip_caption']})" if t.get("blip_caption") else "")
        notes.append({"key": t["dia_id"], "timestamp": when.strftime("%Y-%m-%d"),
                      "content": f"On {when.strftime('%-d %B %Y')}, {t['speaker']} said: {txt}", "entities": [t["speaker"]]})
        keys.add(t["dia_id"])
qs = []
for q in d["qa"]:
    ev = [e for e in q.get("evidence", []) if re.fullmatch(r"D\d+:\d+", e)]
    if q["category"] == 5:
        # keep only adversarial questions whose evidence lies in later sessions: truly absent from our notes
        if ev and all(int(e.split(":")[0][1:]) > n_sess for e in ev) and sum(x["kind"] == "unanswerable" for x in qs) < 10:
            qs.append({"q": q["question"], "gold": [], "kind": "unanswerable"})
    elif ev and all(e in keys for e in ev):
        qs.append({"q": q["question"], "gold": ev, "kind": KINDS[q["category"]], "answer": str(q["answer"])})
json.dump({"name": f"locomo-{d['sample_id']}-s{n_sess}", "scope": f"project:{d['sample_id']}", "notes": notes, "questions": qs},
          sys.stdout, indent=1)
