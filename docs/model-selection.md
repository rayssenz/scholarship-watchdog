# Model selection: extraction stage

Research note, 2026-08-25, updated 2026-09-03. Becomes an ADR once the golden-set benchmark runs.

## Why this decision exists

The extraction stage is the only place this project calls an LLM. It turns cleaned page markdown into a fixed Pydantic schema: program, institution, deadline, funding, requirements. Everything downstream (scoring, notification) trusts those values, so a wrong field produces a wrong eligibility verdict with nothing raising an error.

Two things made the choice worth researching rather than defaulting:

- **The private profile never reaches a model.** Extraction sees only public page text; scoring is local Python. Provider jurisdiction is therefore irrelevant, which leaves accuracy and cost as the only criteria.
- **The right metric is not the obvious one.** See below.

## Decision log

Entries 1 to 4 share a date because the pick, the challenge and the reversal happened in one review sitting. Entry 5 is why the format stays: the pattern of benchmark prior, challenge, and revision on evidence repeats, and it repeated within nine days.

| # | Date | Decided | On what evidence | Status |
| --- | --- | --- | --- | --- |
| 1 | 2026-08-25 | Default `gemini-2.5-flash` | Best Value Accuracy per dollar on the SOB leaderboard | Superseded by 3: a primary-source check found it two rungs below the model Google labels "legacy" |
| 2 | 2026-08-25 | GLM-5.1 challenge rejected, on schema enforcement | Z.AI exposes JSON mode, not server-side schema enforcement | Reweighted in 3: structural validity saturates across all models, so enforcement cannot be the decisive argument. The rejection now rests on operational grounds |
| 3 | 2026-08-25 | Default `gemini-3.7-flash` | Independent adversarial review: the accuracy gap between affordable candidates is not decisive at this volume, and a stale model ID is the failure an unattended cron cannot absorb | Current |
| 4 | 2026-08-25 | Gemini IDs, lineup, rate limits and deprecation policy verified against Google's own pages | Primary sources, after the benchmark write-up's GLM price failed the same check | Current |
| 5 | 2026-09-03 | Default `gemini-3.8-flash` | Google released it on 2 September at unchanged introductory pricing; 3.7 became previous-generation eight days after being chosen | Current |

## The metric that matters

Structured-output quality splits into two questions:

| Metric | Question | Useful? |
| --- | --- | --- |
| JSON Pass Rate | Does the output parse and match the schema? | No: every current model scores ~98% |
| **Value Accuracy** | **Are the extracted values actually correct?** | **Yes: this is where models separate** |

A record can parse cleanly, validate against the schema, and still say `"deadline": "2027-03-15"` when the page says April 15. JSON Pass counts that as success. For this project it means applying late.

Across every frontier model the two diverge by 15 to 30 points, so schema-compliance benchmarks no longer distinguish anything. **This project selects on Value Accuracy.**

## Models considered

| Model | Value Accuracy | Input $/1M | Output $/1M |
| --- | --- | --- | --- |
| Gemini 3.1 Pro | 82.0% | $2.00 | $12.00 |
| GLM-5.1 | 80.6% | $1.40 | $4.40 |
| GLM-4.7 | 80.4% | $0.60 | $2.20 |
| Qwen3.5-35B | 80.1% | $0.163 | $1.30 |
| GPT-5.4 | 79.8% | $2.50 | $15.00 |
| Gemini 2.5 Flash | 79.6% | $0.30 | $2.50 |
| Claude Opus 4.7 | 78.7% | $5.00 | $25.00 |
| Claude Sonnet 4.6 | 77.9% | $3.00 | $15.00 |
| Schematron-8B | 73.1% | $0.05 | $0.25 |

Sorted by Value Accuracy. Schematron-8B is included because it is a purpose-built HTML-to-JSON extraction model and the obvious thing to reach for on this task. It passes JSON 98.7% of the time and lands the worst Value Accuracy on the list, which is the clearest single illustration of why this project selects on values rather than structure. The purpose-built category was dropped on that basis.

## Benchmark

