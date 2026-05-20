# Stage classification

Sets `stage` in each sidecar YAML. Drives the Phase 3 `stage_interaction`
multiplier — the same event class has different severity by stage:

- `clinical_stage` × bucket #7 (Capital Structure & Liquidity) → elevated
  (a financing crunch matters more when there's no product revenue)
- `commercial_stage` × bucket #4 (Commercial Performance & Revenue Quality)
  → elevated (channel/GTN drift is the leading-edge signal)

## Definitions

- **clinical_stage** — No approved product driving material revenue. Value
  derives from the pipeline. A failed readout or capital event is structurally
  more severe than for a commercial-stage name.

- **commercial_stage** — One or more approved products contribute the bulk of
  enterprise value. Revenue-quality signals (gross-to-net, channel inventory,
  formulary, prior-auth) dominate the risk picture; clinical readouts on
  next-gen assets matter less unless they're indication-defining.

- **hybrid** — Has commercial revenue *and* a clinical-stage program whose
  outcome is material to the thesis. Common in mid-cap biotech. Both
  stage-interaction multipliers can apply.

## Re-classification

A name can move between stages (clinical → commercial on first approval;
commercial → hybrid when a pipeline asset becomes material). Update the
sidecar the day the catalyst resolves, not on a calendar cadence.
