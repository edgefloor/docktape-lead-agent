# Docktape lead qualification prototype

A local Python pipeline that researches inbound leads, checks compliance and product fit, updates an Excel tracker, and sends results to Slack.

```text
Lead submission
  -> public research
  -> cited company profile
  -> independent review
  -> compliance and fit
  -> Excel tracker
  -> Slack notification
```

See [`IMPLEMENTATION-PLAN.md`](IMPLEMENTATION-PLAN.md) for the detailed design.

## Demo results

| Company | Status | Fit | Compliance |
| --- | --- | ---: | --- |
| Bitrise | `sales_ready` | 90 | Clear |
| Qovery | `review_required` | Unknown | Review required |
| Holori | `do_not_engage` | Unknown | Flagged as an unlisted competitor |

The generated workbook is [`output/lead-tracker.xlsx`](output/lead-tracker.xlsx). [`output/`](output/) records the completed runs + Slack screenshots.

Submit screenshots from the Slack test channel separately as visual proof.

## Setup

Install the dependencies:

```bash
uv sync
```

Create the environment file:

```bash
cp .env.example .env
```

A live assessment requires SearXNG, Firecrawl, OpenAI, and TypeSafe. Set `FIRECRAWL_API_KEY` only when the Firecrawl endpoint requires authentication. Slack delivery requires `SLACK_WEBHOOK_URL` and it is optional.

I chose SearXNG and Firecrawl because both were already available to me as self-hosted services.

## Run a lead

A submission is a JSON object:

```json
{
  "name": "Example Person",
  "email": "person@example.com",
  "company_name": "Example Cloud",
  "website": "https://example.com",
  "employee_count_band": "51-200"
}
```

`employee_count_band` accepts `1-10`, `11-50`, `51-200`, `201+`, or `unknown`.

I added this field because company size directly affects fit, while public size estimates are often missing or stale. A great addition would be to have researched evidence as lower weighted parameter.

Run a live assessment:

```bash
uv run python main.py data/demo/bitrise.json \
  --output-dir output
```

Send the result to Slack:

```bash
uv run python main.py data/demo/bitrise.json \
  --output-dir output \
  --send-slack
```

An ordinary rerun uses the saved assessment. Add `--refresh` to repeat research and model calls.

Run the synthetic competitor case:

```bash
uv run python main.py data/demo/cloud-trim.json \
  --evidence-file data/demo/cloud-trim-evidence.json \
  --output-dir output
```

Synthetic evidence skips live research but follows the same assessment, scoring, Excel, and notification paths. Every synthetic output is labeled.

## Research

The pipeline uses three public-data sources:

| Source | Purpose |
| --- | --- |
| SearXNG | Finds relevant pages |
| Firecrawl | Fetches bounded page text that can be saved and cited |
| `crt.name` | Adds passive hostname context without probing company systems |

Initial research runs three fixed searches and fetches at most four pages. A compliance follow-up can add one search and one page.

The pipeline saves the evidence with each run for later inspection.

## Model responsibilities

### OpenAI extracts the profile

OpenAI returns a strict JSON Schema profile containing:

- company identity and business description;
- employee-size evidence;
- cloud signals;
- typed jurisdiction facts;
- competitive capabilities;
- a short sales summary.

Every material claim and summary sentence must cite an evidence ID. Python rejects malformed output, missing citations, unknown evidence IDs, refusals, and incomplete responses.

### Jev verifies the profile

Jev receives OpenAI’s profile as a clearly labeled proposal for claim verification, while the overlap question instructs Jev to score only the raw evidence. It reviews each claim, classifies the fit inputs, checks competitor relationships, and decides whether one focused follow-up could resolve uncertain compliance. I recognize that this might be `greedy` inference.

The intended behaviour thus might prevents one model response from proposing and approving the same fact.

## Compliance

The dated exercise policy is in [`data/policy.json`](data/policy.json).

### Named competitors

RapidFuzz compares the submitted name with configured competitor names and aliases. The similarity score is a signal, not a decision.

Jev combines that signal with the domain, company description, and evidence. It classifies each relationship as a likely alias, plausible partial match, distinct entity, no indication, or insufficient identity evidence.

A supported likely alias is flagged. A plausible or unresolved match requires review.

### Unlisted competitors

OpenAI classifies the company's supported capabilities against the cloud-cost and FinOps scope. Jev independently scores capability overlap from `0` to `3`.