Source: **Structured Output Benchmark (SOB)**, [arXiv:2604.25359](https://arxiv.org/abs/2604.25359). 28 models, 5,324 records, human-verified ground truth, scored across seven metrics with schema-complexity weighting.

Three findings bear on this decision:

1. **Model size does not predict extraction accuracy.** Three open-weight models beat GPT-5, Claude Opus 4.7 and Claude Sonnet 4.6 on Value Accuracy.
2. **Generational gains are flat.** GPT-5.5 trails GPT-5.4; Opus 4.7 beats Opus 4.6 by under a point. Paying for a newer flagship buys little here.
3. **Perfect Response collapses to ~50%** even for leaders: at least one wrong field in half of all records. Eval thresholds must be per-field, not per-record.

**Caveat, load-bearing:** SOB does not test Haiku 4.5, Sonnet 5, or Opus 5. Sonnet 4.6 at 77.9% is the nearest measured Anthropic reference; no claim here measures the current Anthropic lineup. The corpus is also Wikipedia passages, OCR documents and meeting transcripts, not admissions pages.

## Cost at this project's volume

~13 sources weekly, ~6K input tokens per changed page, ~900 output tokens, typically 3 changed pages per week.

| Model | Typical week | Est. year |
| --- | --- | --- |
| Gemini 2.5 Flash | $0.012 | ~$1 |
| Claude Haiku 4.5 | $0.031 | ~$2.50 |
| Claude Sonnet 5 | $0.086 | ~$7.30 |

The whole spread is under ten dollars a year. **This was never a cost decision.** It is an accuracy decision with a cost gradient attached. CI eval runs will outspend production regardless of which model ships.

## The decision is inside the noise

GLM-5.1 leads Gemini 2.5 Flash by 1.0pp on Value Accuracy (80.6% vs 79.6%). Whether that lead is statistically real cannot be settled from the published numbers, and this note does not pretend otherwise in either direction.

Treating the two scores as independent proportions at p ≈ 0.80 and n = 5,324, the standard error of the difference is sqrt(2 × 0.8 × 0.2 / 5324) ≈ 0.78pp, which puts the 1.0pp gap at about 1.3 SE: not significant. But that model of the data is wrong. SOB scores every model on the same 5,324 records, so the samples are paired, and the correct test is McNemar's on the discordant pairs. Pairing cancels the item-difficulty variance the models share, which makes the true standard error smaller than 0.78pp, possibly much smaller: on paired data a 1.0pp gap may well be significant. The per-item results that test needs are not published, so the question stays open. (The binomial SE is itself an approximation here, since SOB weights records by schema complexity rather than scoring Value Accuracy as a simple proportion.)

Two things can be said without per-item data, and they are what carry the decision:

- **The corpus is off-domain.** SOB measures extraction over Wikipedia passages, OCR documents and meeting transcripts, not admissions pages. Domain transfer is the dominant uncertainty, and it is larger than the sampling error under either statistical treatment.
- **Effect size matters more than significance at this volume.** At ~150 extractions a year, a true 1pp difference in Value Accuracy is one or two wrong fields annually. Even a certifiably significant 1pp lead would not be worth trading operational properties for.

SOB's own summary points the same way: the top six models sit within one point of each other and rank order changes metric by metric.

So a benchmark gap of this size cannot carry the decision at this volume, and **operational factors are the legitimate tiebreaker**, not a decimal place on a leaderboard. Gemini 3.1 Pro's 2.4pp lead over the Flash tier is different: about 3 SE even under the conservative independence assumption, and pairing only strengthens it, so it survives either treatment. It is still off-domain, which is why it earns a benchmark slot rather than the default.

## Why Gemini 3.8 Flash

Chosen on the operational tiebreakers, since the accuracy race is a tie:

- **A free tier that covers development and the entire CI eval loop.** Evals outspend production in this project, so this is the only cost lever that matters. Paid Standard tier is $0.75/$3.75 per 1M (introductory, through 2026-12-31), a few cents a year at this volume.
- **Current-generation model ID.** The weekly cron runs unattended. A retired model ID fails at 03:00 with nobody watching, and the eval harness cannot detect deprecation; it can only detect accuracy regressions. The default should be chosen against the failure the harness cannot catch. Verified specifics below.
- **Native `responseSchema`** and a single first-party SDK (`google-genai`), so schema conformance is enforced server-side and there is no extra provider or gateway in the dependency chain.

### Verified against Google's model list

Confirmed directly from `ai.google.dev/gemini-api/docs/models` rather than from secondary sources, since the whole point of this section is that a stale model ID is the failure mode:

| Model | ID | Status | Note |
| --- | --- | --- | --- |
| **Gemini 3.8 Flash** | `gemini-3.8-flash` | **New Stable** | released 2 September 2026; "our most intelligent Flash model, engineered for long-horizon software engineering, autonomous agents, and complex enterprise workflows" |
| Gemini 3.7 Flash | `gemini-3.7-flash` | Stable | the previous default here, now the fallback |
| Gemini 3.6 Flash | `gemini-3.6-flash` | Stable | previous generation |
| Gemini 3.5 Flash | `gemini-3.5-flash` | Stable | Google labels this "legacy" |
| Gemini 3 Flash | `gemini-3-flash-preview` | Preview | the weak 77.3% SOB result |
| Gemini 2.5 Flash | `gemini-2.5-flash` | earlier generation | the original pick in this note |

Gemini 3.8 Flash is the newest Flash and carries Stable status rather than Preview, which is the combination the unattended-cron argument calls for. Google positions it for "long-horizon software engineering, autonomous agents", which describes schema-constrained extraction under a weekly cron. Pricing is unchanged from 3.7: $0.75 and $3.75 per million introductory through 31 December 2026, then $1.50 and $7.50.

The check also settled a naming confusion: `gemini-3-flash-preview`, the weak SOB result, is a Preview build at a lower version than 3.5 and above despite what the number suggests.

**This note's model has now gone stale twice while the specification was being written.** Gemini 2.5 Flash was three generations behind when first checked, and 3.7 Flash was superseded eight days after being chosen. That is not an argument against the choices; it is the evidence for the criterion. The reason this project picks a current-generation Stable ID over a better-measured older one is that the cron runs unattended, and a retired model ID fails at 03:00 with nobody watching. A lineup that moves this fast is exactly the environment that argument was written for.

The practical consequence is that the model ID is a config value and swapping it is a one-line change, which is what made this update cheap.

Flash-Lite variants remain available at lower cost (3.5 Flash-Lite $0.30/$2.50, 3.1 Flash-Lite $0.25/$1.50) and were rejected: the price difference is a few cents a year here, and the Lite tier trades away exactly the multi-step reliability this task depends on.

### Operating unattended

The cron runs with nobody watching, so the failure modes that matter are the ones no person is present to catch. Checked against Google's rate-limit and deprecation pages, both retrieved 2026-08-25:

- **Rate limits and 429s.** Free-tier limits apply per project across requests per minute, input tokens per minute, and requests per day; exceeding any one returns `429 RESOURCE_EXHAUSTED`, and daily quotas reset at midnight Pacific. Google publishes the per-model free-tier numbers only in AI Studio, not in the static docs, and states that specified limits are not guaranteed, so the numbers are recorded at cron-enable time and re-checked, never hardcoded. At ~3 extraction calls a week the production cron cannot plausibly hit a per-minute limit; the realistic 429 source is the CI eval loop replaying the golden set in a burst, so eval calls run serialized. A 429 that survives the bounded retries fails the run without advancing the stored snapshots, so the detected change is still pending and the next weekly run picks it up. A failed week self-heals.
- **Deprecation.** `gemini-3.8-flash` was released August 2026 and has no shutdown date announced. Google's stated policy is to announce deprecation in advance, with listed dates as earliest-possible. So retirement is a process risk, not an ambush: a periodic check of the deprecations page covers what the eval harness cannot. Stable Gemini models do get retired, on roughly a 12-to-16-month cycle (`gemini-2.0-flash`: released February 2025, listed shutdown date June 1, 2026; `gemini-3.1-flash-lite`: twelve months). `gemini-2.5-flash` has no announced shutdown date but is already 14 months old, one more reason it is the fallback rather than the default.
- **Latency.** Not a selection factor. A weekly batch of about three pages tolerates any latency the API exhibits.

### The strongest argument against this choice

The only Gemini 3.x Flash that SOB ever measured, Gemini-3-Flash-Preview, scored 77.3% Value Accuracy at rank 22. That is below Gemini 2.5 Flash's 79.6% and below Claude Sonnet 4.6. The 2.3pp gap is about 3 SE under the independence assumption and pairing only strengthens it, so it is real. "Newer Flash is better Flash" is empirically false in this exact model family, and Gemini 3.8 Flash has no extraction benchmark data from anyone.

What reduces the risk without removing it: Google's model list confirms `gemini-3-flash-preview` is a Preview build at a lower version than 3.5, 3.6 and 3.7, so it is an early snapshot rather than a mature release, and SOB flags its always-on thinking as a confound. `gemini-2.5-flash` also stays in the benchmark set as the measured fallback, so the first CI eval run adjudicates on real scholarship pages within hours of the harness existing.

This is a deliberate bet that on-domain measurement arrives fast enough to cover an unmeasured default. If it loses, the fix is a one-line config change.

## Why not GLM-5.1

GLM-5.1 is the strongest challenger and the decision is closer than the headline suggests. It leads the affordable tier on Value Accuracy (80.6% vs 79.6%), it is open weight, and it has the tightest JSON-Pass-to-Value-Accuracy gap of all 28 models at 16.9 points, meaning its values are the best grounded on the list.

It loses the tiebreak on operational grounds:

1. **No free tier for the eval loop.** CI evals are the dominant cost in this project, and Gemini's free tier covers development and repeated golden-set runs outright. That matters more than the production rate.
2. **Weaker structured-output plumbing.** Z.AI's official API exposes `response_format: {"type": "json_object"}`: the schema is described in the prompt and the provider does not enforce it. OpenRouter's default GLM-5.1 endpoint rejects `response_format` outright. Gemini's `responseSchema` enforces conformance server-side. An earlier draft treated this as the decisive argument; it is not (decision log entry 2). SOB's central finding is that structural validity sits near 98% for every model while values discriminate, and constrained decoding fixes structure, not values. With Pydantic validation and one retry already downstream, a JSON-mode model loses little at 14 pages a week. What remains is friction: more validation failures and retries on the one stage where correctness matters.
3. **The published price was stale.** Z.AI's own pricing page lists GLM-5.1 at **$1.40 in / $4.40 out**, not the $1.05/$3.50 quoted in the benchmark write-up. Trivial in absolute terms, but the benchmark write-up's numbers did not survive a check against the primary source, which is why every Gemini claim above cites Google's pages directly.

One accuracy point does not outweigh the free eval loop and the schema plumbing, but it is close enough that GLM-5.1 belongs in the benchmark set rather than being dismissed. If it wins on real scholarship pages by a wider margin than the benchmark suggests, the tradeoff gets revisited.

## What is not decided: the golden set decides

The model is a config value behind an `Extractor` protocol, never a hardcoded call, the same pattern as `Fetcher` and `Notifier`. A published benchmark on someone else's corpus is a prior, not a verdict. Candidates re-run against this project's own golden set of real scholarship pages before the default is locked:

| Model | Host | Why it is in the set |
| --- | --- | --- |
| `gemini-3.8-flash` | Gemini API | the default, on trial and unmeasured |
| `gemini-3.7-flash` | Gemini API | the previous default; first fallback if 3.8 disappoints |
| `gemini-2.5-flash` | Gemini API | measured reference at 79.6% VA |
| `gemini-3.1-pro` | Gemini API | measured VA leader at 82.0%; answers "is Pro worth it" |
| `glm-5.1` | DeepInfra | 80.6% VA, tightest value-grounding gap; confirm the host exposes `json_schema` for this model before the run |

### Golden-set results (pending, P2)

Empty by design. The P2 eval harness populates this table from real scholarship pages, and this note becomes an ADR when it does. Per-field columns match the CI gate in SPEC section 4, where a wrong deadline is disqualifying and a wrong stipend figure is not.

| Model | VA: deadline | VA: funding | VA: nationality | VA: overall | Cost per eval run | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| `gemini-3.8-flash` | pending | pending | pending | pending | pending | pending |
| `gemini-2.5-flash` | pending | pending | pending | pending | pending | pending |
| `gemini-3.1-pro` | pending | pending | pending | pending | pending | pending |
| `glm-5.1` | pending | pending | pending | pending | pending | pending |

One honesty note before the numbers exist: a golden set of a few dozen pages has far less statistical power than SOB, so it cannot resolve 1pp differences either. What it can do that SOB cannot is measure the right domain and the right fields. It adjudicates category failures (a model that misreads admissions tables or European date formats) and per-field floors, not decimal places.

### Exit criteria

The default is re-opened, not just re-benchmarked, if any of these occur:

1. `gemini-3.8-flash` trails `gemini-2.5-flash` on deadline accuracy in the golden-set run. The fallback is promoted; a one-line config change.
2. Any disqualifying-field accuracy lands below the CI gate threshold on real pages.
3. Google announces a shutdown date for `gemini-3.8-flash`, or the free tier stops covering the eval loop.

## Sources

- Structured Output Benchmark: https://interfaze.ai/blog/introducing-structured-output-benchmark ([arXiv:2604.25359](https://arxiv.org/abs/2604.25359))
- LLM API pricing comparison, August 2026: https://www.spheron.network/blog/llm-api-pricing-comparison-gpt-claude-gemini-deepseek-2026/
- Anthropic pricing: https://platform.claude.com/docs/en/about-claude/pricing
- Gemini model list and IDs: https://ai.google.dev/gemini-api/docs/models
- Gemini rate limits (free-tier mechanics; per-model numbers live in AI Studio): https://ai.google.dev/gemini-api/docs/rate-limits
- Gemini deprecation schedule and policy: https://ai.google.dev/gemini-api/docs/deprecations
- Gemini 3.8 Flash announcement and introductory pricing: https://blog.google/innovation-and-ai/models-and-research/gemini-models/introducing-gemini-3-7-flash/
- Z.AI pricing and structured output docs: https://docs.z.ai/guides/overview/pricing · https://docs.z.ai/guides/capabilities/struct-output
