# P1 acceptance evidence

Run on 2026-09-14 against the registry at commit `0655f46`, on the live network
with a Firecrawl key present. Every claim below carries the command that
produced it, per the verification rule in `CLAUDE.md`. Where a bullet could not
be demonstrated, it says so and says why.

One limitation applies throughout: the machine this ran on carries no
`config/sources.local.yaml`, no `config/watched.local.yaml` and no
`config/profile.yaml`. The run therefore exercised the thirteen public sources
only, and the private-routing half of the design is covered by the test suite
rather than by this run. `tests/test_cli.py` and `tests/test_paths.py` hold
those invariants.

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

run report: data/runs/20260914T130630Z.json
```

Thirteen sources, thirteen fetched. Watch sources are fetched before discover
sources, which is the ordering `SPEC.md` section 3.6 requires.

Snapshots landed on the public side of the boundary, and the working tree stayed
clean:

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

$ find .private -name '*.md'
(no output: no private sources configured on this machine)

$ git status --short
(no output)
```

The run report is valid JSON and carries the run's shape:

```json
{
  "alerts": [{"check": "watch_without_deadline",
              "detail": "a watch source must carry a date and a deadline keyword",
              "source_id": "nus-research-scholarship"}],
  "pages_fetched": 13, "pages_changed": 0, "pages_unchanged": 13,
  "pages_failed": 0, "pages_skipped": 0,
  "started_at": "2026-09-14T13:06:46.419773+00:00",
  "finished_at": "2026-09-14T13:06:55.110301+00:00"
}
```

**Verdict: pass.**

## A second run reports every page unchanged

```
$ scholarship-watchdog fetch
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

$ grep -c unchanged /tmp/p1-run-2.txt
13
```

Thirteen of thirteen. Cleaning is deterministic across two runs on all thirteen
live pages, which is what makes change detection mean anything.

**Verdict: pass.**

## A source serving an error page trips a health check

This is the bullet that did not come out clean, and the detail matters more than
the verdict.

The specimen the plan named, `csc-campuschina` forced through `httpx`, no longer
produces the shape it was chosen for. It now refuses plain HTTP connections
outright:

```
$ scholarship-watchdog --repo-root /tmp/p1-probe fetch
csc-campuschina                  discover  failed

# run report: "status": "failed", "reason": "ConnectError", "chars": 0
```

That is the first of the two failure shapes in `SPEC.md` section 3.1, and it is
handled correctly: nothing is stored, no snapshot advances, the next run retries.
It exercises no health check by design.

Probing the registry for a source that still produces the second shape, an HTTP
200 carrying an error body, found one:

```
$ # each firecrawl source, fetched over plain httpx
knight-hennessy-deadlines      HTTP 200  raw=  51778  cleaned= 2519
nus-research-scholarship       HTTP 200  raw=    212  cleaned=    0
csc-campuschina                ConnectError
csc-studyinchina               HTTP 412  raw=   2555  cleaned=    0
```

`nus-research-scholarship` over plain `httpx` answers HTTP 200 with 212 bytes.
Running it through the pipeline twice:

```
$ scholarship-watchdog --repo-root /tmp/p1-probe fetch   # run 1
ALERT nus-research-scholarship: watch_without_deadline - a watch source must carry a date and a deadline keyword
nus-research-scholarship         watch     changed

$ scholarship-watchdog --repo-root /tmp/p1-probe fetch   # run 2
ALERT nus-research-scholarship: watch_without_deadline - a watch source must carry a date and a deadline keyword
nus-research-scholarship         watch     changed
```

An alert fires on both runs, so nothing passes silently. Two things are wrong
with how it fires.

First, the stored snapshot shows what the page actually is:

```
$ cat /tmp/p1-probe/data/snapshots/nus-research-scholarship/*.md
Request unsuccessful. Incapsula incident ID: 1779000770254816462-391498350889997179
```

That is an Imperva bot block served with HTTP 200. It is exactly the second
failure shape, and the `error_signature` check did not fire on it. The patterns
that check searches for are "enable JavaScript", "Access Denied" and bare
4xx/5xx text, and an Incapsula block matches none of them. The alert that did
fire, `watch_without_deadline`, fired because this happens to be a `watch`
source. **A `discover` source blocked the same way would pass silently**, which
is the outcome section 3.1 exists to prevent.

Second, the page reports `changed` on every run rather than `unchanged` on the
second. The Incapsula incident ID is regenerated per request, so the cleaned
markdown differs every time and the hash never settles. Section 3.1 argues that
hashing post-extraction text removes render timestamps and session tokens; this
token survives cleaning because it is the entire body. Under the P3 cron that is
a notification every week for a page that is permanently broken.

Neither defect is in the ordering guard, which holds: the alert fires on run two
with the snapshot already stored, so the check is running ahead of the change
gate as section 3.1 requires.

**Verdict: partial. The mechanism works and the ordering is correct. The
`error_signature` pattern list has a real gap, and a rotating token inside an
error page defeats change detection. Both are recorded as findings rather than
fixed here.**

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

Twelve clean, one failing, and the failure is not a deadline problem. It is the
opposite of the one recorded on 2026-09-05: `knight-hennessy-deadlines` was
reassigned to `firecrawl` that day because the page had gone client-rendered.
It is static again, measured above at HTTP 200 with 2519 characters of clean
text over plain `httpx`, so the registry now declares a paid fetcher for a page
that does not need one. That costs a Firecrawl credit every week and is
otherwise harmless.

No `discover` source was failed for lacking a date, which is the other half of
what this bullet asks.

