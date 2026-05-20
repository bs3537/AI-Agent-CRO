# Severity scoring rubric

Mirror of `src/sma_monitor/scorer/rubric.py` — keep them in sync when tuning.
The prompt feeds the rubric verbatim to Claude Sonnet. Tune anchors in
Phase 7 based on per-bucket alert precision.

## The three axes

Each axis is a **decimal in [0, 10]**. Mid-range scores are valid (e.g. 6.5).

### `financial_impact` — $ implication for this holding
| Score | Anchor |
|------:|--------|
| 0 | No financial implication |
| 3 | Minor — small revenue/cost line item, contained |
| 5 | Moderate — meaningful but contained to one product/one quarter |
| 8 | Major — material to next 1–2 quarters of earnings or guidance |
| 10 | Existential — solvency, going-concern, or thesis-defining |

### `narrative_shift` — change to the long-thesis
| Score | Anchor |
|------:|--------|
| 0 | No update — already priced in or off-thesis |
| 3 | Watch item — worth tracking, no change yet |
| 5 | Update to one component of the thesis |
| 8 | Modification to the central thesis (pivot/derisking/new risk) |
| 10 | Thesis break — the long view no longer holds |

### `time_criticality` — how soon attention is needed
| Score | Anchor |
|------:|--------|
| 0 | No action ever needed |
| 3 | This quarter |
| 5 | This month |
| 8 | This week |
| 10 | Immediate — today or tomorrow |

## Bucket-awareness

The same axis score has different meaning by factor bucket:
- **#12 (microstructure)** rarely scores >5 on narrative_shift — flow ≠ thesis.
- **#1 (clinical)** near a catalyst (≤14d) often scores ≥7 on time_criticality.
- **#4 (commercial)** scores narrative_shift higher for commercial-stage names.
- **#7 (capital)** scores financial_impact higher for clinical-stage names.

## Output format

Claude emits JSON only — no preamble, no markdown fences:
```json
{
  "financial_impact": 7.5,
  "narrative_shift": 6.0,
  "time_criticality": 8.0,
  "rationale": "<one neutral observational sentence>",
  "confidence": 0.85
}
```

`rationale` must be observational, not directional. No "buy"/"sell"/"trim"
verbs — describe what shifts, not what to do about it.
