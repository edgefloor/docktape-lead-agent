# Lead qualification architecture

## Assessment path

`Application.run` validates a submission and computes a fingerprint of qualification inputs, the dated policy, model names, and workflow versions. `RunStore.reusable` selects a completed result only while that fingerprint matches and its 24 hour freshness window remains open. A refresh creates another execution ID under the same stable lead ID.

LangGraph defines the finite path in `workflow/graph.py`:

```text
initial research -> profile -> Jev review -> decision
                                      | useful, permitted follow-up
                                      v
                           follow-up research -> profile -> Jev review -> promotion
                                      |
                                      v
                                  finalization
```

The graph has no edge back to follow-up research. Research also checks the three-search, four-page initial budget and the one-search, one-page follow-up budget. Initial page ranking now puts company About and legal or Terms pages ahead of Careers and engineering pages, because the former are more useful for entity and jurisdiction claims. HTTP retries have a separate two-attempt limit.

The initial attempt becomes authoritative after profile extraction and Jev review. A follow-up has its own evidence snapshot, profile, and review. The graph promotes it only when the candidate has both artifacts, no review validation errors, and no follow-up research failure. A failed candidate leaves the first assessment authoritative. Its failure is recorded as a warning and does not make the completed initial assessment incomplete. A supported compliance flag skips follow-up.

## Module ownership

- `domain/` holds submission identity, evidence and profile types, Jev judgment types, accepted claims, policy, compliance, and scoring. Decision functions do not call providers.
- `research/` owns SearXNG, Firecrawl, certificate context, URL checks, request limits, and evidence collection.
- `inference/profile.py` uses LangChain's `ChatOpenAI` with the Responses API and a strict JSON Schema. `inference/jev_review.py` sends typed questions over HTTP and keeps missing or malformed answers as `InvalidAnswer` records.
- `workflow/` declares graph edges and stage operations. Nodes call adapters and save artifacts after each stage.
- `storage/` owns atomic files, immutable execution artifacts, the current-result pointer, and SQLite checkpoints.
- `reporting/` renders workbook rows and notification payloads. `delivery/` owns Slack transport and receipts.

The OpenAI request retains the constrained employee, cloud, overlap, and country fields. Its citation schema lists only IDs in the current evidence snapshot. Python still validates every material claim and summary citation, including whether identity and jurisdiction citations come from fetched pages. One correction request is allowed if citation validation fails; the failed and corrected calls are logged separately. A second failure ends extraction. Jev receives the proposed profile as a proposal. Its overlap question explicitly asks for a judgment based on raw evidence. The request includes a compact evidence index with stable IDs, source metadata, and excerpts so the reviewer can inspect the cited claims. That index is kept once per request rather than repeated in each question.

If an optional cloud signal is labeled direct but lacks page evidence, profile extraction removes that signal before Jev review and records the removed claim in the profile artifact. Identity, jurisdiction, and production-cloud claims still need page evidence; an invalid required claim stops the assessment. This keeps an unsupported optional detail from discarding an otherwise reviewable profile.

## Records and outcomes

An execution manifest contains the input fingerprint, policy date and hash, prompt and schema versions, requested model names, stage outcomes and timings, and authoritative attempt ID. Each attempt record links its evidence snapshot, profile, review, validation errors, and promotion state. The event log records branches, provider calls, retries, timings, and delivery events without repeating full evidence. `--inspect` reads these records.

`final_status` is the business outcome. `execution_outcome` says whether assessment work completed. `notification_status` records delivery separately. A rejected lead can be a completed execution. A failed Slack send does not change the business decision.

Before Slack transport, `Application.publish` saves an intent with status `unknown`. If the process stops before a terminal receipt, a later run preserves `unknown` and does not send. The explicit notification retry may create a duplicate, so operators must decide whether to use it. The receipt hash covers the versioned text and blocks payload.

## Tracing and privacy

Local manifests, stage artifacts, and `events.jsonl` are always saved. Set `DOCKTAPE_LANGSMITH_TRACING=1` with `LANGSMITH_API_KEY` to send operational spans to LangSmith. Hosted spans contain execution IDs, stage names, outcomes, retry counts, and timings. They do not include submissions, contact details, provider credentials, raw model inputs, or evidence. LangChain's automatic model input tracing is disabled for profile extraction because the request contains full local evidence. Hosted trace failures do not block an assessment.

## Limits

The packaged default policy is an exercise policy, not a production sanctions system. Model aliases can change behind a stable name; the 24 hour reuse window and `--refresh` are the freshness controls. Checkpoints cannot guarantee exactly one provider call if a response arrives just before a crash and its artifact has not been saved. Historical records without the new fingerprint remain readable as files but are not eligible for automatic reuse.
