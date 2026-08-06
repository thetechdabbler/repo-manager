---
session: repo-manager-cli-20260806
topic: "repo-manager CLI: multi-repo workspace management design"
phase: flame
created: 2026-08-06
ideas_evaluated: 10
shortlist_count: 5
scoring_method: six-hats-rapid + impact/feasibility + retrofit-cost tiebreak
---

# Flame Report: repo-manager CLI

> 10 ideas evaluated. 5 shortlisted for Forge.

**Scoring note.** Impact x Feasibility produced a five-way tie at 20/25, which is a real finding rather than a scoring failure: the strongest ideas here are all cheap. A third axis breaks the tie and is arguably the dominant one for a tool that does not exist yet: **retrofit cost**, or how expensive it is to change your mind later. Day-one architecture decisions rank above equally-valuable additions that can land anytime.

**Red Hat note.** User delegated ("you decide"). Gut feeling was inferred from the source artifact: a 766-line design doc, safety-obsessed, five delivery phases, eight of ten readiness boxes pre-checked. That profile predicts enthusiastic adoption of rigor-adding ideas and resistance to scope-deleting ones. The shortlist deliberately weights against that inferred bias, which is why two subtractive ideas (S1-4, and S2-5's kernel) survive into the top three.

---

## Shortlist

### 1. Pure policy function (S2-1)

**Impact**: 4/5 | **Feasibility**: 5/5 | **Retrofit cost**: Very high

`decide(snapshot, operation) -> Decision` with no git, no IO, no subprocess.

**Why shortlisted**: The architectural spine, and the only item on this list that must be decided on day one or paid for forever. Buys the 40-cell safety matrix as a millisecond-scale test suite with zero fixtures, and lets `--help` and the docs table be *generated from* the policy table so they cannot drift from the code.

### 2. Reason code registry (S2-4)

**Impact**: 4/5 | **Feasibility**: 5/5 | **Retrofit cost**: High (frozen once published)

One registry emits the enum, the human-readable message, and the docs table.

**Why shortlisted**: Exit code `3` tells an automated consumer "something was skipped" and nothing more. A closed enum makes exhaustive handling machine-checkable and reduces the Phase 5 Codex skill to a switch statement. Must land before the first JSON consumer exists, and needs a stated compatibility policy since renames become breaking changes.

### 3. Derive-first config, absorbing selection-as-primitive (S1-4 + S2-5)

**Impact**: 4/5 | **Feasibility**: 5/5 | **Retrofit cost**: High

Config exists only to record overrides, written lazily on first disagreement with an inference. Separately: treat *selection* as a primitive independent of any menu.

**Why shortlisted**: Phase 1 as designed is entirely config machinery whose only user-visible verb is `status`. Deriving by default collapses it. S2-5's provocation does not survive as stated (deleting interactive mode contradicts a stated requirement, and a menu is self-teaching in a way `--stdin` is not), but its kernel does: if selection is a tested primitive, the eventual interactive mode is a thin front-end rather than a second path through the policy engine.

### 4. Parallel fetch with dated staleness (S1-3)

**Impact**: 4/5 | **Feasibility**: 5/5 | **Retrofit cost**: Low

`behind 3 (as of 14m ago)` instead of `unknown`, plus a bounded parallel fetch pool.

**Why shortlisted**: Not architecturally urgent, but it decides whether the tool gets reached for daily or avoided. Eleven repos fetched serially is 10 to 20 seconds; bounded-parallel is roughly 2. `GIT_TERMINAL_PROMPT=0` is non-optional here: eleven concurrent credential prompts is a hang that presents as a crash. Auth failure should surface as its own reason code.

### 5. Trash instead of `clean`, with plan-gated discard (S1-2 + S1-1)

**Impact**: 4/5 | **Feasibility**: 5/5 | **Retrofit cost**: Low, but must precede `--discard` shipping

Untracked files move to a trash dir. `--discard` requires `--plan <file>`.

**Why shortlisted**: Committed work is already recoverable via reflog, so `git clean` is the *only* genuinely irreversible operation in the entire design. Redirecting it removes that class of loss outright. General `undo` is deliberately cut from this: the semantics get murky fast (undo a pull after committing on top?) and the word promises more than it can deliver. Together with plan-gated discard, this closes the open discard-semantics item on the readiness checklist.

---

## Impact x Feasibility Matrix

```
          HIGH IMPACT
              |
   S2-1 Pure policy (4,5)      S1-1 Plan/apply (4,4)
   S2-4 Reason codes (4,5)              |
   S1-4 Derive-first (4,5)              |
   S1-3 Parallel fetch (4,5)            |
   S1-2 Trash not clean (4,5)           |
              |
--------------+--------------
   S1-5 Phase 4 first (3,5)    S2-3 Cassettes (3,4)
   S2-2 .git-is-file (3,5)     S2-5 Kill menus (3,4)
              |
          LOW IMPACT
     HIGH FEASIBILITY <-> LOW FEASIBILITY
```

Everything of consequence landed in the quick-wins quadrant, which is why retrofit cost had to do the ordering work.

---

## Full Evaluation

### Plan file as the unit of mutation (S1-1)

| Hat | Perspective |
|---|---|
| White | terraform, pulumi, and `kubectl --dry-run=server` all do this. Git has no equivalent. The design already has `--dry-run` on every command, so plan output exists in some form. |
| Red | Appealing to a safety-first author, but the two-step flow will feel like friction on the routine path. |
| Yellow | Dissolves the open discard-confirmation question. The confirmation becomes a reviewable, diffable, archivable file. CI-friendly. |
| Black | Two steps is friction for the 90% case of "just pull." Worse, a plan goes stale: repo state moves between plan and apply, so per-repo preconditions (expected HEAD sha) are required or the plan lies. |
| Green | Keep one-step commands, but make `--plan <file>` the *only* way to run `--discard`. Destructive gets ceremony; routine does not. |
| Blue | Needs a plan schema plus apply-time re-validation. Medium build, layers onto existing `--dry-run`. |

**Scores**: Impact 4/5, Feasibility 4/5. Kernel merged into shortlist item 5.

### Operation journal with `undo` (S1-2)

| Hat | Perspective |
|---|---|
| White | Reflog already makes committed work recoverable. `git clean` on untracked files is the only true data loss in the design. |
| Red | Strong relief factor. Changes the emotional weight of the whole destructive path. |
| Yellow | Resets the risk math: `--discard` stops needing three confirmations. Also useful for recovering from tool bugs. |
| Black | General `undo` semantics get murky (undo a pull after committing on top?). Trash dir needs GC. Partial undo of a batch confuses. The word invites unkeepable promises. |
| Green | Do not ship `undo`. Ship "untracked files move to trash, never oblivion" plus a printed journal. ~90% of the value at ~10% of the semantics. |
| Blue | Trash-on-clean is about a day. General undo is weeks. |

**Scores**: Impact 4/5, Feasibility 5/5 as narrowed. **Shortlisted (narrowed).**

### Staleness-dated remote state (S1-3)

| Hat | Perspective |
|---|---|
| White | The design sets `fetch_before_status = false` and defines `relationship = unknown`. Parallel fetch is trivially available since git is process-per-repo. |
| Red | The unglamorous one that determines actual daily adoption. |
| Yellow | Makes default `status` both fast and informative. Serial ~10-20s versus parallel ~2s across 11 repos. |
| Black | Parallel fetch plus credential prompts is a hang that looks like a crash. Interleaved output needs serializing. Dated info can mislead if the label is too subtle. |
| Green | `GIT_TERMINAL_PROMPT=0`, capped concurrency, auth failure as its own reason code. Age-based dimming so staleness is visually obvious. |
| Blue | Small. Thread pool, one timestamp field, a renderer change. |

**Scores**: Impact 4/5, Feasibility 5/5. **Shortlisted.**

### Delete config from v1 (S1-4)

| Hat | Perspective |
|---|---|
| White | Phase 1 is entirely config machinery. Its only user-visible verb is `status`. |
| Red | The idea the author is most likely to resist, and most likely to benefit from. |
| Yellow | Install-to-value drops to one command. Deletes a component, its validation rules, and its test suite from v1. |
| Black | Overrides are a real requirement: `dev` for `payments-api` is not inferable if `origin/HEAD` points at `main`. Groups-as-directories cannot express the doc's own example, where `payments` crosses paths. Named profiles would be lost. |
| Green | Derive by default; config exists only as a lazily-written override file. Profiles stay possible without gating v1. |
| Blue | Negative work. This is a subtraction. |

**Scores**: Impact 4/5, Feasibility 5/5. **Shortlisted (as the hybrid).**

### Build Phase 4 first (S1-5)

| Hat | Perspective |
|---|---|
| White | mu-repo, meta, gita, vcstool, and git-repo all ship multi-repo update/sync. None do incremental cross-repo summaries with watermarks. Both unchecked readiness items live in the dangerous half. |
| Red | Tempting because it is the interesting part, which is exactly why it deserves suspicion. |
| Yellow | Read-only means zero risk of wrecking your own workspace while developing. Validates the workspace abstraction cheaply. It is the differentiated part. |
| Black | Also the part most easily faked with a `for` loop over `git log`. Watermarks are the only sticky piece. If the daily pain is "update eleven repos," this delivers no relief. Risk of building the interesting thing over the useful thing. |
| Green | Resolvable by one question: which for-loop do you actually retype every morning? |
| Blue | Reordering is free today and expensive later. |

**Scores**: Impact 3/5 (high variance), Feasibility 5/5. **Cut**: it is a sequencing question, not a feature.

### Pure policy function (S2-1)

| Hat | Perspective |
|---|---|
| White | Standard functional-core / imperative-shell. The design separates policy from coordination conceptually but does not commit to purity. |
| Red | Obvious-yes, therefore easy to under-prioritize. |
| Yellow | 40 table cells tested in milliseconds, no fixtures. Riskiest logic gets cheapest tests. The safety matrix becomes printable data. |
| Black | Almost none. Bugs can migrate into snapshot construction, so that seam needs its own tests. |
| Green | Generate the docs and `--help` safety table *from* the policy table. Add a property test: `update` never emits a branch-changing command for any snapshot. |
| Blue | Nearly free on day one, expensive to retrofit. |

**Scores**: Impact 4/5, Feasibility 5/5. **Shortlisted, ranked first.**

### Repository identity / aliased repos (S2-2)

| Hat | Perspective |
|---|---|
| White | Linked worktrees and submodules both use `.git` as a file containing a `gitdir:` pointer. The doc lists worktree support as a future consideration, meaning today it misbehaves silently. |
| Red | Low excitement, real correctness value. |
| Yellow | Prevents silent wrongness: double-counted commits in summaries, confusing `switch` failures. Cheap insurance. |
| Black | Full identity keying costs a `rev-list --max-parents=0` per repo, and multi-root repos exist. The workspace in question probably has no aliases, so the full version solves a hypothetical. |
| Green | Ship ten lines: detect `.git`-is-a-file, label it, refuse to mutate it. Defer identity keying until summaries need dedup. |
| Blue | Tiny when scoped to file-vs-dir detection. |

**Scores**: Impact 3/5, Feasibility 5/5. **Cut from Forge**: just write it, it does not need a brief.

### Record/replay cassettes (S2-3)

| Hat | Perspective |
|---|---|
| White | The VCR/betamax pattern. Precondition is a single subprocess boundary, which the design already has. |
| Red | Satisfying to build, which is a warning sign this early. |
| Yellow | The fixture matrix is 11 scenarios each needing bare remotes and setup scripts. Cassettes replace most of that and give deterministic end-to-end CLI tests. |
| Black | Cassettes rot silently. One recorded on git 2.43 verifies that you parse what you recorded, not what git emits now. Fixtures test reality; cassettes test memory. False confidence is worse than slow tests. |
| Green | Both, with a job: fixtures run nightly and re-record cassettes; cassettes run per-PR. Rot becomes a failing nightly rather than false confidence. |
| Blue | Recording harness is about a day. Combined with S2-1, very little needs real git. |

**Scores**: Impact 3/5, Feasibility 4/5. **Cut**: defer until fixture setup actually hurts.

### Reason codes as the contract (S2-4)

| Hat | Perspective |
|---|---|
| White | The design already has classifications and prose skip reasons. This formalizes the pair and commits to stability. |
| Red | Obvious-yes, and cheap enough to actually do. |
| Yellow | Satisfies design principle 4 directly. Makes Phase 5 trivial. Closed enums make exhaustive handling checkable. |
| Black | Very little. The cost is discipline: once published, renames are breaking. Needs a documented compatibility policy. |
| Green | One registry generates the enum, the human message, and the docs table, so prose and code cannot disagree. |
| Blue | Small, but must land before the first JSON consumer exists. |

**Scores**: Impact 4/5, Feasibility 5/5. **Shortlisted, ranked second.**

### Delete interactive mode (S2-5)

| Hat | Perspective |
|---|---|
| White | jj, gh, and kubectl all lean on `--json` plus external filters. fzf multi-select is established. But interactive mode is a headline feature of this design, with a numbered menu and a selection expression language. |
| Red | The provocation. Uncomfortable, and productively so. |
| Yellow | Removes the largest and hardest-to-test surface. Selection expressions become jq, which is never documented or tested. |
| Black | Contradicts a stated requirement. The target user may not be a jq user. Typed discard confirmation genuinely wants interactivity. `--stdin` discoverability is poor where a menu is self-teaching. Windows and non-fzf users get nothing. |
| Green | Defer rather than delete. Ship `list --json` and `--stdin` in v1; build the v2 menu on the same selection primitives. |
| Blue | Deferring is free. The real extraction is "selection is a primitive, separate from the menu." |

**Scores**: Impact 3/5, Feasibility 4/5. **Kernel merged into shortlist item 3.**

---

## Evaluation Summary

- **Total evaluated**: 10
- **Shortlisted**: 5 (2 of them merged pairs)
- **Scoring method**: rapid Six Hats, then Impact/Feasibility, then retrofit-cost tiebreak
- **Highest impact**: five-way tie at 4/5 (S2-1, S2-4, S1-4, S1-3, S1-2)
- **Most feasible**: S1-4 (a subtraction, so negative work)
- **Most novel**: S1-2 (inverting safety posture from prevention to reversibility)
- **Highest retrofit cost, so most urgent**: S2-1
- **Cut with reasons**: S1-5 (a sequencing question, not a feature), S2-2 (too small to need a brief), S2-3 (premature test infrastructure)

## Open question carried forward

S1-5 reduces to one factual question that only the user can answer, and it determines v1 scope: **which for-loop do you actually retype every morning?** If it is `git log`, summaries are v1. If it is `git pull`, update/sync is v1.

---

*Generated by specsmd Ideation Flow, Flame skill*
