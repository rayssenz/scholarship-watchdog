# P1 acceptance evidence

Run on 2026-09-19 against the live registry, with the code at commit `48cb9ac` and a
Firecrawl key present. Every claim below carries the command that produced it and its
output, per the verification rule in `CLAUDE.md`.

One limitation applies throughout. The machine this ran on has no
`config/sources.local.yaml`, `config/watched.local.yaml` or `config/profile.yaml`, so the
run covers the thirteen public sources only. The private half of the design, routing,
redaction and the rule that nothing private reaches a public output, is held by the test
suite instead: `tests/test_cli.py`, `tests/test_config.py` and `tests/test_paths.py`.

An earlier acceptance run, on 2026-09-14, found gaps that the adversarial and cross-model
reviews then widened. What they found and how each was closed is summarised at the end.
Everything above that section is the final code's own output.

## A manual run fetches every registered page and writes snapshots

```
$ scholarship-watchdog fetch
ALERT nus-research-scholarship: watch_without_deadline - a watch source must carry a date and a deadline keyword
daad-study-scholarship           watch     changed
daad-helmut-schmidt              watch     changed
knight-hennessy-deadlines        watch     changed
nus-research-scholarship         watch     changed
daad-database                    discover  changed
csc-campuschina                  discover  changed
csc-studyinchina                 discover  changed
tsinghua-cgs                     discover  changed
ntu-postgrad-scholarships        discover  changed
mext-scholarships                discover  changed
jasso-scholarships               discover  changed
kyoto-scholarships               discover  changed
fulbright-foreign-student        discover  changed

run report: data/runs/20260919T130321Z.json
```

Thirteen sources, thirteen fetched, watch sources ahead of discover sources as section 3.6
requires. The run started from an empty `data/`, so every page is new. The report for this
run:

```
20260919T130321Z.json {'pages_fetched': 13, 'pages_changed': 13, 'pages_unchanged': 0, 'pages_failed': 0, 'pages_skipped': 0, 'started_at': '2026-09-19T13:02:54.799149+00:00'}
```

Snapshots landed on the public side of the boundary and nothing reached git:

```
$ find data/snapshots -name '*.md' | sort
data/snapshots/csc-campuschina/scholarships-index-html-c96810a880.md
data/snapshots/csc-studyinchina/index-739ca61761.md
data/snapshots/daad-database/deutschland-stipendium-d-a088ad3ec2.md
data/snapshots/daad-helmut-schmidt/deutschland-stipendium-d-d7ea5684ef.md
data/snapshots/daad-study-scholarship/deutschland-stipendium-d-896768d9a1.md
data/snapshots/fulbright-foreign-student/about-foreign-student-pr-17d82a1aa5.md
data/snapshots/jasso-scholarships/en-ryugaku-scholarship-j-7d51b51ff2.md
data/snapshots/knight-hennessy-deadlines/admission-preparing-your-3ccef12fe7.md
data/snapshots/kyoto-scholarships/en-current-how-to-financ-78ac1e9a0b.md
data/snapshots/mext-scholarships/en-planning-scholarships-c3f2aba655.md
data/snapshots/ntu-postgrad-scholarships/admissions-graduate-fina-1cf3af3e89.md
data/snapshots/nus-research-scholarship/scholarships-nus-researc-0c0eaa67a7.md
data/snapshots/tsinghua-cgs/en-info-1027-1117-htm-f604f768fe.md

$ git status --short
(no output)
```

**Verdict: pass.**

## A second run reports every page unchanged

```
$ scholarship-watchdog fetch
ALERT nus-research-scholarship: watch_without_deadline - a watch source must carry a date and a deadline keyword
daad-study-scholarship           watch     unchanged
daad-helmut-schmidt              watch     unchanged
knight-hennessy-deadlines        watch     unchanged
nus-research-scholarship         watch     unchanged
daad-database                    discover  unchanged
csc-campuschina                  discover  unchanged
csc-studyinchina                 discover  unchanged
tsinghua-cgs                     discover  unchanged
ntu-postgrad-scholarships        discover  unchanged
mext-scholarships                discover  unchanged
jasso-scholarships               discover  unchanged
kyoto-scholarships               discover  unchanged
fulbright-foreign-student        discover  unchanged

run report: data/runs/20260919T130338Z.json
```

```
20260919T130338Z.json {'pages_fetched': 13, 'pages_changed': 0, 'pages_unchanged': 13, 'pages_failed': 0, 'pages_skipped': 0, 'started_at': '2026-09-19T13:03:29.256503+00:00'}
```

Cleaning is deterministic on all thirteen live pages, including the four rendered through
Firecrawl, which is what makes a detected change mean anything.

**Verdict: pass.**

## A source serving an error page trips a health check

Two sources from the registry, forced through plain `httpx` and registered as `discover`,
which is the role that has no deadline check to fall back on:

- `nus-research-scholarship` answers HTTP 200 behind an Imperva bot wall, the second
  failure shape in section 3.1. The wall varies its body per request; on this run it
  served a page that cleans to nothing.
- `csc-campuschina` refuses plain HTTP connections outright, the first failure shape.

