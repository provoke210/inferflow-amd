# Validation record

Date: 2026-10-14. Python 3.9.6; versions pinned in requirements.lock. The current suite includes the original 48 tests plus English asset serving and a JavaScript/Python policy parity test. The latter requires Node and checks all 45 combinations of boundary days and tri-state conditions. Tests isolate real credentials and make no AMD calls.

The local browser flow was checked for rule outcomes, stale-result invalidation, drafts, batch summaries, exact-cache reuse and simulated experiments. The published English demo is separately checked for rule interactions, recorded output selection, data loading and submission downloads.

Original small live validation: 7 upstream calls, including 3 generations, model discovery and usage. Later paired-study work added 27 attempts (including failures and quota probes) from a starting ledger value of 8 to 35. The deterministic policy step and English/public-site preparation add no AMD calls. The local request cap is 40. These are local request counts, not provider points.

The completed live cold comparison contains 12 HTTP successes across two arms, but it failed some content-quality checks. In particular, both arms approved a day-10 return under a 7-day policy. Raw successes are not content-correctness passes. See live-study.md and live-study-quality.json.

The original simulated study used multilingual synthetic prompts; benchmark.json preserves it unchanged in the local archive and is excluded from this English release. benchmark-en.json is a new English-language simulator run with the same design and seed. Translation changes content hashes and synthetic token/latency values; do not substitute its values for the historical run. Both remain clearly simulated.

Dependency checks and Python tests passed in the development environment. Docker was unavailable, so no successful image build is claimed. No production load test, merchant pilot, independent blind quality evaluation or statistical significance claim is made.
