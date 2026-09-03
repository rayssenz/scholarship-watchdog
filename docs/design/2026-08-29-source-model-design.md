# Source model: watch and discover

Design, 2026-08-29. Supersedes the single-role registry in `SPEC.md` section 3.1.

## Why this exists

The first registry listed fourteen URLs and asked the extractor to pull a deadline from each. A design-review audit fetched all eleven reachable ones and found that **none contained a deadline**. Every URL was an index or overview page; the dates lived a click deeper.

Because the extraction schema is optional-typed, this would not have failed loudly. It would have produced schema-valid records with null deadlines, reported success every week, and stayed wrong until a real deadline passed unnotified. That is the failure the system exists to prevent, so the source model is redesigned before any code is written.

The audit findings were not fourteen broken URLs. They were fourteen sources doing a job they are good at, labelled as a job they cannot do.

## What the system is for

Deadline safety first, discovery second. Missing a date on a programme already known is unrecoverable; failing to surface an unknown programme is a lost opportunity but not a broken promise. The design weights the two accordingly.

## Two roles

Every source declares a `role`, and the role determines what the system demands of it.

```yaml
- id: daad-study-scholarship
  role: watch
  url: https://www2.daad.de/deutschland/stipendium/datenbank/en/21148-scholarship-database/?detail=50026200
  country: DE
  # verified 2026-08-29: "1 October 2027", 9 deadline keywords

- id: ntu-postgrad-scholarships
  role: discover
  url: https://www.ntu.edu.sg/admissions/graduate/financialmatters/scholarships
  country: SG
```

**`watch`** guards a deadline. One programme, one page, checked weekly. A watch source that stops yielding a deadline is broken by definition and alerts. This is the guard that would have caught the original defect.

**`discover`** finds programme names and links on portal and index pages. Missing dates are expected and never alert. Its records are capped at `shortlist` until promotion gives them a real page.

A discover source that has never yielded a stub is flagged once in the digest after its first successful extraction. Without this, `discover` has no equivalent of the watch role's guard, and a portal that fetches cleanly while producing nothing looks exactly like a quiet week. That is the project's original defect wearing a new role, and the registry already contains a source it would catch: `daad-database` is annotated "structurally incompatible, a search application, not a document", and would yield zero candidates forever while every health check passed.

**Discover-role fetching preserves hyperlink targets.** Default trafilatura cleaning discards `href` attributes along with navigation, which would leave the extractor reading programme names as plain text with no link to promote. Link-preserving cleaning applies to discover sources only, and its consequence is accepted deliberately: hrefs join the hashed text, so a portal that rotates tracking parameters on its links will register as changed more often. Watch sources keep default cleaning, since they are hashed for deadline changes and links are noise there.

`expects_dates` disappears as a separate flag. It was a patch over the missing distinction; role expresses it directly.

Most of the existing registry becomes `discover`, which is what those pages were always good for. DAAD illustrates both roles on one domain: the scholarship overview is a menu of programmes, and `?detail=50026200` is the Study Scholarship itself, carrying its own deadline.

## Promotion

Discovery surfaces a programme and, where the listing links to it, that programme's own URL. A candidate link is verified, and if verification produces a record above the promotion tier, the entry joins the watch list. From the following run it is fetched weekly like any hand-picked watch source.

A promoted entry is an ordinary watch source. There is no second code path and no special case.

**Promotion is decided on the leaf record, not the discovery stub.** This ordering is load-bearing. A discovery stub carries a programme name, a link, and nulls for everything else; under the null policy those nulls cap it at `shortlist` and block `act-now`, so a stub can never clear an `act-now` threshold and promotion would be unreachable by construction. Scoring the stub also measures almost nothing about the programme.

**The link needs somewhere to live.** The extraction schema has one URL field, `source_url`, and it holds provenance: the portal the record came from. Nothing can carry the leaf URL a stub points at. The schema therefore gains a nullable **`candidate_url`**, populated only by discover-role extraction and excluded from both hashes. A null `candidate_url` is exactly the unpromotable case. This contradicts the "schema untouched" claim an earlier draft of this document made, and that claim is retracted below.

