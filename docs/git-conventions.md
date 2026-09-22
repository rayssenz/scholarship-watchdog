# Git conventions

## Branching model

Trunk-based. `main` is always green and always runnable. Work happens on short-lived branches that merge back within a day or two.

```
main                 CI green, linear history (squash or rebase merges only)
  feat/p1-fetch-stage
  fix/snapshot-key-per-page
  docs/model-selection-3-8
data                 orphan branch, machine-written, never merged
```

### Why not GitFlow

`develop`, `release/*` and `hotfix/*` exist to coordinate parallel streams of work and to stabilise a release while the next one is in progress. This project has one developer, ships continuously, and supports no older versions. Adopting that structure would add three long-lived branches that solve problems this project does not have.

The test applied here: a convention is worth adopting when it prevents a failure that can actually occur. Trunk-based branching prevents a broken `main`. GitFlow would prevent release collisions that cannot happen with one person and no release train.

### Why pull requests at all, for a solo project

Two reasons, neither of them ceremony.

CI gates the merge. Tests and lint run on every pull request, and the golden-set eval joins them in P2, so a change reaches `main` only after the checks it has to pass have passed.

The pull request is where evidence lives. This project's central claim is that changes are justified by measurement, not assertion: golden-set eval results, benchmark tables, review findings. A PR body is where those attach to the change they justify. A direct push to `main` has nowhere to put them.

### The `data` branch

Created with `git checkout --orphan data`, so it shares no history with `main`. It holds runtime state: page snapshots, extracted records, run reports, and the encrypted private bundle. It is written by the workflow, never by hand, and never merged in either direction.

The orphan is deliberate. State is not code. A branch with a common ancestor invites someone to merge it, and a merge would drag megabytes of snapshots into the source history permanently.

The workflow pushes with `--force-with-lease`, never `--force`, so a concurrent run fails loudly instead of overwriting another run's state.

## Commit messages

Conventional Commits, single line, imperative mood, lower case after the type.

```
feat(fetch): add role-aware source registry
fix(store): key snapshots by page rather than source
docs(spec): record the promotion gate as fitness not urgency
test(score): cover every null-policy row
chore: pin trafilatura
```

Types in use: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `ci`.

No body unless the change needs a reason that the diff cannot carry. No trailers.

The scope is the pipeline stage where possible (`fetch`, `extract`, `score`, `store`, `notify`), because that maps one-to-one onto `SPEC.md` sections 3.1 to 3.5 and makes the log searchable against the spec.

## What lands in one commit

One commit is one reviewable idea. A commit that adds a feature and reformats four other files is two commits.

Where a change is driven by a spec decision, the spec edit and the code implementing it belong in the same commit. They are one idea, and splitting them produces a window in which the repository contradicts itself.

## Merge strategy

A branch that carries one idea is squash-merged: its intermediate commits are working notes, and `main` gets one commit that says what changed.

A phase branch is rebase-merged instead. Its commits are each already one reviewable idea, each with its tests, and several carry a spec amendment together with the code it describes. Squashing them would fuse a dozen justified changes into one, and the history would stop showing which decision produced which line.

Either way `main` stays linear. Merge commits are not used.

## Tags

Semantic versioning on `main` only. `v0.1.0` at the end of P5. Phases are not tagged; they are PR milestones, not releases.

## Protection rules on `main`

These are the target rules. They are switched on after the P1 pull request has run CI once, because a required status check whose name has never appeared on a pull request can block the first merge rather than guard it. Until then they are followed by hand.

- Pull request required, no direct pushes
- CI must pass: pytest and ruff, and the golden-set eval gate from P2
- No force push, no branch deletion
- Linear history: squash or rebase merges only

`data` is exempt from all of these, because the workflow writes it on every run.
