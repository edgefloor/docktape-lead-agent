# Docktape lead agent

This uv application researches a submitted company, proposes a cited profile with OpenAI, reviews the evidence with Jev, and makes the qualification decision in Python. LangGraph records the assessment stages and permits one targeted compliance follow-up.

The application saves an Excel tracker and a local audit. Slack delivery is optional.

## Set up

```sh
uv sync --locked --group dev
cp .env.example .env
```

Set `SEARXNG_BASE_URL`, `FIRECRAWL_BASE_URL`, `TYPESAFE_API_KEY`, `OPENAI_API_KEY`, and `OPENAI_MODEL` in `.env`. Set `FIRECRAWL_API_KEY` if your Firecrawl endpoint requires it. Set `SLACK_WEBHOOK_URL` to send notifications.

## Assess a lead

```sh
uv run docktape-lead-agent examples/submissions/bitrise.json --output-dir output
```

The command prints the lead ID, execution ID, business status, execution outcome, score, and notification status. It does not send Slack unless you pass `--send-slack`.

Use `--refresh` to start a new assessment. The default command reuses a completed result for up to 24 hours when the qualification inputs, policy, model configuration, and workflow version match. A changed employee band or policy starts a new execution under the same lead ID.

To use synthetic evidence for a local run, pass `--evidence-file`:

```sh
uv run docktape-lead-agent examples/submissions/cloud-trim.json \
  --evidence-file examples/evidence/cloud-trim-evidence.json \
  --output-dir output
```

The synthetic run still calls OpenAI and Jev. It skips network research and cannot perform a compliance follow-up.

## Inspect and recover a run

```sh
uv run docktape-lead-agent --inspect LEAD_ID --output-dir output
uv run docktape-lead-agent --resume LEAD_ID EXECUTION_ID --output-dir output
```

`--inspect` prints the current execution manifest, result, and local events. `--resume` continues an interrupted execution from its SQLite checkpoint. Completed stage artifacts make replay of a saved stage idempotent. An external response lost before its stage artifact is saved may be requested again.

Results live in `output/runs/LEAD_ID/executions/EXECUTION_ID/`. Each execution has evidence snapshots, profile and review artifacts, attempt records, stage records, and `events.jsonl`. `output/runs/LEAD_ID/current.json` selects the current result. The workbook has one row per lead.

To send a saved notification after an uncertain attempt, use an explicit retry:

```sh
uv run docktape-lead-agent --retry-notification LEAD_ID --output-dir output
```

An interrupted Slack send is marked `unknown`. An ordinary run does not resend it. The explicit retry may deliver a duplicate if Slack accepted the earlier request.

## Check the package

```sh
uv run --locked ruff format --check src tests
uv run --locked ruff check src tests
uv run --locked pytest
uv build
```

The [architecture guide](docs/architecture.md) explains the workflow and record contracts. The [refactoring plan](docs/refactoring-plan.md) records the original migration proposal. The dated default policy is packaged at `src/docktape_lead_agent/resources/policy.json`; pass `--policy PATH` to use another policy file.
