# CLAUDE.md

> **Step 0 of any task: read [`SPEC.md`](./SPEC.md) section 5 (privacy), the section for
> the stage you are touching (3.1 to 3.5), and section 6 before any scaffold or phase
> work.** `SPEC.md` is the contract for this repo. Do not duplicate its details here, go
> there for the why. [`docs/process.md`](./docs/process.md) covers how the work is
> sequenced and which skill runs at which stage.

## Hard Rules

These are specific to this repo. **When these rules, the Behavioral Rules below, and
`SPEC.md` disagree, the order is: Hard Rules, then `SPEC.md`, then Behavioral Rules.** A
protocol that `SPEC.md` names is not a speculative abstraction, and a multi-file edit that
restores agreement between documents is not scope creep.

### Never commit anything shaped by the eligibility profile

The test for "shaped by": *would this file's content change if `profile.yaml`,
`sources.local.yaml` or `watched.local.yaml` changed?* If yes, it belongs in
`data/private.age`, never in the working tree or the public `data` branch.

`SPEC.md` section 5 lists the cases found so far: scores, tiers, notifications, the
notified log, the auto-grown watch list, every verified leaf record, verification state,
and snapshots or report rows from private sources. **The list is not closed** — it grew
four times during design, each time because someone matched against the list instead of
applying the test.

Before writing any new output path, run `git check-ignore -q <path>` and expect either
success or a reason. Never copy a value from a private config into a fixture, an example,
a docstring or a commit message; fixtures use `config/profile.example.yaml` and public
sources only. P3 owes an automated test for the invariant in section 5; until it exists,
this check is manual.

Anything touching secrets, the profile, `.gitignore` or CI gets a security review
before it is committed.

### No implementation code before the spec covering it is settled

**Do not create `src/` or `pyproject.toml` while `SPEC.md`'s header lists anything as
outstanding before implementation.** The header currently lists nothing, so P1 may
proceed. If a future header names an outstanding item, say so and stop.

If something unanticipated surfaces mid-build, stop and *propose* a spec amendment rather
than writing one unilaterally. Once accepted, the spec edit and the code it covers land in
one commit, per [`docs/git-conventions.md`](./docs/git-conventions.md).

### Documents must agree

`SPEC.md`, `docs/`, `diagrams/` and `config/` describe one system. A contradiction between
them is a defect, fixed as one across every file it touches.

### Verification before assertion

No claim that something works without the command output that shows it. This applies to
test runs, registry audits, and statements written into documents. A sentence asserting
that a number is accurate is itself a claim requiring output.

---

## Behavioral Rules

### 1. Think Before Coding
**Don't assume. Don't hide confusion. Surface tradeoffs.** State assumptions explicitly,
present multiple interpretations rather than choosing silently, and ask clarifying
questions before proceeding. **When a decision is ambiguous or architecturally
significant, STOP and present options — do not pick silently or proceed on a guess.**

### 2. Simplicity First
**Minimum code that solves the problem. Nothing speculative.** Avoid unrequested features,
single-use abstractions, unnecessary flexibility, and error handling for impossible
scenarios. If you write 200 lines and it could be 50, rewrite it.

### 3. Surgical Changes
**Touch only what you must. Clean up only your own mess.** When editing existing code,
don't improve unrelated sections or refactor working code. Match existing style. Remove
only the unused code your changes created. Every changed line should trace directly to
the request.

### 4. Goal-Driven Execution
**Define success criteria. Verify at every checkpoint.** Transform tasks into verifiable
goals with clear success metrics. For multi-step work, outline the steps up front in the
explicit format below, then work through them. On non-trivial tasks, **pause at each
checkpoint for review instead of looping autonomously to completion.**

```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
```

In a non-interactive run (cron, CI) where rule 1 cannot ask, state the
assumption in the output and continue.

---

## Project Context & Conventions

An autonomous agent that monitors scholarship websites weekly on a GitHub Actions cron,
extracts opportunities with an LLM, scores them deterministically against a private
eligibility profile, and emails only new or materially changed high-fit findings. Single
user. Python 3.12, `pyproject.toml`, ruff, pytest — none of which exist yet.

Phase status lives in the table at the end of `docs/process.md`. This file carries no
phase state, so it does not go stale when a phase ships.

### Where each change belongs

- **Anything a stage does, or any contract between stages → `SPEC.md` first**, then the
  code. Sections 3.1 to 3.5 map one to one onto the pipeline stages; 3.6 covers state
  persistence and the write protocol.
- **Pipeline code → the layout `SPEC.md` section 6 names for P1.** One package per stage,
  each behind the protocol its SPEC section defines.
- **Sources and tuning → `config/`.** The registry, the scoring weights and every
  threshold in the `SPEC.md` section 3.3 table are configuration, not constants in code.
  Private files are gitignored; those a human owns ship a committed `.example` twin,
  while `watched.local.yaml` is machine-written and has none by design.
- **Design decisions with alternatives considered → `docs/`.** A decision that took
  research gets a note with a dated log, as `docs/model-selection.md` does.
- **Diagrams → edit the `.mmd`, regenerate the SVG and PNG, then run
  `python scripts/sync_diagrams.py`.** The `.mmd` files are the single source of truth.
  SPEC embeds them as native mermaid and the sync script stops that copy drifting; README
  embeds the rendered SVGs, which display in any viewer. The SVGs carry a white background
  so they read on GitHub dark mode. Never hand-edit the SVG, PNG or excalidraw output.

### Testing

Each stage's tests cover the guards its `SPEC.md` section names, not only the happy path.
Test the failure the spec names: a watch source losing its deadline must alert, a
candidate link to a login page must be rejected, a promoted page failing three times must
evict rather than alert a fourth time.

Scoring carries no LLM call and no I/O, so it is a pure function of
`(record, profile, weights, run_date)` and is tested as one.

### Verify (run from repo root)

```bash
pip install -e ".[dev]"                   # once, into a Python 3.12 environment
ruff check . && ruff format --check .
pytest                                    # the golden-set eval gate joins in P2
python scripts/sync_diagrams.py --check   # SPEC mermaid matches .mmd; SVGs carry a background
python scripts/probe_sources.py           # registry gate, live network
scholarship-watchdog fetch                # one real pass over the registry, live network
```

The probe hits the live network, so results vary with host availability, and it is not a
CI gate. A source it cannot reach this run is reported and excluded rather than failed,
since a timeout is a network fact and not a registry defect. A browser-only source is
judged against the `verified` note the registry carries from a hand-check.

### Git

Trunk-based, short-lived branches, Conventional Commits on one line. A single-idea branch
is squash-merged; a phase branch, whose commits are each one idea, is rebase-merged. Branch
protection on `main` is switched on once CI has run on a pull request. The `data` branch is an orphan holding runtime state and is never
merged. Full rationale in [`docs/git-conventions.md`](./docs/git-conventions.md).

Documents that ship to a reader (`README.md`, `SPEC.md`, `docs/`) get an editing pass
for tone and concision, and avoid em dashes. Commit messages, code comments and this
file are exempt: their formatting is doing a job.
