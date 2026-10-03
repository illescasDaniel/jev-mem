# Release checklist for 0.1.0

Internal checklist; kept here so the README can stay about using the project.

- [x] Old-vs-new retrieval comparison repeated: the gap was the call cap, not a regression.
- [x] Live trial on a real project: SessionStart, recall auto/full, tool errors, worktree scope/branch tag/unmerged
      penalty. It found two bugs, both fixed (the MCP server's vector index did not see notes written by other
      processes; `mode="bogus"` was accepted).
- [x] `consolidate --all` on a copy of a real store: 82 notes, same 4 correct supersessions as the live DB, 0 new
      proposals.
- [x] Documentation pass: README rewritten, diagrams in `docs/diagrams`, technical docs split out.
- [ ] Fresh-install test: `uv tool install` / `uvx` from a clean machine or container, `claude mcp add` per the docs,
      hooks.
- [ ] Run the hooks on a second project and collect `JEVMEM_HOOK_LOG` data for a few days.
- [ ] CI green on Linux, macOS and Windows (git subprocess calls, paths, sqlite-vec fallback).
- [ ] Publish the repository, set up the PyPI trusted publisher and the `pypi` GitHub environment.
- [ ] Tag `v0.1.0`, let `release.yml` build and upload, verify `pip install jevmem` and the entry points.
