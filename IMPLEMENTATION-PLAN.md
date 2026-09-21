# Docktape lead qualification prototype

## Purpose and scope

Build one local Python application that accepts a lead submission, researches the company, screens the exercise engagement policy, calculates fit, updates an Excel tracker, and sends a notification to a Slack test channel.

Process one lead at a time through a fixed sequence:

```text
JSON submission
  -> validate and identify the lead
  -> collect a bounded set of public evidence
  -> OpenAI extracts a company profile and sales summary
  -> Jev evaluates the evidence and engagement policy
  -> optionally perform one research follow-up and reassess
  -> Python calculates fit and assigns the final status
  -> save the result and update Excel
  -> optionally send the saved notification to Slack
```

OpenAI produces structured text. Jev makes constrained judgments. Python owns arithmetic, policy enforcement, explanation formatting, persistence, and delivery.

A command-line entry point, a fixed sequence of functions, and local run records are sufficient for the prototype.

## 1. Command-line interface and configuration

Run a live assessment with:

```bash
uv run python main.py data/demo/bitrise.json \
  --output-dir output
```

Add `--send-slack` to deliver the saved notification. Without that flag, create the result, workbook row, and notification text, and record delivery as `not_requested`.

Retry delivery without repeating research or model calls:

```bash
uv run python main.py \
  --retry-notification LEAD_ID \
  --output-dir output
```

The submission argument and `--retry-notification` are mutually exclusive.

Configure the integrations through environment variables. Use `python-dotenv` to load a local `.env` file for development, preserving values already set in the environment. An optional `--env-file` argument selects a different file.

```text
SEARXNG_BASE_URL=
FIRECRAWL_BASE_URL=
FIRECRAWL_API_KEY=
TYPESAFE_API_KEY=
OPENAI_API_KEY=
OPENAI_MODEL=
SLACK_WEBHOOK_URL=
```

Validate the settings needed for the selected operation before it starts and report missing values clearly. Keep credentials out of saved artifacts and redact them from error messages.

## 2. Accept and identify a lead

Accept these fields:

| Field | Requirement |
| --- | --- |
| `name` | Required nonempty text |
| `email` | Required, with basic syntax validation |
| `company_name` | Required nonempty text |
| `website` | Required public HTTP or HTTPS URL |
| `employee_count_band` | Required: `1-10`, `11-50`, `51-200`, `201+`, or `unknown` |

Employee count is required because it directly affects qualification. The form includes an explicit `unknown` option so callers do not invent a value when the information is unavailable. Preserve the selected band as self-reported evidence. It may establish the size component when public evidence is absent. If credible public evidence conflicts with it, preserve the conflict and route to review.

Validate input before network calls. Reject URLs containing credentials and URLs targeting local or private addresses. Apply public-address checks to researched URLs and redirects as well. Restrict scraper requests to public website content.

Derive a stable lead ID from normalized email, company name, and website hostname. Trim text, normalize case and whitespace, remove a leading `www.` from the hostname, and ignore URL paths, query strings, fragments, and scheme for identity. Preserve the original submitted values separately.

Identical normalized submissions update one tracker row. A changed email, company name, or hostname creates a different lead. Each row represents a contact's submission.

## 3. Collect public evidence within fixed limits

Use `httpx` for service requests. Configure connection, read, write, and pool timeouts. Retry timeouts, connection failures, HTTP 429, and HTTP 5xx once, with a bounded delay. Honor `Retry-After` within that bound. Slack has separate delivery rules in section 9.

### Initial research

1. Read the submitted homepage with Firecrawl.
2. Run these three SearXNG queries, retaining at most five results each:

   ```text
   {company} {domain} official company
   {company} {domain} employee count legal entity jurisdiction headquarters
   {company} {domain} cloud infrastructure engineering
   ```

3. Read up to three additional relevant pages with Firecrawl.

Initial research permits at most four distinct page requests, including the homepage. Prefer About, Careers, Engineering, Product, Infrastructure, Legal, or Terms pages on the company website. Permit external sources when they help establish company identity, employee count, typed jurisdictions, or cloud demand. Failed page requests consume a slot; the single transport retry does not create another slot.

Use Firecrawl's scrape endpoint with Markdown and links output. Retain at most 12,000 characters of text per page and 1,000 characters per search snippet. Record truncation. Use a bounded HTTP response size as well, so the limit is enforced before loading an arbitrarily large response into memory.

