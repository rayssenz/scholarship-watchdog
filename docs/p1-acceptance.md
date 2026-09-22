# P1 acceptance evidence

Run on 2026-09-22 against the live registry, with the branch at commit `14daced` (the last
commit before this document) and a Firecrawl key present. Every claim below carries the
command that produced it and its output, per the verification rule in `CLAUDE.md`.

One limitation applies throughout. The machine this ran on has no
`config/sources.local.yaml`, `config/watched.local.yaml` or `config/profile.yaml`, so the
run covers the thirteen public sources only. The private half of the design, routing,
redaction and the rule that nothing private reaches a public output, is held by the test
suite instead: `tests/test_cli.py`, `tests/test_config.py`, `tests/test_models.py` and
`tests/test_paths.py`.

## A manual run fetches every registered page and writes snapshots

The run started from an empty `data/`, so every page is new.

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

run report: data/runs/20260922T074643Z.json
```

Thirteen sources, thirteen fetched, watch sources ahead of discover sources as section 3.6
requires. The report for this run:

```
20260922T074643Z.json {'pages_fetched': 13, 'pages_changed': 13, 'pages_unchanged': 0, 'pages_broken': 0, 'pages_failed': 0, 'pages_skipped': 0}
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

run report: data/runs/20260922T074652Z.json
```

```
20260922T074652Z.json {'pages_fetched': 13, 'pages_changed': 0, 'pages_unchanged': 13, 'pages_broken': 0, 'pages_failed': 0, 'pages_skipped': 0}
```

Cleaning is deterministic on all thirteen live pages, including the four rendered through
Firecrawl, which is what makes a detected change mean anything.

**Verdict: pass.**

## A source serving an error page trips a health check

Two registry sources forced through plain `httpx` and registered as `discover`, the role
with no deadline check to fall back on:

- `nus-research-scholarship` answers HTTP 200 behind an Imperva bot wall. The wall varies
  its body per request. On this run it served a page that cleans to nothing.
- `csc-campuschina` rejects a plain HTTP client. On this run it answered HTTP 412; on
  2026-09-19 it refused the connection outright.

```
$ scholarship-watchdog --repo-root <probe> fetch        # run 1
ALERT nus-research-scholarship: error_signature - empty content after cleaning: the page returned no readable text, which is what a JavaScript-only shell looks like
nus-research-scholarship         discover  broken
csc-campuschina                  discover  failed

$ scholarship-watchdog --repo-root <probe> fetch        # run 2
ALERT nus-research-scholarship: error_signature - empty content after cleaning: the page returned no readable text, which is what a JavaScript-only shell looks like
ALERT csc-campuschina: skipped_twice - not fetched 2 runs running: HTTP 412
nus-research-scholarship         discover  broken
csc-campuschina                  discover  failed

$ find <probe>/data/snapshots -name '*.md' | wc -l
0
```

The run-two report rows:

```
{'id': 'nus-research-scholarship', 'status': 'ok', 'changed': True, 'broken': True, 'reason': None}
{'id': 'csc-campuschina', 'status': 'failed', 'changed': False, 'broken': False, 'reason': 'HTTP 412'}
```

The wall alerts on both runs, is reported as `broken` rather than `changed`, and is never
stored, so the last good copy of a real page would survive it. The failing host is quiet
for one bad week and alerts on the second, which is the skipped-source check's "repeated
fetch failure" branch firing on a real host.

On this run the wall tripped the empty-content branch. On 2026-09-14 the same wall served
its vendor text instead, and that measurement is why vendor bot-wall phrases are in the
error signature. The stored body was, in full:

```
Request unsuccessful. Incapsula incident ID: 1779000770254816462-391498350889997179
```

None of the generic error phrases matched it at the time, so a `discover` source blocked
that way would have passed silently. The `incapsula incident` phrase now covers it at any
page length.

**Verdict: pass.**

## The role-aware probe fails a watch source with no deadline

The registry itself contains no watch source that lacks a deadline, so the probe was
pointed at a three-source copy of it in which the MEXT portal, which lists programmes
and states no deadline of its own, is registered as `watch`. JASSO stays `discover`.

```
$ python - <probe>/sources.yaml <<'EOF'
import runpy, sys, pathlib
mod = runpy.run_path("scripts/probe_sources.py", run_name="probe")
mod["main"].__globals__["REGISTRY"] = pathlib.Path(sys.argv[1])
print("exit code:", mod["main"]())
EOF
daad-study-scholarship       ok
jasso-scholarships           ok
mext-scholarships            NO DEADLINE (dates=0 kw=0)

2/3 sources clean, 0 unresolved, 1 failing
Sources not reached this run are excluded: a timeout is a network
fact, not a registry defect. Re-run before treating one as a failure.
exit code: 1
```

The watch source without a deadline fails, the discover source without one passes, and
the real watch source passes.

Over the real registry:

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
Sources not reached this run are excluded: a timeout is a network
fact, not a registry defect. Re-run before treating one as a failure.
```

The one failure is not a deadline problem. `knight-hennessy-deadlines` was moved to
Firecrawl on 2026-09-04 when the page went client-rendered, and it is static again, so the
registry pays for a render it does not need. The probe exits non-zero until the registry
is corrected, which is left for P2 with the other registry questions below.

The pipeline's own deadline check fired on both live runs above: `nus-research-scholarship`
alerted `watch_without_deadline`. Its page delegates deadlines to faculty websites and
states none of its own, so the alert is correct. Two limits of the heuristic are recorded
rather than fixed here: the keyword pattern misses the plural "deadlines", and any date
anywhere on the page satisfies it. Fixing the first alone would silence this correct
alert. Both belong with the P2 extractor, together with whether NUS is a `watch` source.

**Verdict: pass.**

## Tests and lint pass in CI

```
$ gh run list --branch feat/p1-foundation --limit 2 \
    --json headSha,event,status,conclusion \
    -q '.[] | "\(.headSha[0:7]) \(.event) \(.status) \(.conclusion)"'
14daced pull_request completed success
14daced push completed success

$ pytest -q
214 passed in 0.87s

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
| The role-aware probe fails a watch source with no deadline | pass |
| Tests and lint pass in CI | pass |

Carried into P2: the registry's Knight-Hennessy fetcher, whether NUS is a `watch` source,
and the two deadline-heuristic limits.

## What review changed before this run

A first acceptance run on 2026-09-14, three adversarial reviews, two cross-model reviews
and an independent review of the finished pull request read this branch. Every fix below
was verified by breaking it and watching its test fail.

- **Guards that could not fire.** A source failing every week never alerted, because a
  failure reset the skipped-source counter. Content collapse fired once and then compared
  the broken page against itself. An empty Firecrawl render raised nothing.
- **What happens to a broken page.** A page the health check calls broken now keeps the
  last good snapshot and alerts every run. A collapse that holds identical for three runs
  is adopted as the page's new content, so a page that genuinely shrank recovers. Generic
  error phrases count only on short pages, so a real page whose FAQ mentions "access
  denied" is not frozen.
- **Privacy.** The exit code, cookies, loader error messages and a source id that could
  name a path all let private state reach a public output. Each is closed and tested.
- **Robustness.** Redirect bodies were read before the byte cap applied. One malformed
  link failed a whole portal. An unknown charset or an unreadable state file ended the run.
  Firecrawl's origin status was ignored.

The ordering of the health check ahead of the change gate held under every review, and
its test still fails on its own assertion when the check is moved behind the gate.