Verification does the real work:

1. Fetch `candidate_url`, using **the fetcher of the portal that produced it**. A JS-rendered portal links to JS-rendered pages; verifying those with `httpx` would systematically reject every candidate from the CSC and NUS portals, whose audit notes already record that a plain fetch returns nothing.
2. Run the **real extractor** on the fetched page. A regex date-scan is not sufficient: the registry's own PKU note records twelve dates and zero deadline keywords on a page whose dates are news timestamps, so a date-scan would admit exactly the wrong pages.
3. Score the resulting record against the profile.
4. Promote when that record passes the promotion gate, defined below.

The stub's only job is to surface the link.

### Verify and gate are separate operations

Treating them as one event was the defect in the previous draft: the gate could only be evaluated at the instant of the verification fetch, so a candidate held back by the cap, unblocked by a profile edit, or whose page later gained a deadline could never be reconsidered. Splitting them makes all three fall out of one rule.

**Verify** is expensive and runs rarely: one fetch and one extraction per canonical URL. **Gate** is free and runs every run over stored leaf records, alongside the score recomputation `SPEC.md` section 5 already performs.

Verification state is keyed by **canonical URL**, not by stub identity, and lives in the private bundle:

| State | Meaning | Exit |
| --- | --- | --- |
| `unverified` | queued | verification attempt |
| `transient(n, next_run)` | fetch or extraction failed recoverably | retry until `transient_max_attempts`, then `unreachable` |
| `verified(leaf_hash, at)` | leaf fetched, extracted, stored | re-verify only if the stub's `candidate_url` changes, or on the slow cadence below |
| `unreachable(at)` | attempts exhausted, or evicted after promotion | only when a stub's `candidate_url` changes |

Keying on canonical URL rather than stub identity matters because programme names drift between weekly extractions of a churning portal. Stub identity keyed state would re-verify the same CSC leaf once per name variant, paying a Firecrawl render each time.

**Slow re-verification.** A `verified` leaf whose record has a null deadline is re-verified every eighth run, bounded. This is the common state of a scholarship page between cycles: "applications for 2027 open in September" carries no date in August and a real one in September, and nothing about the portal changes when it does. Without the slow cadence that page is verified once, rejected, and never looked at again.

**The queue** admits every canonical URL in `unverified` or a due `transient`, ordered FIFO by first sighting, up to `verification_cap` per run. FIFO rather than score-ordered is deliberate: ordering by score would make the sequence of verification fetches a function of the private profile, and the fetches are visible in timing and credit consumption.

Deriving verification from "changed this run" would strand candidates permanently, and the scenario is concrete. A first run over the CSC portal yields thirty stubs; the cap halts verification after eight; the portal snapshot advances because the portal itself was processed. Next week the portal is unchanged, the skip gate stops it, no new stubs appear, and the twenty-two remaining candidates are never looked at again. That is the round-one finding rebuilt: work derived from change events, behind a gate that then prevents it firing.

Verification costs one fetch and one extraction per candidate. Both count against the budget alarm, which must therefore estimate **fetch credits as well as tokens**: a thirty-candidate first run over a Firecrawl portal spends thirty renders before a single model token, and an alarm that counts only extraction spend would miss it entirely.

### The promotion gate is fitness, not urgency

The gate runs every run over every stored leaf record not already watched. It passes when the record has no hard blockers, a usable deadline, and fit at or above `promotion_min_fit`. A usable deadline means `deadline` is non-null, **or** it has passed on a source with `recurs: annual`.

Gating on the `act-now` tier was the obvious choice and is wrong, for a reason `SPEC.md` section 3.3 already argues against itself. A recurring programme whose deadline has passed routes to `next-cycle`, and the spec calls that the most valuable find in the system: next year's target with twelve months to prepare recommendation letters and embassy paperwork. `next-cycle` sits below `act-now`, so an urgency gate would make the spec's most-valued case permanently unpromotable, and permanently so, since a passed deadline is static and the portal listing rarely changes. The symmetric failure is a strong far-future match that only promotes once its deadline is near, at which point the watch role is guarding a date it can no longer usefully guard. A watchdog that starts watching only when it is already too late has inverted its own purpose.