The pipeline flags an unlisted competitor only when:

- OpenAI returns an evidence-backed `competing_service` classification.
- At least `0.80` probability lies on levels `2` and `3`.

Missing evidence, reviewer disagreement, or probability across the decision boundary requires review.

Holori demonstrates this path without appearing in the named competitor list.

### Country screening

The exercise policy screens verified registered, contracting, parent, and operational-headquarters jurisdictions against Belarus, Iran, North Korea, and Russia.

An ordinary office in a listed country requires review but does not create an automatic flag.

## Fit and routing

The fit score has two components:

| Component | Categories | Points |
| --- | --- | ---: |
| Employee size | `1-10`, `11-50`, `51-200`, `201+` | 5, 15, 30, 40 |
| Cloud demand | minimal, digital product, production cloud, substantial cloud | 0, 20, 40, 60 |

The maximum score is 100. An unknown component remains blank and makes the score provisional.

A lead becomes `sales_ready` only when:

- compliance is clear;
- both score components are known;
- the total score is at least 60;
- cloud demand contributes at least 40 points.

A compliance flag produces `do_not_engage`. Uncertain compliance, unresolved evidence, a failed assessment, or provisional fit produces `review_required`. Other complete results become `lower_priority`.

The score ranks leads. It does not estimate cloud spend in dollars.

## Outputs

The pipeline creates a stable lead ID from the normalized email, company name, and website hostname. Processing the same lead again updates its Excel row.

```text
output/
  lead-tracker.xlsx
  runs/{lead_id}/
    input.json
    evidence.json
    openai-profile.json
    jev-assessment.json
    result.json
    notification.txt
    delivery.json
```

The Excel tracker contains the status, fit, compliance audit, summary, contact details, evidence links, and notification status.

Slack receives the same summary with the status, fit, and one plain-language compliance reason. Technical scores and diagnostics remain in the workbook and run records.

## Retry Slack delivery

Retry a saved notification without repeating research or model calls:

```bash
uv run python main.py \
  --retry-notification LEAD_ID \
  --output-dir output
```

The application does not resend a confirmed message with unchanged content. An explicit retry after an uncertain delivery records that a duplicate may exist.

## Reproduce the demo

Run all three cases against the same output directory:

```bash
uv run python main.py data/demo/bitrise.json \
  --output-dir output \
  --refresh \
  --send-slack

uv run python main.py data/demo/qovery.json \
  --output-dir output \
  --refresh \
  --send-slack

uv run python main.py data/demo/holori.json \
  --output-dir output \
  --refresh \
  --send-slack
```

The resulting workbook contains one `sales_ready`, one `review_required`, and one `do_not_engage` result.

## Tests

Left them as-is have not reviewed their quality. The model's I used tend to generate these excessively. I have great workaround for this for go projects, for python I haven't made one yet.

## Failure behavior

The research client applies timeouts, caps response sizes, and retries transient failures once.

A failed search or page fetch becomes a warning when enough evidence remains. A failed model call or assessment produces a saved `review_required` result when local persistence is available.

The application saves the result and workbook before Slack delivery. It saves the delivery record before updating the workbook's notification status.

Credentials are excluded from run records, Excel, Slack messages, and error output.

## Known limitations

- The CLI processes one lead at a time.
- Public evidence may be stale, incomplete, or contradictory.
- The `0.80` thresholds need calibration against a representative evaluation set.

## AI-assisted development

I drafted the first version of the requirements and documentation in ChatGPT using a brainstorming session and Pro reasoning. I used Codex for implementation, testing, review, and live verification.

| Tool or skill | Best use |
| --- | --- |
| ChatGPT | Brainstorming, requirement analysis, and the first documentation draft |
| Codex | Repository-aware implementation, testing, debugging, and review |
| ColGREP | Semantic search across source, tests, configuration, and documentation |
| CodeGraph | Tracing callers, callees, implementations, and execution paths |
| `typesafe-ai` | Designing typed LLM judgments and working with Jev |
| `tdd` | Reproducing defects with regression tests before changing code |
| `codebase-design` | Defining module boundaries and separating decisions from presentation |
| `why` | Evidence-based diagnosis of failures and design decisions |
| `how` | Tracing and explaining runtime behavior |
| `technical-writing` | Writing concise documentation and instructions |
| `unslop` | Removing vague, repetitive, and generated-sounding language |

I made the final decisions about scope, data sources, scoring, compliance rules, and presentation.
