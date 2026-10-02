from jevmem import Service
from jevmem.decider import JevDecider
svc = Service(":memory:", decider=JevDecider())
P = "project:shop"
notes = [
  ("We deploy the shop with ops/deploy.sh.", "2026-03-01"),
  ("Deployments of the shop are done by running ops/deploy.sh.", "2026-04-10"),
  ("The default branch of the shop repo is master, as recorded on 2026-01-05.", "2026-01-05"),
  ("The default branch of the shop repo was renamed to main on 2026-09-20.", "2026-09-20"),
  ("The shop uses Postgres as its database.", "2026-02-01"),
  ("The shop uses MySQL as its database.", "2026-06-01"),
  ("Lunch on Fridays is at 12:30.", "2026-05-01"),
]
for t, ts in notes:
    r = svc.writer.write(t, scope=P, timestamp=ts, entities=["shop"])
rep = svc.consolidator.run()
print(rep)
for n in svc.store.nodes():
    fl = svc.store.flags_of(n.id)
    if fl: print(f"[{n.id}] {n.content[:60]} -> {[(f,o,round(p,2)) for f,o,p in fl]}")
for it in svc.consolidator.pending():
    print("PENDING", it["kind"], round(it["p"],2), [m["content"][:45] for m in it["memories"]])
print("calls", svc.decider.calls, f"{svc.decider.latency_s:.1f}s")
