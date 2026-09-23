# Qualification outcome rules

The rules below describe the business result after evidence review. The execution and notification results have separate fields.

| Evidence and review | Business result | Reason |
| --- | --- | --- |
| Supported identity, clear compliance, score at least 60, and direct production cloud evidence | `sales_ready` | The fit threshold and cloud requirement both pass. |
| Supported identity, clear compliance, complete score below 60 | `lower_priority` | The lead is eligible but below the fit threshold. |
| Supported policy competitor or restricted registered entity | `do_not_engage` | An accepted compliance flag takes precedence over uncertain fit. |
| Unknown employee size or unsupported cloud category | `review_required` | The score is provisional. |
| Identity missing, contradicted, or below the claim confidence threshold | `review_required` | The company cannot be qualified without accepted identity evidence. |
| Conflicting relevant jurisdiction facts for the same entity and role | `review_required` | The country check cannot clear the conflict. |
| OpenAI and Jev disagree about material unlisted competitor overlap | `review_required` | Neither proposal alone establishes the flag or clearance. |
| One permitted follow-up fails or its reassessment is invalid | `review_required` unless the initial assessment has an accepted flag | The initial attempt stays authoritative. |
| Slack acceptance is uncertain after an interrupted send | Business result unchanged | Delivery status is `unknown`; an operator must request a retry. |

The default score table and policy entries are in [the packaged policy and domain code](../src/docktape_lead_agent/domain/). The [architecture guide](architecture.md) explains the attempt and delivery records.
