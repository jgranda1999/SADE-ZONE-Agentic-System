# SADE v5 Contract Regression Checklist

Use this checklist after any changes to:
- `prompts/*.md`
- `models.py`
- `main.py` tool descriptions

Goal: keep orchestrator/sub-agent contracts aligned and prevent schema drift.

## 1) Contract Shape Checks (Static)

- Confirm `models.py` input contracts exist and match prompts:
  - `EnvironmentAgentInput`: `payload`, `uav`, `uav_model`, `weather_forecast`
  - `ReputationAgentInput`: `reputation_records`
  - `ClaimsAgentInput`: `action_id`, `requested_entry_time`, `pilot`, `uav`, `required_actions`, `incident_codes`, `wind_context`, `payload_context`, `attestation_claims`
- Confirm each v5 sub-agent prompt input section matches the corresponding `*AgentInput` model.
- Confirm orchestrator prompt minimal payload blocks for each sub-agent match `models.py`.
- Confirm `main.py` sub-agent tool descriptions match the same minimal payloads.

## 2) No Tool-Era Wording Drift

- In sub-agent prompts (`env`, `rm`, `claims`), ensure wording indicates:
  - input comes from orchestrator JSON only
  - do not call tools/sub-agents
- Ensure there are no stale references like:
  - "from the tool"
  - "tool response"
  - old flat input fields (`pilot_id`, `org_id`, `drone_id`, `entry_time`, `request`)
    unless they are intentionally nested within the new objects.

## 3) Required Output Invariants

- `EnvironmentAgentOutput`
  - has both `recommendation_wind` and `recommendation_payload`
  - includes `why_wind` and `why_payload`
- `ReputationAgentOutput`
  - includes `demo_steady_max_m_s`, `demo_gust_max_m_s`, `demo_payload_max_kg`, `incident_codes`, `n_0100_0101`
- `ClaimsAgentOutput`
  - when `satisfied=false`, `evidence_requirement_spec` is present with **non-empty** `categories` (matches `main.py` `parse_orchestrator_output` guards)
  - when `satisfied=true`, `evidence_requirement_spec` is null (STATE 5.3 approve path; orchestrator STRICT RULES)
  - includes `resolved_incident_prefixes` and `unresolved_incident_prefixes`
- `OrchestratorOutput.visibility`
  - always includes full `environment_agent`, `reputation_agent`, `claims_agent`, `rule_trace`

## 4) Quick Runtime Smoke

Run from project root:

```bash
python main.py good
python main.py medium
python main.py bad
```

Validate:
- Run completes and writes `results/integration/entry_result_*.txt`
- Decision JSON parses and includes `decision` + `visibility`
- No schema/guardrail errors around claims:
  - `ACTION-REQUIRED` cannot finalize with `claims_agent.called=false` (except STATE 0/1 early actions)
  - `ACTION-REQUIRED` from **PART_107** missing (STATE 3b) must follow the same path: `claims_agent.called=true`, evidence spec when unsatisfied
  - if claims returns spec, decision echoes same spec
  - claims `satisfied=true` must not pair with non-empty spec (orchestrator validation rejects)

## 5) Spot-Check Minimal Payload Intent

For each sub-agent call in orchestrator behavior, verify only intended subset is described/passed:
- `environment_agent`: env subset only
- `reputation_agent`: `reputation_records` only
- `claims_agent`: claims subset only

If a prompt/tool description starts reintroducing the full entry request, treat as regression.

## 6) Final Consistency Pass

- Confirm section naming consistency in prompts:
  - `INPUT FORMAT`
  - `INPUT MAPPING`
  - `OUTPUT FORMAT`
  - `IMPORTANT RULES`
- Run a linter/diagnostics pass after edits.

