# Conviction tier definitions

Sets `conviction_tier` in each sidecar YAML. Drives the Phase 3
`conviction_multiplier` on the composite severity score — a CRL on a tier-5
position should weigh more than the same CRL on a tier-1 watchlist holding.

These are **placeholder** definitions. Replace with the actual rubric you use.
The multiplier column maps onto the Phase 3 scorer; tune in Phase 7 once you
have two weeks of alert precision data.

| Tier | Multiplier | Definition |
|------|------------|------------|
| 5 | 1.50× | Core conviction. Thesis well-validated, position sized at or near max (15–24% NAV). Loss of thesis is a portfolio-defining event. |
| 4 | 1.20× | High conviction. Thesis intact, sized meaningfully (5–15%). Would add on weakness barring thesis break. |
| 3 | 1.00× | Base position. Standard sizing (1–5%). Risk/reward acceptable but not asymmetric. |
| 2 | 0.85× | Tactical / value entry. Held for a specific near-term catalyst or mispricing; not a long-term hold. |
| 1 | 0.70× | Watchlist / starter (< 1%). Optionality-only; an early bearish signal here doesn't change much. |

## Maintenance

- Re-validate tiers **quarterly** against current reality (Phase 7).
- A tier downgrade from 5 → 3 because thesis is degrading is itself a signal —
  log the reason in the thesis field when you change it.
