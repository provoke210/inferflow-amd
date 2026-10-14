# Deterministic return-policy precheck

Version: demo-return-v1. This implements a demo shop policy, not a general legal rule or an automatic order action.

| Confirmed facts | Result |
|---|---|
| Delivered 0–7 days ago inclusive, unused, packaging intact | Ready for human review, not approved |
| Delivered on day 8 or day 10, other facts pass | Conditions not met |
| Used item or damaged packaging, all facts known | Conditions not met |
| Any fact unknown | More information needed; ask only missing questions |
| A known failing fact and an unknown fact | More information needed, with the known failure visible |

Intact packaging does not mean unopened packaging. Days are a non-negative integer confirmed by a person. The feature does not infer dates or time zones from natural language. The server rejects negative or fractional days, boolean days, string booleans and extra fields.

`POST /api/returns/check` accepts:

```json
{"days_since_delivery":10,"unused":true,"packaging_intact":true}
```

It returns `status=conditions_not_met`, the failed field, each check, missing fields, questions, `human_review_required=true`, `automatic_action=false` and `source=local_rules`. Policy version and the 7-day window are fixed by the server. There is no approved state or refund endpoint. No LLM output, queue, quota or generation cache participates. Explanations use fixed templates.

The public judge site runs the matching JavaScript function in the browser. The parity test compares complete responses across 45 boundary/unknown combinations. Browser checks covered days 7, 8 and 10, unknown packaging, all facts unknown and invalidation after editing. A stale async response cannot overwrite changed or cleared inputs.

Free-form `/api/chat` and batch summaries remain fallible drafts. The app does not automatically detect every return question or verify user-entered facts. Historical incorrect model responses are preserved. The rule-only implementation step made zero AMD calls; the local ledger remained at 35/40.