The deadline side of the guard did fire, on the live run rather than in the
probe: `nus-research-scholarship` alerted `watch_without_deadline` on both
passes. Reading the rendered page shows the alert is correct in substance and
arrived by luck:

```
$ grep -C1 -iE 'deadline|1 January' data/snapshots/nus-research-scholarship/*.md
- The above stipend rates will take effect from 1 January 2026.
Application Deadlines
| August (Semester 1 Intake) | Please refer to faculty websites for respective application deadlines |
| January (Semester 2 Intake) | Please refer to faculty websites for respective application deadlines |

$ python -c "from scholarship_watchdog.deadlines import *; ..."
dates found     : ['1 January 2026']
keywords found  : []
has_deadline    : False
```

The page delegates its deadlines to faculty websites and states none of its own,
so alerting is right. It alerted for the wrong reason: `DEADLINE_PATTERN` does
not match the plural, so the page's own "Application Deadlines" heading was
never seen.

```
Application Deadline     -> True
Application Deadlines    -> False
application deadlines    -> False
closing dates            -> False
applications close       -> True
```

The consequence is worth stating plainly, because the obvious fix is the wrong
one. Teaching the pattern the plural would pair the delegated keyword with the
unrelated stipend date `1 January 2026`, `has_deadline` would return `True`, and
this genuinely broken watch source would go quiet. That is the failure direction
the module's own docstring warns about.

**Verdict: pass on behaviour, with two findings. The guard fires on a watch
source carrying no deadline and spares discover sources. The pattern has a
plural gap, and this source is misclassified: a page that delegates its
deadlines is a `discover` source, not a `watch` source.**

## Tests and lint pass in CI

```
$ gh pr checks 1
check   pass    21s   .../job/103983666444
check   pass    21s   .../job/103983683478

$ gh pr view 1 --json mergeable,mergeStateStatus
MERGEABLE
CLEAN
```

Locally, at the same commit:

```
$ .venv/bin/pytest -q
137 passed in 0.68s

$ .venv/bin/ruff check .
All checks passed!

$ .venv/bin/ruff format --check .
42 files already formatted

$ .venv/bin/python scripts/sync_diagrams.py --check
SPEC.md: in sync
weekly-run.svg: embeddable
finding-new-scholarships.svg: embeddable
```

**Verdict: pass.**

## Summary

| Acceptance bullet | Verdict |
| --- | --- |
| A manual run fetches every page, writes snapshots, produces usable JSON | pass |
| A second run reports every page unchanged | pass |
| An error page trips a health check rather than passing silently | partial |
| The role-aware probe fails a watch source with no deadline | pass, with findings |
| Tests and lint pass in CI | pass |

Findings raised by this run, none of them fixed here:

1. `ERROR_SIGNATURES` does not cover Imperva or Incapsula bot blocks, which are
   served with HTTP 200. A `discover` source blocked that way passes silently.
2. An error page carrying a per-request token reports `changed` on every run,
   which under the P3 cron is a weekly notification about a permanently broken
   page.
3. `DEADLINE_PATTERN` misses the plural forms "deadlines" and "closing dates".
   Fixing it in isolation would silence finding 4.
4. `nus-research-scholarship` delegates its deadlines to faculty websites and
   states none of its own. It is registered as `watch` and behaves like a
   `discover` source.
5. `knight-hennessy-deadlines` is declared `firecrawl` and is measurably static
   again, so the registry buys a rendered page it does not need.

Findings 1 and 2 are the two that would let a real breakage go unnoticed, and
both were fixed before P1 closed. Findings 3, 4 and 5 are registry and heuristic
questions carried forward.

### Findings 1 and 2, after the fix

`ERROR_SIGNATURES` now covers bot walls, and `PageOutcome` carries a `broken`
flag that is kept deliberately separate from `changed`. Re-running the same live
probe, this time registered as a `discover` source, which is the case that used
to pass silently:

```
$ scholarship-watchdog --repo-root /tmp/p1-probe2 fetch     # run 1
ALERT nus-research-scholarship: error_signature - empty content after cleaning: the page returned no readable text, which is what a JavaScript-only shell looks like
nus-research-scholarship         discover  changed

$ scholarship-watchdog --repo-root /tmp/p1-probe2 fetch     # run 2
ALERT nus-research-scholarship: error_signature - empty content after cleaning: the page returned no readable text, which is what a JavaScript-only shell looks like
nus-research-scholarship         discover  unchanged

$ # the committed row for that page
{'id': 'nus-research-scholarship', 'role': 'discover', 'status': 'ok',
 'changed': False, 'broken': True, 'chars': 0}
```

The alert fires on both runs and the row is marked broken. Note the wall serves
a different body per request: this run cleaned to nothing and tripped the
empty-extraction branch, where the earlier run served the Incapsula text and
tripped the new pattern. Both paths now alert.

Why `broken` is a separate field rather than forcing `changed` to false: the
first attempt at this fix did suppress `changed`, and that quietly broke
`test_a_stored_error_page_alerts_on_the_second_run_through_fetch_all`. That test
proves the health check runs ahead of the skip gate by asserting run two sees no
change and alerts anyway. Suppressing `changed` would have made its precondition
trivially true and retired the hardest guard in this stage. The hash stays a
truthful statement about the bytes; `broken` carries the judgement.

Both fixes were verified by removing them and watching the covering tests fail:
deleting the Incapsula patterns failed two tests, hardwiring `broken=False`
failed two others, and restoring each returned the suite to 142 passing.
