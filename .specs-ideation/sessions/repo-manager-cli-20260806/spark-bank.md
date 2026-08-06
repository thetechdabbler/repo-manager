---
session: repo-manager-cli-20260806
topic: "repo-manager CLI: multi-repo workspace management design"
phase: spark
created: 2026-08-06
total_ideas: 10
total_batches: 2
favorites_count: 0
domains_covered: 10
---

# Spark Bank: repo-manager CLI

> 10 ideas generated across 2 batches. Technical topic, so diversity is measured in engineering perspectives (architecture, testing, correctness, DX, sequencing) rather than the open-topic domain wheel.

Source context: `/Users/alex/work/services/docs/repo-manager-architecture.md`

---

## Favorites

*No explicit favorites selected. User delegated Red Hat scoring ("you decide"); see flame-report.md for the inferred shortlist.*

---

## All Ideas by Theme

### Theme: Command shape and control flow

**S1-1** — **Plan file as the unit of mutation**
Split every mutating command into `plan` and `apply`. `repo-manager sync --plan sync.toml` writes the exact per-repo git command list it intends to run; `apply` executes it. Dry-run stops being a flag and becomes the default shape of the tool. The discard confirmation becomes a file you read, diff, and archive.
*Perspective: Architecture / command model. Technique: Analogy (terraform plan/apply).*

**S2-5** — **Delete interactive mode; let the shell be the selector**
Menus, selection expressions (`group:`, `search:`, `dirty`), plan tables, and typed confirmations are the most expensive surface to build and test. Compose instead: `repo-manager list --json | jq '...' | repo-manager update --stdin`. The selection language becomes jq and grep, which users already know.
*Perspective: Scope / shell composition. Technique: Inversion + SCAMPER-Eliminate. (Provocation)*

### Theme: Safety and recoverability

**S1-2** — **Operation journal with `undo`**
Record pre-state per repo (HEAD sha, branch, stash refs, upstream) before any mutation. `repo-manager undo` walks it backward. Untracked files under `--discard` are tar'd to a trash dir rather than `git clean`'d. Inverts the safety posture: instead of making destructive ops hard to reach, make them cheap to reverse.
*Perspective: Safety / recoverability. Technique: Inversion.*

**S2-2** — **Repository identity, because "independent repos" is an assumption that breaks**
A linked `git worktree` has `.git` as a *file*, not a directory, and shares refs with its parent. Two clones of one origin are not independent for summaries. A root reached via symlink can be discovered twice. Key each repo on `(root-commit-sha, origin-url)` plus `.git` file-vs-dir detection, and add an `aliased` classification.
*Perspective: Correctness / discovery invariants. Technique: What-if.*

### Theme: Testing strategy

**S2-1** — **Extract the policy engine as a pure function and table-test the safety matrix**
Make it `decide(snapshot, operation) -> Decision` with no git, no IO, no subprocess. The 8x5 safety table becomes a parametrized test running in milliseconds; fixture repos only ever test the git backend adapter. Add a property test: for any snapshot, an `update` decision never emits a command that changes branch.
*Perspective: Testing / pure-logic seam. Technique: First Principles.*

**S2-3** — **Record/replay cassettes for the git backend**
The git backend is the only subprocess boundary, which makes it a perfect seam. `--record` dumps every `(repo, argv) -> (exit, stdout, stderr)` to a cassette; tests replay it. End-to-end coverage against real git output with no fixture construction and full determinism. A bug report becomes "attach your cassette."
*Perspective: Testing / subprocess seam. Technique: Analogy (HTTP VCR).*

### Theme: Performance and honest reporting

**S1-3** — **Staleness-dated remote state instead of `unknown`**
`relationship = unknown` is honest but discards real information. Persist `last_fetch_at` per repo and render `behind 3 (as of 14m ago)` with age-based dimming. Pair with a bounded parallel fetch pool, since N repos means N network round trips and fetch is essentially the entire latency budget.
*Perspective: Performance / cost model. Technique: First Principles.*

### Theme: Scope and setup cost

**S1-4** — **Delete config from v1 and derive everything**
`init`, profiles, groups, and inference-source tracking stand between install and first value. Zero-config: walk for `.git` from cwd, read default branch from `refs/remotes/origin/HEAD`, treat groups as path prefixes. Config comes into existence lazily, written only to record an override.
*Perspective: DX / setup cost. Technique: SCAMPER-Eliminate.*

### Theme: Delivery sequencing

**S1-5** — **Build Phase 4 first**
Summaries are read-only, need no policy engine, no discard semantics, no confirmation UX, and are the part no existing tool does. Update and sync are commodity (`mu-repo`, `meta`, `gita`, `vcstool`). Phase 1 becomes "cross-repo commit summaries with watermarks": shippable at zero mutation risk, and it validates the workspace abstraction before you invest in the dangerous half.
*Perspective: Delivery sequencing / prior art. Technique: Inversion (on sequencing).*

### Theme: Machine contract

**S2-4** — **Reason codes, not prose, as the Phase 5 contract**
Exit code `3` tells an agent "something was skipped" and nothing more. Give every per-repo result `{status, reason_code, suggested_action, commands_run}` with `reason_code` from a closed enum: `DIRTY_WORKTREE | NO_UPSTREAM | DIVERGED | DETACHED | DEFAULT_BRANCH_AMBIGUOUS | REMOTE_UNAVAILABLE`. The Codex skill switches on enums instead of parsing English.
*Perspective: Output contract / automation. Technique: SCAMPER-Substitute.*

---

## Perspective Coverage

| Perspective | Ideas | Coverage |
|---|---|---|
| Architecture / command model | 1 | ██ |
| Safety / recoverability | 1 | ██ |
| Performance / cost model | 1 | ██ |
| DX / setup cost | 1 | ██ |
| Delivery sequencing | 1 | ██ |
| Testing / pure-logic seam | 1 | ██ |
| Testing / subprocess seam | 1 | ██ |
| Correctness / discovery invariants | 1 | ██ |
| Output contract / automation | 1 | ██ |
| Scope / shell composition | 1 | ██ |

Not yet explored: observability and the operation log, config lifecycle and migration, partial-batch failure recovery, the Codex skill boundary itself.

---

## Session Stats

- **Batches generated**: 2
- **Total ideas**: 10
- **Favorites**: 0 (delegated)
- **Techniques used**: Analogy, Inversion, First Principles, SCAMPER (Eliminate, Substitute), What-if
- **Provocations injected**: 1 (S2-5)

---

*Generated by specsmd Ideation Flow, Spark skill*