Use a SearXNG instance with JSON output enabled and query `/search` with `q` and `format=json`. [SearXNG search API](https://docs.searxng.org/dev/search_api.html), [Firecrawl scrape API](https://docs.firecrawl.dev/api-reference/endpoint/scrape).

### Certificate-transparency context

Resolve the submitted hostname to its registrable apex with `tldextract`. Make one request:

```text
GET https://crt.name/v1/search?apex={apex}
Accept: text/plain
```

Stream at most 250,000 bytes and retain at most 100 distinct in-scope hostnames. Record counts for malformed, wildcard, duplicate, out-of-scope, and omitted entries. Omission counts apply to the received portion; mark response truncation separately.

Use the returned names as passive discovery context for company research. Claims about ownership, current use, headquarters, cloud providers, workloads, or spending require supporting page evidence. Fit points come from the verified company profile. An unavailable certificate-transparency service creates a warning; assessment continues with the other sources.

### Evidence records

Give source records stable IDs and save:

```json
{
  "id": "evidence_...",
  "source_type": "page",
  "source_url": "https://example.com/engineering",
  "excerpt": "Exact text retained from the source.",
  "retrieved_at": "2026-09-21T12:00:00Z",
  "company_association": "submitted_domain",
  "truncated": false,
  "synthetic": false
}
```

Source types distinguish pages, search snippets, and submitted fields. Keep certificate names in a separate context field. Record search query, result title, URL, snippet, and position. Association with a company is a claim to validate, particularly for external sources.

Search snippets can guide research and support explicitly inferred statements. Sales-readiness decisions about company identity, jurisdiction facts, and direct cloud usage require fetched page evidence. Self-reported employee count remains permitted as described above.

Treat submitted and retrieved text as evidence to evaluate. Application instructions remain separate from that content.

## 4. OpenAI extracts a structured company profile

Call the OpenAI Responses API with the configured `OPENAI_MODEL`, `store=false`, and strict JSON Schema output through `text.format`. Set the SDK retry limit to one. Record the requested and returned model names.

The profile contains:

- Company identity and observed aliases.
- A short business description.
- Typed jurisdiction facts containing entity name, ISO country code, and one role: registered jurisdiction, contracting entity, ultimate parent, operational headquarters, or office.
- Employee-count band.
- Cloud signals and a proposed cloud-demand category.
- Evidence IDs for each material claim.
- Missing facts and conflicts.
- A short sales summary represented as sentences, each with supporting evidence IDs.

Each material claim includes its typed value, support type, and evidence IDs. Support types are `self_reported`, `direct`, `inferred`, and `unknown`. Do not treat an office as a legal domicile or headquarters. Multiple locations conflict only when they describe the same entity and role. Describe cloud demand through supported workload signals.

Python checks the response schema, allowed values, and existence of every cited evidence ID. Jev then checks whether the cited evidence supports each claim. Valid JSON and valid references do not prove factual support.

Handle refusals, incomplete responses, and invalid output as assessment failures. Python assigns final fit points and sales status after validation. [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).

## 5. Jev makes typed judgments

Call `POST https://api.typesafe.ai/v1/systemone` through `httpx`, using `model: "jev-latest"`. Save the response's resolved model version, raw answers, probabilities, and confidence values.

Jev receives the normalized submission, raw evidence, certificate context, profile, policy, and deterministic name-similarity signals. Clearly label the OpenAI profile as proposed claims, not independent evidence.

Jev returns typed judgments, not generated explanations. Define finite questions and let Python translate the answers into result fields. Use Choice questions for the following judgments:

| Judgment | Answer space |
| --- | --- |
| Support for each material profile claim | `supported`, `contradicted`, `insufficient` |
| Employee-count category | The four size bands, `unknown`, `conflicting` |
| Strongest cloud-demand category | `minimal`, `digital_product`, `production_cloud`, `substantial_cloud`, `unknown`, `conflicting` |
| Basis for each competitor decision | `likely_alias`, `plausible_partial`, `distinct_entity`, `no_indication`, `insufficient_identity` |
| Useful compliance follow-up | `none`, `jurisdiction`, or one of the three competitor identifiers |

Use one ordered Score question for unlisted competitive overlap. Its four descriptive levels are `0` no material overlap, `1` adjacent only, `2` material overlap through at least one limited or ancillary core capability, and `3` direct competitor with FinOps or cloud-cost optimization as a core offering. Read the probability-weighted score and full level distribution together. Group levels `0–1` and `2–3` by their shared policy action, requiring at least `0.80` probability on one side before clearing or flagging. Confidence measures concentration on an individual level and is retained for diagnostics; uncertainty between two levels on the same policy side does not require review.

For each competitor, also select the strongest supporting evidence ID from supplied candidates, `submitted_name`, or `none`. These IDs are predefined options. Python copies the corresponding source text and formats the explanation.

Submit independent questions together. They all inspect the supplied state and cannot rely on another answer from the same request. Python validates that outcome and evidence selections are consistent. An inconsistent explanation basis requires review.

Use `confidence >= 0.80` as the prototype acceptance threshold for material Choice judgments. Lower confidence makes that component uncertain. This is an initial operating rule to evaluate on demo cases, not a measured guarantee of accuracy. A missing answer, invalid option, or malformed response cannot qualify a lead.

Validate each summary sentence against its cited evidence as well as checking the structured decision fields. Render the final summary by joining accepted sentences; replace unsupported claims with explicit uncertainty. Unsupported summary content cannot be sent as an established fact.

[TypeSafe System One](https://docs.typesafe.ai/concepts/system-one), [HTTP API](https://docs.typesafe.ai/api), [citation verification](https://docs.typesafe.ai/cookbooks/citation_check).

## 6. Apply the exercise engagement policy

Store policy in `data/policy.json`, with a version and effective date of `2026-09-21`.

The fictional competitors are:

- CloudTrim Inc
- SpendWise Cloud
- RightSize Cloud Co

Normalize names and compute RapidFuzz similarity signals against all three entries. Pass the original names, normalized names, scores, aliases, domain, business description, and evidence to Jev. A similarity score never decides identity by itself.

Use these semantic expectations:

| Submitted name | Expected treatment |
| --- | --- |
| Cloud Trim Incorporated | Likely alias of CloudTrim Inc |
| SpendWise | Plausible partial match requiring context |
| RightSize Cloud Company | Likely alias of RightSize Cloud Co |
| Right Size Furniture | Distinct when evidence establishes an unrelated furniture business |

Python maps accepted judgments to compliance outcomes:

- `likely_alias` becomes `flagged`.
- `plausible_partial` or `insufficient_identity` becomes `review_required`.
- `distinct_entity` or `no_indication` clears that competitor check.

Insufficient confidence becomes `review_required` for a positive or ambiguous competitor relationship, including a low-confidence likely-alias selection. `no_indication` clears the check because low confidence in an absence is not evidence of a possible match.

The named list is not exhaustive. The policy also defines a generic competitive scope for cloud cost optimization and FinOps through concrete competing and adjacent capabilities. OpenAI extracts an evidence-backed `competitive_overlap` claim from the research, and Jev independently scores the degree of supported capability overlap from the same saved evidence. Python flags an unlisted company only when OpenAI says `competing_service`, the claim cites saved evidence, the Jev overlap score is at least `2.0`, and the combined probability of levels `2–3` meets `0.80`. An unknown OpenAI result, missing evidence, reviewer disagreement, or a probability distribution crossing the policy boundary becomes `review_required`. Agreement below the score threshold clears when the combined probability of levels `0–1` meets `0.80`. Raw Jev confidence is retained but does not replace the score or grouped probability. The general Jev claim-support question remains useful for constructing the accepted profile, but it is not counted as a third competitor reviewer. The company name is not added to policy merely to obtain a desired result.

The exercise country set is `BY`, `IR`, `KP`, and `RU`. Python performs the lookup after Jev validates each structured jurisdiction fact. A verified registered jurisdiction, contracting entity, ultimate parent, or operational headquarters in the set is `flagged`. An office is context only; a listed-country office requires review but does not itself prove legal domicile. Different countries conflict only when they are asserted for the same entity and role. If no relevant jurisdiction is verified, review is required.

This set is a dated business rule for the exercise. It is not a complete sanctions database or a statement that every engagement involving those countries is legally prohibited. [OFAC country-program guidance](https://ofac.treasury.gov/sanctions-programs-and-country-information/where-is-ofacs-country-list-what-countries-do-i-need-to-worry-about-in-terms-of-us-sanctions).

Combine checks in this order: any accepted flag wins; otherwise any unresolved check requires review; otherwise compliance is clear. A known flag remains visible even when another check fails. `Clear` means clear under the exercise checks only.

Python generates a short rationale from the selected policy entry, reason, and evidence. For example:

> Possible match to SpendWise Cloud. The submitted name shares the distinctive “SpendWise” name, but the collected sources do not establish whether it is the listed company. Review required.

## 7. Permit one compliance follow-up

If compliance is ambiguous and there is no accepted flag, Jev may select one follow-up target. Python builds a fixed query for that target, using the submitted company and domain.

Run at most one additional SearXNG query, retain five results, and fetch at most one relevant new page. Total research is therefore bounded at four search queries and five distinct page requests, plus the certificate-context request.

Append evidence, rerun OpenAI once, and rerun Jev once against the expanded evidence set. Save both assessment attempts. The second pass cannot request further research. Final routing uses the final assessment; earlier conflicts remain in the audit record.

If the follow-up fails or does not resolve the concern, retain the prior evidence and route to review. Keep the final summary tied to the evidence used in the final assessment.

## 8. Calculate fit and assign final status

Python calculates points from Jev's accepted categories.

| Employee count | Points |
| --- | ---: |
| 1–10 | 5 |
| 11–50 | 15 |
| 51–200 | 30 |
| 201+ | 40 |

| Strongest supported cloud category | Points |
| --- | ---: |
| Confirmed minimal infrastructure needs | 0 |
| Digital product, without direct production-cloud evidence | 20 |
| Explicit production cloud infrastructure | 40 |
| Explicit substantial cloud-hosted workloads, such as large-scale processing or GPU services | 60 |

Use one cloud category. Repeated mentions do not add points. GPU use or data processing without evidence of cloud hosting does not establish the 60-point category. Missing evidence is unknown, not minimal infrastructure.

Store each component as points or null. Sum known components into a provisional score when one is unknown. If both are unknown, the score is null. Include a separate `score_status` of `complete` or `provisional`.

Apply final routing in this order:

| Condition | Status |
| --- | --- |
| Any accepted compliance flag | `do_not_engage` |
| Uncertain compliance, unresolved material conflict, unsupported identity, failed assessment, or unknown fit component | `review_required` |
| Compliance clear, complete score ≥60, and cloud points ≥40 | `sales_ready` |
| Other complete, clear assessments | `lower_priority` |

Direct production-cloud evidence is required for sales readiness. This prevents a large company from qualifying solely because it sells a digital product. The score remains a prioritization heuristic, not a cloud-spend estimate.

Source outages alone do not determine status. Missing evidence needed for a decision does. A failed optional lookup can coexist with a complete assessment.

## 9. Persist results, update Excel, and notify Slack

Create one final result object containing the submission, accepted profile, fit components, score status, compliance checks, final status, summary, evidence references, policy version, model versions, warnings, errors, and processing time.

Write `output/lead-tracker.xlsx` with these columns:

```text
Final status, Fit score, Score status, Compliance outcome,
Company, Website, Jurisdictions, Employee band,
Cloud signals, Fit rationale, Compliance audit, Summary,
Contact name, Contact email,
Source links, Lead ID, Processed time, Notification status
```

Use `openpyxl`. Update by lead ID, freeze the header, enable filters, wrap long text, set readable column widths, and color the final status. Store scores as numbers and missing scores as blanks. Write untrusted text as literal cell content, never executable formulas. Keep full source excerpts in run records.

Write files through temporary files and atomic replacement. Save the result and workbook before attempting notification. If the workbook cannot be saved, stop delivery and report the persistence error.

Build `notification.txt` from the saved final result. Include final status, company, score and completeness, the same sales summary as Excel, and one primary compliance reason written for a sales representative. Do not expose model names, confidence values, probability distributions, or validation terminology in that reason. Keep those details, the full check list, and source links in the workbook and run records rather than Slack. Label synthetic cases prominently. Escape untrusted content and prevent unintended Slack mentions.

Slack delivery has four states:

| State | Meaning |
| --- | --- |
| `not_requested` | Notification saved but not sent |
| `sent` | Slack returned HTTP 200 with `ok` |
| `failed` | Delivery was rejected or definitely not attempted successfully |
| `unknown` | The request may have reached Slack, but acceptance was not confirmed |

Save a hash of the message content and delivery attempts in `delivery.json`. Skip content already confirmed as sent. A notification-only retry reads the saved result and message without calling either model.

Retry a definitive connection-establishment failure or HTTP 429 once. Record an ambiguous timeout, write failure, or server failure that could follow acceptance as `unknown`. An explicit notification-only retry may resend the message and reports that duplication is possible. Permanent webhook errors require correction before retrying.

Incoming webhooks return an acknowledgment rather than a message timestamp. Store the acknowledgment and send time; leave the message identifier null. Delivery can duplicate after an ambiguous failure. Persist the delivery result before refreshing the workbook's notification column, so a workbook-update failure does not trigger another send. [Slack incoming webhooks](https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks/).

## 10. Local records and failure handling

Keep one current record per lead:

```text
output/runs/{lead_id}/
  input.json
  evidence.json
  openai-profile.json
  jev-assessment.json
  result.json
  notification.txt
  delivery.json
```

The model record files contain an attempts array when a compliance follow-up runs. Persist partial research and sanitized failure details if a stage fails. An assessment failure produces a review-required result and tracker row when local persistence remains available. Preserve independently accepted compliance flags.

For the same normalized input, ordinary reruns resume the saved assessment and repair missing local outputs. Use `--refresh` to collect new research. Archive the previous assessment under the same lead directory before replacing it; update the existing tracker row. `--send-slack` controls notification delivery for both new assessments and refreshes.

Use distinct exit codes for successful processing, input/configuration errors, incomplete assessment, and output/delivery failure. A business result such as `do_not_engage` is not an execution failure.

## 11. Code layout and dependencies

```text
main.py              CLI, configuration, fixed sequence, saved-run handling
models.py            typed inputs, evidence, profiles, judgments, final result
discovery.py         httpx, Firecrawl, SearXNG, crt.name
assessment.py        OpenAI, Jev questions, policy, score, routing
delivery.py          Excel, notification formatting, Slack delivery records
data/policy.json     dated exercise policy
data/demo/           submissions and clearly labeled synthetic evidence
tests/               unit tests and HTTP contract tests
pyproject.toml       dependencies and supported Python version
uv.lock              reproducible dependency versions
.env.example         configuration names without credentials
README.md            setup, decisions, limitations, demo evidence
```

Use `httpx`, `openai`, `python-dotenv`, `rapidfuzz`, `tldextract`, `openpyxl`, and `pydantic`. Declare `pydantic` directly for typed models, validation, and JSON Schema generation. Call TypeSafe with `httpx`.

## 12. Verification and submission

Unit and HTTP contract tests run without credentials or external requests. Cover:

- Input normalization, stable IDs, and public-URL rejection.
- Evidence budgets, truncation, and bounded retries.
- OpenAI structured-output request shape, refusals, and invalid evidence IDs.
- Jev answer validation, confidence thresholds, and contradictory evidence.
- Policy precedence, multiple offices, structured jurisdiction conflicts, unknown relevant jurisdictions, and failed screening.
- Scores below and at 60, provisional scores, and the direct-cloud requirement.
- The single follow-up limit and consistent final summaries.
- Excel row updates, literal text handling, and persistence before delivery.
- Slack acknowledgment, rejection, uncertain delivery, and notification-only retries.

Separately evaluate the live semantic behavior with clean leads, partial competitor names, likely aliases, and unrelated businesses sharing words. Mocked responses test application logic; live evaluations test model judgments.

Demonstrate three cases through the same application:

| Case | Intended demonstration |
| --- | --- |
| Company with supported size and cloud demand | Live research, complete scoring, clear screening |
| Small company or weak evidence | Lower priority or explicit review requirement |
| Holori, absent from the named list | Independent OpenAI and Jev capability review identifies an unlisted competing service |

Run Bitrise, Qovery, and Holori with live public research for the submission evidence. Synthetic cases use an explicit `--evidence-file` fixture instead of network discovery, but still call OpenAI and Jev and use the same scoring, Excel, and Slack paths. Fixtures must declare their synthetic status; propagate that label to every output. Record actual outcomes rather than forcing an expected category.

Submit the code or a zip, the generated tracker, and screenshots from the real Slack test channel. Capture enough of each message to show company, final status, fit, compliance reason, and summary. A saved message preview or mocked acknowledgment is not delivery proof.

The README explains the AI coding tools actually used, source choices, why self-reported size is accepted, the fit formula and direct-cloud requirement, fuzzy matching and uncertainty handling, the exercise country policy, API setup, retry behavior, and known limitations. Include exact commands to reproduce the runs and distinguish automated tests from live demo evidence.
