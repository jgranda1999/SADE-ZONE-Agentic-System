### SAFEcert evidence requirement example (`ACTION_REQUIRED`)

This document describes a concrete **Input → SADE agent pipeline → `evidence_requirement_spec`** example aligned with the `action_required` integration fixtures. The goal is to show what SAFEcert consumes when SADE returns `ACTION_REQUIRED`: the structured **`evidence_requirement_spec`** (plus metadata when delivered inside the evaluation API payload).

**Authoritative files**

| Role 
|------
| Full entry request (merged input: subjects, weather, history, claims, reputation) 
| Claims only (same content as embedded in entry request) 
| Reputation only 
| Prior decisions on this series 
| Saved orchestrator run (decision + visibility) 
| External evaluation API shape (`request_id` = evaluation UUID) 

---

### 1) Input

#### 1.1 Subjects (UAV, UAV model, pilot, zone)

The scenario uses **DJI Mavic 3**–class limits so the *current* forecast is inside the manufacturer flight computer (MFC) envelope but in an **elevated** band (wind gusts near max, payload near max). That supports environmental analysis (medium risk) while reputation and certification gaps still force `ACTION_REQUIRED`.

Key fields (see full JSON in `action_required_entry_request.json`):

- **`uav_model.max_wind_tolerance`:** `12.2` kt — forecast gusts `11.0` kt are within tolerance but high utilization (~90% of max).
- **`uav_model.max_payload_cap_kg`:** `2.268` — requested **`payload`** `"2.0"` kg leaves a small margin (`~0.27` kg), so payload is “near limit” without being a denial by environment alone.
- **`zone.sade_zone_id`:** `zone-001` — matches `weather_forecast.sade_zone_id`.

#### 1.2 Reputation records and attestation claims on file

**Reputation:** Five historical sessions for `pilot-001` / `drone-001`. Two sessions carry **unresolved** incident codes that drive orchestrator rules:

| Record | Incident codes | Notes |
|--------|----------------|--------|
| `rep-003` | `0101-100` | Medium-severity (0101 family) |
| `rep-005` | `0011-010` | High-severity (0011 prefix) |

**Attestation claims (on file before this evaluation):**

1. **`ENVIRONMENT` / `MAX_WIND_GUST(24.3kt)`** — `SATISFIED` with meta showing observed gust below the certified ceiling. This is the **environmental capability** artifact used later by the claims agent to mark **`MITIGATE_WIND_RISK`** as satisfied (wind mitigation proof present vs. current gusts).
2. **`CAPABILITY` / `FOLLOWUP_REPORT(incident_code=0011-010)`** — `SATISFIED`. Per the claims-agent contract, follow-up alone does **not** remove the need for verified **`INCIDENT_MITIGATION`** when mitigation is still unproven; both incidents still produce mitigation rows in the evidence spec.
3. **No `CERTIFICATION` / `PART_107`** claim valid at `requested_entry_time` — so **`PART_107_VERIFICATION`** stays unsatisfied.

There is **no** on-file claim for **`0101-100`** follow-up or mitigation.

#### 1.3 Environmental conditions (forecast for the requested window)

From `weather_forecast` on the entry request:

- `max_wind_knots`: `9.0`, `max_gust_knots`: `11.0` — within `uav_model.max_wind_tolerance` (`12.2`).
- Temperature and visibility are benign for this example; they are not pulled into the evidence requirement spec because the unresolved gaps are incident + Part 107, not a missing weather attestation.

#### 1.4 Entry request history

`entry_request_history.json` includes a **prior** `ACTION_REQUIRED` whose `evidence_requirement_spec` asked only for **`PART_107`**. That sets narrative context (operator returning with more data) without changing the current run’s logic.

---

### 2) Agent pipeline → `ACTION_REQUIRED`

For this fixture, the orchestrator ends in **`ACTION-REQUIRED`** with required actions along the lines of:

- `RESOLVE_HIGH_SEVERITY_INCIDENTS`
- `RESOLVE_0100_0101_INCIDENTS`
- `PART_107_VERIFICATION`

The **claims agent** is invoked (STATE 5). It verifies actions against `attestation_claims` and context. Outcome (see `entry_result_action_required.txt` → `visibility.claims_agent`):

- **`MITIGATE_WIND_RISK`:** satisfied (e.g. `MAX_WIND_GUST` proof covers current gust context).
- **Incident resolution actions:** unsatisfied until **`INCIDENT_MITIGATION`** is evidenced per code.
- **`PART_107_VERIFICATION`:** unsatisfied (no qualifying Part 107 claim).

So the **`evidence_requirement_spec`** lists **only remaining gaps**: two **`INCIDENT_MITIGATION`** rows and **`PART_107`**. It does **not** repeat environmental rows, because environmental mitigation is already satisfied on file.

---

### 3) Output SAFEcert cares about (`evidence_requirement_spec`)

Below is the **`evidence_requirement_spec`** object from `results/integration/entry_result_SADE_API_action_required.json` (what consumers read after API normalization). **`request_id`** is the evaluation UUID **`b80eac98-e26b-4988-9179-c4e84fc4530f`**.

```json
{
  "type": "EVIDENCE_REQUIREMENT",
  "spec_version": "1.0",
  "request_id": "b80eac98-e26b-4988-9179-c4e84fc4530f",
  "subject": {
    "sade_zone_id": "UNKNOWN",
    "pilot_id": "pilot-001",
    "organization_id": "org-001",
    "drone_id": "drone-001"
  },
  "categories": [
    {
      "category": "CAPABILITY",
      "requirements": [
        {
          "requirement_id": "req-mitigation-0011-010",
          "expr": "INCIDENT_MITIGATION(incident_code=0011-010)",
          "keyword": "INCIDENT_MITIGATION",
          "applicable_scopes": ["PILOT", "UAV"],
          "params": [
            {
              "prefix": "0011",
              "incident_code": "0011-010",
              "incident_codes": null,
              "key": null,
              "value": null
            }
          ]
        },
        {
          "requirement_id": "req-mitigation-0101-100",
          "expr": "INCIDENT_MITIGATION(incident_code=0101-100)",
          "keyword": "INCIDENT_MITIGATION",
          "applicable_scopes": ["PILOT", "UAV"],
          "params": [
            {
              "prefix": "0101",
              "incident_code": "0101-100",
              "incident_codes": null,
              "key": null,
              "value": null
            }
          ]
        }
      ]
    },
    {
      "category": "CERTIFICATION",
      "requirements": [
        {
          "requirement_id": "req-part-107",
          "expr": "PART_107",
          "keyword": "PART_107",
          "applicable_scopes": ["PILOT"],
          "params": []
        }
      ]
    }
  ]
}
```

**Note on `subject.sade_zone_id`:** The claims-agent contract may emit `"UNKNOWN"` when zone is not supplied on the claims input. The operational zone for this request is still **`zone-001`** on the entry request; SAFEcert can join on evaluation id + pilot + drone if needed.

---
