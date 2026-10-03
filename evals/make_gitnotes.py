"""Build a 'real project notes' eval from a git repo's history: one note per commit subject since a date.
Usage: python evals/make_gitnotes.py <repo> <since YYYY-MM-DD> <questions.json> [extra_notes.json] > evals/real_<name>.json
`extra_notes.json` (optional) is [{key, timestamp, content}] of hand-written notes (e.g. facts from docs) merged in;
gold keys may be commit hashes or extra-note keys.
`questions.json` is [{q, gold:[short hashes], kind}] written by hand against that history (gold [] = unanswerable).
The output holds private commit text, so evals/real_*.json is gitignored."""
import json, subprocess, sys

repo, since, qfile = sys.argv[1:4]
log = subprocess.run(["git", "-C", repo, "log", "--no-merges", "--format=%h|%ad|%s", "--date=short"],
                     capture_output=True, text=True, check=True).stdout.strip().splitlines()
notes = []
for line in log:
    h, d, subject = line.split("|", 2)
    if d < since:                      # author date (--since would use the committer date, wrong after rebases)
        continue
    notes.append({"key": h, "timestamp": d, "content": f"On {d}, commit {h}: {subject}", "entities": []})
if len(sys.argv) > 4:
    for e in json.load(open(sys.argv[4])):
        notes.append({"key": e["key"], "timestamp": e["timestamp"], "content": e["content"], "entities": e.get("entities", [])})
keys = {n["key"] for n in notes}
assert len(keys) == len(notes), "duplicate note keys"
qs = json.load(open(qfile))
bad = [(q["q"], g) for q in qs for g in q["gold"] if g not in keys]
assert not bad, f"gold hashes not in the selected history: {bad}"
name = repo.rstrip("/").split("/")[-1]
json.dump({"name": f"git-{name}-since-{since}", "scope": f"project:{name}", "notes": notes, "questions": qs}, sys.stdout, indent=1)
