"""Git facts about where a note was written, and what the repository is about right now.

Everything shells out to `git` with a short timeout and returns None / the neutral answer outside a repository, so
jevmem works the same in a folder that is not under version control."""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

TIMEOUT_S = 3.0


def _git(cwd: str | None, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", *args], cwd=cwd or os.getcwd(), capture_output=True, text=True, timeout=TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def project_dir(cwd: str | None = None) -> str:
    """Where the host agent works: `JEVMEM_REPO`, else the given directory, else the process's."""
    return os.environ.get("JEVMEM_REPO") or cwd or os.getcwd()


def repo_name(cwd: str | None) -> str | None:
    """Name of the repository `cwd` belongs to. All worktrees of one repository share it (the common git dir)."""
    common = _git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not common:
        return None
    p = Path(common)
    return (p.parent if p.name == ".git" else p).name


@dataclass(frozen=True)
class GitState:
    branch: str | None       # None on a detached HEAD
    commit: str
    default: str | None      # "main", "master" or origin's default branch


def state(cwd: str | None) -> GitState | None:
    commit = _git(cwd, "rev-parse", "HEAD")
    if not commit:
        return None
    branch = _git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    head = _git(cwd, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    default = head.split("/", 1)[-1] if head else next(
        (b for b in ("main", "master") if _git(cwd, "rev-parse", "--verify", "-q", b)), None)
    return GitState(None if branch in (None, "HEAD") else branch, commit, default)


def recent_work(cwd: str | None, until: str | None = None, commits: int = 10) -> str:
    """Text standing in for "what is this session about": branch, last commit subjects and the paths they touched.
    `until` (ISO date-time) looks at the repository as it was then."""
    args = ["log", f"-{commits}", "--name-only", "--format=%s"] + ([f"--until={until}"] if until else [])
    log = _git(cwd, *args) or ""
    st = state(cwd)
    lines = [ln for ln in log.splitlines() if ln.strip()]
    paths = sorted({ln.rsplit("/", 1)[0] for ln in lines if "/" in ln})[:30]
    subjects = [ln for ln in lines if "/" not in ln or " " in ln][:commits]
    return " ".join(([st.branch] if st and st.branch else []) + subjects + paths)


class BranchRank:
    """Rank notes written on a branch that never reached the current history (abandoned or not yet merged) lower.

    A note is penalized when it was written on another branch than the current/default one AND its commit is
    reachable from neither HEAD nor the default branch. A squash-merged branch looks the same as an abandoned one, so
    the penalty is mild and the note stays visible. Notes without git info (older, or written outside a repo) are not
    penalized."""

    def __init__(self, cwd: str | None, penalty: float):
        self.cwd, self.factor, self.st = cwd, penalty, state(cwd)
        self._cache: dict[tuple[str, str], bool] = {}

    def unmerged(self, branch: str | None, commit: str | None) -> bool:
        if not (self.st and branch and commit) or branch in (self.st.branch, self.st.default):
            return False
        key = (branch, commit)
        if key not in self._cache:
            self._cache[key] = not any(
                _git(self.cwd, "merge-base", "--is-ancestor", commit, ref) is not None
                for ref in ("HEAD", self.st.default) if ref)
        return self._cache[key]

    def penalty(self, node) -> float:
        return self.factor if self.unmerged(node.branch, node.commit) else 1.0