```
$ scholarship-watchdog --repo-root <probe> fetch        # run 1
ALERT nus-research-scholarship: error_signature - empty content after cleaning: the page returned no readable text, which is what a JavaScript-only shell looks like
nus-research-scholarship         discover  broken
csc-campuschina                  discover  failed

$ scholarship-watchdog --repo-root <probe> fetch        # run 2
ALERT nus-research-scholarship: error_signature - empty content after cleaning: the page returned no readable text, which is what a JavaScript-only shell looks like
ALERT csc-campuschina: skipped_twice - not fetched 2 runs running: ConnectError
nus-research-scholarship         discover  broken
csc-campuschina                  discover  failed

$ find <probe>/data/snapshots -name '*.md' | wc -l
0
```

The run-two report rows:

```
{'id': 'nus-research-scholarship', 'status': 'ok', 'changed': True, 'broken': True, 'reason': None}
{'id': 'csc-campuschina', 'status': 'failed', 'changed': False, 'broken': False, 'reason': 'ConnectError'}
```

The bot wall alerts on both runs, is reported as `broken` rather than `changed`, and is
never stored, so the last good copy of a real page would survive it. The refused host is
quiet for one bad week and alerts on the second, which is the skipped-source check's
"repeated fetch failure" branch firing on a real host.

**Verdict: pass.**

## The role-aware probe fails a watch source with no deadline

```
$ python scripts/probe_sources.py
csc-campuschina              browser-only, verified in registry
csc-studyinchina             browser-only, verified in registry
daad-database                ok
daad-helmut-schmidt          ok
daad-study-scholarship       ok
fulbright-foreign-student    ok
jasso-scholarships           ok
knight-hennessy-deadlines    FETCHER MISMATCH: declared firecrawl, measured httpx
kyoto-scholarships           ok
mext-scholarships            ok
ntu-postgrad-scholarships    ok
nus-research-scholarship     browser-only, verified in registry
tsinghua-cgs                 ok

12/13 sources clean, 0 unresolved, 1 failing
```

No `discover` source is failed for lacking a date. The one failure is not a deadline
problem: `knight-hennessy-deadlines` was moved to Firecrawl on 2026-09-04 when the page
went client-rendered, and it is static again, so the registry now pays for a render it
does not need.

The deadline side of the guard fired on both live runs above: `nus-research-scholarship`
alerted `watch_without_deadline`. The rendered page delegates its deadlines to faculty
websites and states none of its own, so the alert is correct in substance. Two limits of
the heuristic behind it are recorded rather than fixed here. The keyword pattern misses
the plural "deadlines", and any date anywhere on the page satisfies the check, so a page
reading "Last updated 1 January 2026. Deadlines are on faculty websites." would pass.
Fixing the first alone would silence this correct alert. Both belong with the P2
extractor, which reads deadlines properly, together with the question of whether NUS is
a `watch` source at all.

**Verdict: pass, with the two heuristic limits above carried into P2.**

## Tests and lint pass in CI

```
$ gh run list --branch feat/p1-foundation --limit 3 \
    --json headSha,event,status,conclusion,databaseId \
    -q '.[] | "\(.headSha[0:7]) \(.event) \(.status) \(.conclusion) \(.databaseId)"'
48cb9ac pull_request completed success 35444684937
48cb9ac push completed success 35444683182
c31f519 pull_request completed success 35443926455

$ pytest -q
181 passed in 0.78s

$ ruff check .
All checks passed!

$ ruff format --check .
43 files already formatted
```

**Verdict: pass.**

## Summary

| Acceptance bullet | Verdict |
| --- | --- |
| A manual run fetches every page, writes snapshots, produces usable JSON | pass |
| A second run reports every page unchanged | pass |
| An error page trips a health check rather than passing silently | pass |
| The role-aware probe fails a watch source with no deadline | pass, heuristic limits carried to P2 |
| Tests and lint pass in CI | pass |

## What the first run and the reviews changed

The 2026-09-14 run passed four bullets and only partly passed the third. Three
adversarial reviews and one cross-model review then read the whole branch. Every fix
below was verified by breaking it and watching its test fail.

- **Guards that could not fire.** A source failing every week never alerted, because a
  failure reset the skipped-source counter. Content collapse fired once, then compared
  the broken page against itself. An empty Firecrawl render raised nothing, while the
  same symptom over `httpx` alerted. All three now fire on every run, because a page the
  health check calls broken is stored exactly as a failed fetch is: not at all.
- **Error pages served as success.** Bot walls answered with HTTP 200 matched none of
  the error patterns. They are covered now, and a `broken` flag separate from `changed`
  stops a wall with a rotating incident ID from reading as new content every week.
- **Privacy.** The exit code changed when a private page broke. A cookie set by a
  private page rode along on the next public request. A loader error could quote a line
  of a private file or name a promoted programme. One malformed URL could end the run
  with a traceback. Each is closed and tested.
- **Resource limits.** Redirect bodies were read in full before the byte cap applied.
  Redirects are now followed by hand, and discovered links resolve against the page's
  final address.
- **Firecrawl.** The target page's own status is now read, so a maintenance page served
  with 503 is a failure rather than a snapshot.

The ordering of the health check ahead of the change gate, which section 3.1 exists to
establish, held under every review. Its test was restated for the new storage rule and
still fails on its own assertion when the check is moved behind the gate.
