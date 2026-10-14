# Real batch summaries and cold-cache comparison

Date: 2026-10-14. Model: DeepSeek-V4-Flash on AMD Token Factory. All tickets are synthetic, with no real customer data.

## Observed result

| Metric | FIFO / fixed concurrency / no cache | Priority / exact cache |
|---|---:|---:|
| Interactive end-to-end median | 13.946 s | 8.363 s |
| Background end-to-end median | 5.826 s | 11.831 s |
| Interactive p95 (sample maximum) | 16.720 s | 8.407 s |
| Background p95 (sample maximum) | 8.682 s | 14.103 s |
| HTTP successes / requests | 6 / 6 | 6 / 6 |
| Upstream generation attempts | 6 | 5 |
| Exact cache hits | 0 / 6 | 1 / 6 |
| Returned upstream tokens | 1096 | 937 |
| Successful requests / second | 0.346 | 0.424 |
| HTTP 429 | 0 | 0 |

Interactive median time fell by about 40% in this sample, while the background median roughly doubled. This does not show that every request became faster, establish production p95, or demonstrate hardware acceleration. Caching and priority changed together, so their effects cannot be isolated. Sequential execution, provider variation and different output lengths also matter. Each class has just three requests, with no confidence interval or statistical significance conclusion.

## Method

Each arm used the same six requests: three summaries at 0, 0.02 and 0.04 seconds, then three questions at 0.2, 0.4 and 0.6 seconds. One question repeated exactly. Temperature was 0, output caps were 160 for summaries and 96 for questions, and deadlines were 90 seconds. Both used the same model and revised study-v2 prompt, 20 RPM and concurrency cap 2.

Each arm had an independent empty cache. The baseline disabled caching. Both shared the persistent budget/RPM ledger. Before measurement, the runner waited naturally until 61 seconds after the most recent provider call; it did not clear the provider window. The JSON includes complete inputs, arrival times, settings, per-request events and a workload SHA-256.

Batch here means multiple independent summary requests submitted together and dispatched by the gateway. It is not a provider Batch API or GPU continuous batching. Only the real cold pair ran to completion. The planned real warm pair was omitted.

## Content review

HTTP success and finish_reason=stop indicate complete text, not correctness. The raw JSON usable flag has that technical meaning only.

- Delayed parcels: both arms identify a tracking check and avoid inventing an arrival date. An earlier prompt had drifted into unwanted return handling; the revised prompt avoided that in this sample.
- Wrong-color exchanges: both preserve the issue and suggest checking the order and stock, but redundantly ask to confirm already-known intact packaging and omit some known details. Editing is required.
- Return summaries: both retain unknown packaging and shipping fees without inventing a fee.
- Critical counterexample: both arms treated delivery 10 days ago as within a 7-day policy and incorrectly approved eligibility.
- Unknown-packaging questions: answers introduced an unsupported unopened-packaging condition and sometimes implied access to order actions that the app does not have. Caching preserves these defects as well as the text.

See live-study-quality.json for every reviewed response. This is an assistant review against the inputs, not an independent blind evaluation or a production accuracy score. The later deterministic precheck uses structured facts and local template explanations; it does not change this historical evidence or fix free-form model text.

## Preserved failures and accounting

The first run had 6/6 baseline successes and 6/6 optimized HTTP 502 failures. One diagnostic usage query also returned 502. Failure latency was not presented as an improvement. The full record is live-study-attempt1.json. A recovery usage probe later succeeded. Both cold arms were then repeated in full with the same revised prompt, rather than rerunning only a favorable portion.

The work moved the local ledger from 8 to 35: **27 new upstream attempts against a ceiling of 30**, including failures, metadata and probes. There were 23 generation attempts. Successful responses reported 2,854 observable tokens; failed-request tokens remain unknown. The conservative pre-send reservation totaled 22,822 against 24,000, using message UTF-8 bytes plus protocol allowance and the output cap. This is not an official tokenizer or billing guarantee. Limits remained 20 RPM and concurrency cap 2. No key rotation or ledger deletion bypassed them.

The final usage query showed about 0.9994463 of 1 provider points remaining. Although API field names end in _usd, these are described as event points, not asserted to be actual dollar charges. The local rolling 24-hour request cap is 40. Materials and the public evidence site do not generate new AMD calls.

Records: live-study.json, live-study-attempt1.json, live-study-preflight.json, live-study-quality.json and live-study-session.json in results/.

## Reproduction

The runner prints a plan without execution by default. To run another live cold pair, stop the gateway, verify the key's remaining quota and other clients, and explicitly execute:

```sh
python -m inferflow.live_study --execute --cold-only --calls-cap 15 --token-cap 13466 --output results/new-live-study.json
```

This command spends new quota. Do not overwrite the submitted records or reset the ledger. The current runner stops on an error and saves partial results. Historical JSON retains original plan fields; actual scope is identified by cold_only, case_order and calls_cap_this_invocation. The 30-call session ceiling includes preflight and prior attempts, not just the final runner invocation.
