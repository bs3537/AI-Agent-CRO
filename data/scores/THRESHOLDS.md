# Composite formula and thresholds

## Composite formula

```
raw_avg   = (financial_impact + narrative_shift + time_criticality) / 3
composite = raw_avg
          × bucket_weight[bucket_id]
          × position_weight(pct_nav)
          × conviction_mult[conviction_tier]
          × catalyst_boost(days_to_catalyst, bucket_id)
          × stage_interaction(stage, bucket_id)
```

Where:

| Factor | Range | Source |
|--------|-------|--------|
| `raw_avg` | 0 … 10 | mean of three axis scores from rubric |
| `bucket_weight` | 0.30 … 1.00 | `multipliers.BUCKET_WEIGHTS` |
| `position_weight` | 0.30 … 1.50 | log-scale on %NAV; 1% ≈ 0.53, 24% ≈ 1.39 |
| `conviction_mult` | 0.70 (tier 1) … 1.50 (tier 5) | `CONVICTION_MULT` |
| `catalyst_boost` | 1.0 / 1.5 / 2.0 | only buckets #1 & #2; ≤14d / ≤3d |
| `stage_interaction` | 1.00 / 1.15 / 1.30 | matched stage×bucket pairs |

Neutral product of multipliers ≈ 1.0. Max ≈ 5.85. Min ≈ 0.06.
A raw_avg of 10 can therefore produce a composite up to ~58.

## Thresholds (Phase 3 placeholders — tune in Phase 7)

| Threshold | Value | What fires |
|-----------|-------|------------|
| `T`  | 15.0 | Real-time alert (scorer + red team in Phase 4/5 pipeline) |
| `T₂` | 8.0  | Red-team pass + included in evening digest only |
| < T₂ | —    | Stored, no alert, no red team (cost) |

## Examples

```
5/5/5 article, tier 3, 5% NAV, no catalyst, neutral stage, bucket 1
  = 5 × 1.00 × 0.90 × 1.00 × 1.00 × 1.00 = 4.50    → below T₂

7/6/5 article, tier 4, 8% NAV, no catalyst, neutral, bucket 1
  ≈ 6 × 1.00 × 1.06 × 1.20 × 1.00 × 1.00 = 7.63    → below T₂ (barely)

8/8/8 article, tier 5, 20% NAV, within 3d catalyst, bucket 2
  = 8 × 1.00 × 1.34 × 1.50 × 2.00 × 1.00 = 32.16   → above T (alert)

8/8/8 article, tier 5, 20% NAV, no catalyst, commercial-stage × bucket 4
  = 8 × 0.95 × 1.34 × 1.50 × 1.00 × 1.30 = 19.86   → above T (alert)
```

## Bumping MULTIPLIERS_VERSION

`MULTIPLIERS_VERSION` in `multipliers.py` is part of the `inputs_hash`
on every score row. When you change a weight or threshold, **bump the
version string**. The scorer pipeline will then re-score every (article,
ticker) pair under the new regime; old scores remain in the DB for audit.