A record that passes the gate with no capacity under the watch-list cap is **held**, not rejected. It is reported once when first held, and promotes without re-verification when capacity appears.

### Identity across promotion

A leaf record is a **new identity, not a replacement for its stub**. It takes its own `source_id`, derived from its canonical URL as `leaf:<sha256(canonical_url)[:12]>`, and its records live in the private bundle.

An earlier draft had the leaf record hash under the portal's `source_id` so that it would replace the stub row. That rebuilt the exact property `SPEC.md` section 3.4 withdrew: it requires `normalize(program)` extracted from the leaf page to equal `normalize(program)` extracted from the list page, and both are LLM output from different documents. "Nanyang President's Graduate Scholarship" on the portal against "NPGS (Nanyang President's Graduate Scholarship) – Admission Requirements" on the leaf normalises to two different strings, producing the orphaned stub the measure existed to prevent. It also meant the next portal re-extraction would overwrite the leaf record's real deadline with the stub's null.

Three measures replace it:

- **Canonical URL is the identity of a candidate**, computed as: scheme and host lowercased, fragment dropped, tracking parameters dropped, meaningful query preserved, trailing slash normalised. Query preservation is not optional, since DAAD leaf pages are `?detail=50026200` and naive query-stripping would collapse every DAAD programme onto one URL. The same canonicalisation is applied to hrefs before discover-page hashing, which removes the link-churn cost an earlier draft accepted.
- **Dedup runs at queue admission and again at promotion**, across the union of all three config files. Admission-time dedup is what prevents paying for the same leaf twice when two portals list it.
- **The alias `stub_hash → leaf_hash` is written at verification time**, not at promotion. The stub row is never mutated; it remains a shortlist record marked merged and is excluded from digests. The notified log is read through the alias in both directions, so a promoted entry inherits the stub's notified state and does not re-notify as new.

If a leaf page yields several records, for instance a master's and a doctoral variant, the gate passes when any one of them passes, the watch entry is the URL, and every record from that page aliases to the stub.

**Promoted entries inherit** `fetcher`, `institution` and `recurs` from the discovering portal. `fetcher` is load-bearing rather than tidy: verification proves the page works with the portal's fetcher, so watching it with the registry default would take a Firecrawl-only NUS leaf, fetch it weekly with `httpx`, collect zero characters, trip the content-collapse check for three weeks and evict an entry that was never broken.

**`country` is inherited only from single-country portals.** Fulbright's international site links to roughly 160 national commission pages, and MEXT's overview links to embassies worldwide; a commission page inheriting `US` would receive a US date-convention hint and transpose its `DD/MM` dates, which `SPEC.md` section 3.2 names the highest-risk failure in the system. A discover source may therefore set `country: null`, in which case leaf extraction infers the country from the page. Multi-country portals also flood the queue: 160 candidates at a cap of eight is twenty weeks of verification, so such portals carry an optional `candidate_pattern` restricting which links are admitted, and the user's own commission page remains a hand-picked watch source in the local file regardless.

**Eviction.** The watch list has admission and needs an exit. A promoted entry whose page fails to fetch on three consecutive runs is demoted, removed from the watch list, and reported in the digest. Without this, a portal that linked to a cycle-specific application form (`/apply/2026`) produces an entry that 404s and alerts every week forever.

A failure means an HTTP error, a timeout, or an error-signature health check. A source **skipped** for a missing Firecrawl key is not a failure and does not touch the counter: counting skips would evict every promoted JS-rendered page three weeks after a key expired, which is precisely when the user least wants their watch list quietly emptied. The counter lives in the private bundle, since a stateless runner cannot otherwise remember week two.

During weeks one and two the health checks also alert on the same failures. That is accepted: two alerts then eviction is the intended sequence, and suppressing the health check would hide a failure that might not be terminal. Eviction moves the candidate's canonical URL to `unreachable`; it re-enters the queue only when a stub's `candidate_url` changes, which is the signal that the portal now points somewhere different.

The watch list is capped, default 25. At the cap, passing candidates are held rather than dropped, as described above. A profile edit that hard-blocks an already-promoted programme does not demote it; the entry keeps being fetched. For one user and a list of this size that is accepted rather than solved, and it is stated so the omission is a decision.

**When there is no link**, promotion cannot happen. The find appears in the digest marked unpromotable, and the user may add a page by hand. This limit is reported rather than worked around. The alternative considered was falling back to watching the programme's row on the portal page itself, which doubles the promotion paths and their failure modes to cover a case not yet observed. It is documented as the upgrade if run reports show unpromotable finds are common.

**Digest semantics.** An unpromotable or never-promoted find is reported **once**, on the run that discovered it, not weekly. Repeating them turns the digest into noise and trains the user to ignore it. "Once" is derived from `first_seen == this run` rather than stored as a terminal flag: a flag would have to be cleared if the portal later adds the link, and a flag nobody remembers to clear is how a promotable candidate becomes permanently invisible.

**Without an `age` recipient the system is report-only.** Discovery and verification run, findings reach the digest, and nothing is promoted or persisted privately, because there is nowhere private to persist it. This is what a stranger cloning the repository gets, and it degrades rather than crashes.

## Where sources live

Auto-promotion is driven by scoring, and scoring is derived from the private profile. A publicly-committed watch list that grew from eligibility would leak citizenship by inference, exactly as a public score table or a public notification would.

| File | Contents | Committed |
| --- | --- | --- |
| `config/sources.yaml` | discovery portals and hand-picked watch pages | public |
| `config/sources.local.yaml` | country-specific sources: the user's Fulbright commission, their Japanese embassy | gitignored |
| `config/watched.local.yaml` | auto-promoted entries | gitignored |

The public file is what a stranger clones and runs. The two local files sit beside `profile.yaml` under the same rule, and ride the `data` branch encrypted, as the notified log already does.

This is the third time the same leak has been found in a different place. It is therefore stated once as a rule rather than rediscovered a fourth time:

> **Anything shaped by the profile is private.** Not only the profile, but the scores, the notifications, the notified log, and the auto-grown watch list.

Fulbright and MEXT resolve under this rule without special handling. Their deadlines are set per country by national commissions and embassies, so the international pages are discovery sources and the user's own country pages are watch sources in the local file. The seed CSC embassy notice is a local source for the same reason: naming an embassy names a country.

### The rule applies to what a run produces, not only to what it reads

Moving the watch list into a private file protects the list and nothing else. Every source the system fetches also produces a page snapshot, a record carrying its `source_url`, and a row in the run report, and `SPEC.md` section 3.6 commits all three to the public `data` branch. A promoted or local source therefore republishes, in public, exactly the entry that was moved out of `sources.yaml` to hide it: a snapshot directory named for the programme, a record pointing at its URL, and a fetch row proving it is watched. A snapshot of the user's embassy page names their country outright.

Private sources therefore carry private artifacts:

| Artifact | Public source | Private or promoted source |
| --- | --- | --- |
| Page snapshot | `data/snapshots/<source_id>/…` | inside the private bundle |
| Extracted record | `data/records.jsonl` | inside the private bundle |
| Run-report row | per-source detail | omitted entirely; counts go to the email digest |
| Alias entry | `config/aliases.yaml`, public | inside the private bundle |
| Golden eval snapshot | drawn from public sources only | never used as a golden case |

The golden set deserves the explicit exclusion because an embassy page is the natural candidate for the `DD.MM` date-format test case the model-selection work called for, and committing it as a fixture would leak the country through the test suite.

**One opaque bundle, not per-file encryption.** All private state lives in a single `data/private.age`: private and promoted snapshots, every leaf record, the watch list, the local aliases, the notified log, the per-source failure counters eviction depends on, and the **per-URL verification state**. Verification state belongs here because "permanently rejected" means "scored below the gate", which is profile-derived; storing it on the public stub row would publish which candidates the profile rejected.

Every leaf record is private, not only those from private portals. Classifying leaf privacy by the discovering portal would split the store into two code paths and raise a merge question at every lookup, and for one user the saving is nil. The rule is simpler stated whole: **stubs follow their portal; everything downstream of a `candidate_url` fetch is private.**

The invariant an implementer can test, and should: *after any run, no file in the public checkout has content that depends on `profile.yaml`, `sources.local.yaml`, or `watched.local.yaml`.*

Three reasons file-level encryption fails here:

- **Paths are not encrypted.** `data/snapshots/fulbright-xx-commission/deadline.md.age` protects its contents and publishes the country in its name.
- **Per-line encryption breaks the store.** `records.jsonl` is valued for being diffable with sorted keys; encrypting rows individually destroys that and still leaks a record count per source.
- **Shape is a side channel.** A per-file layout lets an observer watch which private file grew in which week.

A single bundle shows one opaque blob changing on every run whether or not anything happened, which is the point.

**Nothing about private sources reaches the committed run report,** not even aggregate counts. The public report already says which public portals changed each week; an aggregate line saying the private watch list grew that same week lets an observer correlate the two and conclude that some programme on the changed portal cleared the profile's blockers. Private counts go to the email digest, which is already private.

**Verification artifacts inherit the privacy class of the discovering portal.** A candidate found by the user's embassy portal produces a leaf record whose URL names the country, so it belongs in the bundle even before promotion.

**Ownership is split, because two writers touch these files.** `sources.local.yaml` is human-owned: the local copy is truth, the run never writes it, and a sync step encrypts it into the bundle. `watched.local.yaml` is machine-owned: the bundle is truth, promotion writes it, and hand edits go through a sync command rather than a local edit that a later run would overwrite.

**Key loss is recoverable and should be documented rather than feared.** The `age` recipient key lives in Actions secrets and in the user's password manager. Losing it costs the watch list, the notified log and the private snapshots: recovery is re-seeding the watch list and accepting one duplicate digest. No scholarship data is unrecoverable, because every private record can be re-extracted from its source.

**Decrypted state never enters the working tree.** The obvious implementation is the dangerous one: decrypt into `data/`, work there, re-encrypt at the end, and a run that dies in between leaves plaintext exactly where the next `git add` will pick it up. The bundle is therefore decrypted to a scratch directory outside the checkout; no commit step runs `if: always()`, since a step that commits regardless of outcome eventually commits a half-cleaned failure; and `.gitignore` names the scratch path so a mistake in either rule still fails closed.

**The bundle is compressed before encryption.** `age` output is nondeterministic, so every run rewrites the whole blob and git stores it whole: about a megabyte of plaintext becomes tens of megabytes a year of undeltable history. `tar`, then `zstd`, then `age` costs one pipeline stage and cuts that several-fold.

The cost of the single bundle is worth naming rather than discovering. `SPEC.md` section 3.6 justifies the `data` branch partly as a public auditable record of what changed and when; for promoted and local sources that is now false, because their history is inside an opaque blob nobody can inspect, including the portfolio reader this repository exists for. The audit trail for private sources is the digest, and that is the price of the privacy rule.

Two sentences in `SPEC.md` section 5 become false under this design and must be corrected: that the `data` branch "stores snapshots and raw records only", and that "extracted scholarship records are public information and are safe to commit". Both hold for public sources and fail for private ones.

This is the fourth instance of one leak. The rule was correct; the failure was applying it only to inputs. The corrected form is: **anything shaped by the profile is private, including every artifact produced by acting on it.**

## Numbers this design requires

Six values were left implicit across earlier drafts, which meant an implementer would invent them. They are named here and belong in config, not in code.

| Value | Default | Meaning |
| --- | --- | --- |
| score range | 0 to 100 | soft signals accumulate on this scale |
| tier boundaries | `act-now` 70+, `shortlist` 40 to 69, `archive` below 40 | `next-cycle` is orthogonal: any passed-deadline recurring record, whatever its points |
| `notify_tiers` | `{act-now, next-cycle}` | which tiers reach the digest |
| `promotion_min_fit` | 55 | gate threshold; below the notify tier so a promising find is watched before it is urgent |
| `verification_cap` | 8 per run | primary brake on verification cost |
| `transient_max_attempts` | 3 | after which a canonical URL becomes `unreachable` |

`promotion_min_fit` sitting below the notification threshold is deliberate. A programme worth watching is not the same as a programme worth emailing about: the watch role exists to catch a deadline *moving*, which requires watching before the record is urgent.

**One notification trigger was missing.** `SPEC.md` section 3.5 has "new record above threshold" and "content hash changed, still above threshold". The payoff case for this whole design fits neither: a promoted `next-cycle` entry whose new-cycle deadline appears has changed, and has moved from below the threshold to above it. A row is added: **content hash changed, now above threshold when it was not, notify as new.** Without it, the most valuable event in the system is silent.

## Failure modes and guards

| Failure | Guard |
| --- | --- |
| A watch page loses its deadline after a site change | A watch source yielding no deadline alerts. This is the original defect, now caught. |
| The watch list grows without bound | A cap on auto-promoted entries; the digest reports list size weekly. At the cap, passing candidates are held and reported once rather than dropped silently. |
| A promoted link is a login page, a PDF, or dead | Verified by real extraction before promotion; rejected and reported instead of added. |
| A promoted page later dies | Three consecutive failed fetches demote the entry and report it, rather than alerting weekly forever. |
| The same programme discovered on two portals | Promotion deduplicates on canonical URL across all three config files, not on stub hash. |
| A promoted programme re-notifies as new | The stub-to-promoted alias is written automatically at promotion time. |
| Verification fails transiently and never retries | Verification state is keyed by canonical URL and persisted; `transient` re-queues up to `transient_max_attempts`. |
| A leaf page gains its deadline after the cycle opens | `verified` leaves with a null deadline are re-verified every eighth run; the portal need not change. |
| A candidate passes the gate while the watch list is full | Held, not rejected; promotes when capacity appears, without paying for verification again. |
| A profile edit makes a rejected leaf eligible | The gate runs every run over stored leaf records, so re-evaluation needs no re-fetch. |

## What this does not change

The model choice and its benchmark, JSONL as the committed format for public records, email-only notification, and the skip-gate cost control are untouched. This design changes what the system points at, how an entry enters the watch list, and how private artifacts are stored.

Two claims in earlier drafts of this document were wrong and are retracted here rather than quietly edited away:

- **"The dual-hash design is untouched."** Promotion crosses a `source_id` boundary and the identity hash is built from `source_id`, so the store's identity layer is directly affected.
- **"The extraction schema is untouched."** Promotion needs somewhere to put a candidate link, and no existing field can hold one; the schema gains `candidate_url`. This claim was written one paragraph after retracting the first, which is a fair indication that "untouched" assertions in this project deserve checking rather than trusting.

## Sections of SPEC.md that change

| Section | Change |
| --- | --- |
| 3 diagram | the mermaid source and `diagrams/` gain the discover, verify and promote loop, and the public/private artifact split |
| 3.1 `fetch` | roles replace `expects_dates`; `follow_links` removed; link-preserving cleaning for discover; verification fetch and fetcher inheritance |
| 3.2 `extract` | `candidate_url` added, nullable, discover-only, excluded from both hashes |
| 3.3 `score` | tier boundaries defined; promotion gate stated as a fitness predicate covering `next-cycle` |
| 3.4 `store` | identity across promotion; canonical URL defined; auto-written aliases in the private bundle |
| 3.5 `notify` | notification threshold named; alias-aware notified-log lookup; digest semantics for unpromotable finds and private counts |
| 3.6 state | private bundle replaces per-file encryption; decrypt-at-start; promotion write added to the ordered protocol |
| 4 production | verification cap; budget alarm covers fetch credits as well as tokens; no private-source rows in the committed report |
| 5 privacy | two sentences corrected; private artifact table; bundle rationale; ownership and key-loss policy |
| 6 phases | P1 grows; promotion lands in P2 alongside extraction and scoring |

Two further consistency edits fall out: `docs/process.md`'s stage-2 gate still names `expects_dates`, a flag this design deletes, and `config/sources.yaml`'s field-reference comments document the same flag. Both are part of the registry rebuild already scheduled in that file's STATUS header.

**The run is a single pass.** A fresh Actions runner has no gitignored files: the local configs exist only inside the bundle on the `data` branch, so the run decrypts at start and writes the bundle once at the end.

```
0. decrypt data/private.age        absent      -> start from empty state
                                   corrupt     -> abort and alert, never treat as empty
1. fetch watch sources             public, local, promoted        [budget]
2. fetch discover sources                                         [budget]
3. extract changed pages           discover -> stubs with candidate_url
4. verification queue              FIFO, <= verification_cap      [budget]
5. score everything; run the gate over stored leaf records; decide promotions and evictions
6. send digest
7. write notified log
8. advance snapshots               per page, only on that page's success
9. write bundle; commit; push --force-with-lease
```

**Watch fetches precede discovery and verification** because they share one budget. "Deadline safety first" is a principle in section 2 of the spec and has to be enforced by ordering: a thirty-candidate Firecrawl queue running first could exhaust the budget before the DAAD detail pages are fetched, starving the guarantee the system exists to provide.

**A budget halt skips remaining work and continues to step 5.** It never aborts the run. Aborting would discard the verifications already paid for, so the next run would repeat the same fetches, spend the same credits and halt at the same point: a livelock that costs money weekly and never progresses.

An earlier draft argued about ordering the promotion write against the notification send. That argument was vacuous. Promotions, the notified log, the counters and the private records all live inside one blob written once at step 9, so no ordering exists between them. The real unit of crash-safety is the whole bundle, and the direction is unchanged: a crash before step 9 loses that run's private writes, and every consequence is a duplicate, never a miss.

**An undecryptable bundle is not an empty one.** Treating a corrupt blob as empty state would rebuild the watch list from nothing and push that over the real one, destroying it. Absent means first run; corrupt means stop.

## `follow_links` is dropped

`SPEC.md` section 3.1 offers a bounded one-level crawl for index pages, and the `discover` role now serves the same pages by a different mechanism. Discovery plus promotion subsumes crawling: it reaches the same leaf pages, and it verifies each one before adopting it, which the crawl does not. The sole annotated `follow_links` candidate in the registry, CSC, is a discover source.

An earlier draft left this open for the registry rebuild to settle. That was a mistake in sequencing rather than in judgement: `follow_links` is section 3.1 machinery and section 3.1 lands in P1, so leaving it open leaves P1's scope ambiguous on crawl support, per-page snapshot keying and seed-mode granularity. Deciding wrongly costs re-adding a feature; not deciding costs an unplannable phase. `follow_links` is therefore removed from the spec, with the condition for re-adding it recorded: run reports showing that discovery consistently fails to surface links on index pages.

## Consequences for the phases

The phase split changes, because promotion was placed in P2 while depending on P3 machinery. Verification writes the bundle and the gate reports through the digest, so promotion cannot be built or tested end to end before the bundle and the notifier exist. `fetcher` inheritance was likewise assigned to P1, where there is nothing yet to inherit from.

| Phase | Contents |
| --- | --- |
| P1 | roles, role-aware probe, link-preserving cleaning for discover, snapshot keying |
| P2 | extraction including discover stubs and `candidate_url`, scoring with the null policy and four tiers, the JSONL store |
| P3 | notifier, private bundle, workflow, budget alarm covering fetch credits |
| P4 | verification queue, gate, promotion, eviction |
| P5 | presentation and release |

P2's earlier four-hour estimate is not credible now that discover-mode extraction and its eval cases sit inside it.

The watch list begins with three verified entries (two DAAD detail pages, one CSC embassy notice, the last of which is a local source). It grows two ways: automatically as discovery promotes, and by hand, since the registry's own audit notes already point at unverified leaf candidates such as Knight-Hennessy's application page, NTU's per-scholarship pages, and the DAAD `?detail=` pattern. Hand-curation during the rebuild is expected to reach roughly ten entries before any promotion code exists, which is what makes the system useful from the first run rather than waiting on promotion to bootstrap it.
