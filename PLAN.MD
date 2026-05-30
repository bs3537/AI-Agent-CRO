# PLAN.MD

# Unified Thesis Rating System And App Improvement Plan

## 2026-05-30 Amendment: LLM-Final Decision Authority

This supersedes the earlier deterministic-final recommendation in this file.
The app is an AI Agent CRO, so Codex/LLM is the final decision-maker whenever it
returns a valid structured grade. Deterministic scores, red-team severity,
fundamental data, source-quality checks, and EMA20 technicals are inputs that
guide the LLM. They are persisted for audit and transparency, but they do not
post-process or override the LLM's final `A | B | C | D` grade.

Operational rule:

```text
if Codex returns valid llm_grade:
    final_grade = llm_grade
else:
    final_grade = deterministic_fallback_grade

action = "sell" if final_grade == "D" else "hold"
display = "SELL D" for D, otherwise "HOLD A/B/C"
```

If the deterministic score and LLM grade disagree, the dashboard should show the
disagreement in the detail view as useful context, not silently force the
deterministic result.

## Executive View

The current app has the right architecture for a thesis-drift monitor, but the
top-line decision model should be tightened.

Today, the backend produces a `hold | watch | sell` verdict, then the API derives
an `A | B | C | D` grade afterward. That creates a subtle product problem: the
verdict and grade are related, but they are not the same decision object. A user
can see "HOLD" and not know whether that means a clean hold or a deteriorating
hold that deserves immediate attention.

My recommendation:

1. Make the letter grade the canonical rating.
2. Derive the displayed action from the grade.
3. Use Codex as the final structured evidence analyst and decision-maker for
   the portfolio-facing grade.
4. Keep deterministic scores auditable, visible, and backtestable as inputs,
   not final overrides.
5. Add a technical trend factor: monitor daily close versus the 20-day EMA and
   show the EMA line on each sparkline. Below-EMA names should lose rating
   quality or attention status, but a technical break alone should not force a
   sell unless fundamental/thesis evidence also confirms deterioration.

Target tile language:

```text
A -> HOLD / clean
B -> HOLD / monitor
C -> HOLD / on watch
D -> SELL / thesis broken
```

In the UI, the top chip should become one synchronized signal:

```text
HOLD A
HOLD B
HOLD C
SELL D
```

This removes the mismatch between "HOLD" and "grade" while preserving the useful
idea that not all holds are equal.

## Product Philosophy

This app should not try to be a broker-style recommendation engine. It should be
a thesis-integrity system.

The question is not:

```text
Should I buy or sell this stock today?
```

The better question is:

```text
Has the original reason I own this position changed enough that I need to act?
```

That makes the grade system more natural than the current standalone
HOLD/WATCH/SELL system.

`A`, `B`, and `C` are all still holds, but they communicate very different levels
of portfolio attention:

- `A`: thesis intact; routine monitoring.
- `B`: thesis mostly intact, but one or more issues are emerging.
- `C`: thesis under real pressure; requires active review.
- `D`: thesis broken or close enough that sell/exit review is the default.

## Current State

Relevant current files:

- `src/sma_monitor/decision/engine.py`
  - Builds the per-holding evidence bundle.
  - Calls Codex when available.
  - Falls back to a deterministic heuristic when offline.
  - Persists `hold | watch | sell`.

- `src/sma_monitor/api/routes/positions.py`
  - Converts the latest decision into API output.
  - Derives the A/B/C/D grade after the decision already exists.

- `frontend/src/components/PositionCard.tsx`
  - Shows the grade badge beside the HOLD/WATCH/SELL chip.

- `frontend/src/components/DecisionChip.tsx`
  - Displays the verdict text.

- `frontend/src/components/GradeBadge.tsx`
  - Displays the grade.

- `src/sma_monitor/news/fmp_client.py`
  - Already stores daily-close price history in `price_series`.
  - This should become the source for EMA20, not a separate vendor feed.

- `frontend/src/components/Sparkline.tsx`
  - Currently draws only the price line.
  - This should be upgraded to draw price plus EMA20.

Current backend behavior:

```text
sell  -> grade D
watch -> grade C
hold + serious bear concern -> grade C
hold + moderate concern or low confidence -> grade B
hold + clean evidence -> grade A
```

This is directionally sensible, but the app should invert the relationship:
compute grade first, then derive display action from grade.

## Target Rating Contract

Introduce a canonical `PositionRating` object.

```python
class PositionRating:
    ticker: str
    action: Literal["hold", "sell"]
    grade: Literal["A", "B", "C", "D"]
    attention_state: Literal["clean", "monitor", "watch", "broken"]
    risk_score: float                 # 0-100
    risk_components: dict             # transparent scoring waterfall
    latest_close: float | None
    ema20: float | None
    price_vs_ema20_pct: float | None
    technical_state: Literal[
        "above_ema20", "below_ema20", "extended_below_ema20", "no_price_data"
    ]
    deterministic_grade: str
    llm_grade: str | None
    final_grade: str
    note: str
    drivers: list[str]
    confidence: float
    model_used: str
    rating_version: str
    thesis_hash: str
    inputs_hash: str
    decided_at: datetime
```

Derived action:

```python
action = "sell" if final_grade == "D" else "hold"
```

Derived attention state:

```python
A -> clean
B -> monitor
C -> watch
D -> broken
```

Backward-compatible mapping if old consumers still expect `verdict`:

```python
A -> hold
B -> hold
C -> watch
D -> sell
```

The important point: the old `watch` concept becomes an attention state under a
hold, not a separate top-line action that competes with the grade.

## Deterministic Rating Engine

Create a new deterministic rating layer in:

```text
src/sma_monitor/decision/rating.py
```

The rating engine should convert the evidence bundle into a transparent
`risk_score` from `0` to `100`, then map that score to a grade.

Initial grade thresholds:

```text
A: 0-19      Thesis intact
B: 20-39     Mild drift / monitor
C: 40-64     Material drift / on watch
D: 65-100    Thesis broken / sell review
```

Suggested risk components:

```text
1. Red-team severity                         up to 35 points
2. Thesis-clause impact                      up to 30 points
3. Top scored article composite              up to 20 points
4. Catalyst / timing pressure                up to 10 points
5. Technical trend: close vs 20-day EMA       up to 15 points
6. Data-quality or uncertainty penalty        up to 10 points
```

The score can exceed 100 before clamping.

### Component 1: Red-Team Severity

Use the strongest red-team `severity_of_concern`.

```text
severity 1 -> 0 points
severity 2 -> 8 points
severity 3 -> 18 points
severity 4 -> 28 points
severity 5 -> 35 points
```

Hard rule:

```text
severity >= 4 cannot produce grade A or B.
severity >= 5 should normally produce grade D unless source quality is poor.
```

### Component 2: Thesis-Clause Impact

The current app stores the thesis as free text. That is usable, but not ideal.
The next version should break each thesis into testable claims.

Example sidecar addition:

```yaml
thesis_checks:
  - id: lead_asset_efficacy
    claim: Lead asset has clinically meaningful efficacy.
    importance: critical
  - id: cash_runway
    claim: Company can fund through next major catalyst.
    importance: high
  - id: commercial_launch
    claim: Launch trajectory supports long-term revenue assumptions.
    importance: medium
```

Codex can help extract these checks from the existing free-text thesis, but the
stored checks should become explicit and editable.

Suggested impact scoring:

```text
No thesis clause hit                         0 points
Low-importance clause pressured              8 points
Medium-importance clause pressured           15 points
High-importance clause pressured             22 points
Critical clause contradicted                 30 points
```

Hard rule:

```text
If a critical thesis clause is directly contradicted by a primary or
well-corroborated source, grade must be D.
```

### Component 3: Top Scored Article Composite

Use the existing composite score from `scorer/multipliers.py`.

Suggested mapping:

```text
below T2 (<8)             0-5 points
T2 to T (8-15)            8-14 points
above T (>=15)            15-20 points
```

The composite already accounts for:

- article severity
- bucket importance
- conviction tier
- position size
- catalyst proximity
- stage interaction

Portfolio-manager adjustment:

Position size should raise attention priority, but it should not by itself make
the thesis "broken." A 20% NAV position deserves faster review than a 1% NAV
position, but the grade should still be driven by thesis evidence. Over time,
split these concepts:

```text
thesis_risk_score      -> A/B/C/D grade
portfolio_priority     -> ordering, alert urgency, email prominence
```

### Component 4: Catalyst / Timing Pressure

Timing matters when evidence lands near a catalyst.

Suggested rules:

```text
overdue unresolved catalyst                  +10
clinical/regulatory catalyst <= 3 days       +8
clinical/regulatory catalyst <= 14 days      +5
no near catalyst                             +0
```

This should raise attention, not create a sell rating by itself.

### Component 5: Technical Trend / 20-Day EMA

Add a daily technical layer from the existing FMP daily-close price series.

The monitored signal:

```text
20-day exponential moving average on daily closes
latest close above/below EMA20
distance from EMA20 as %
consecutive closes below EMA20
EMA20 slope over the last 5 trading days
```

Portfolio-manager view:

Price below the 20-day EMA is not a thesis invalidator by itself. It is a
position-management warning. It says the market is no longer confirming the
position in the short/intermediate daily timeframe. For a long book, that should
raise attention, reduce tolerance for weak fundamentals, and make a marginal
hold less comfortable.

Suggested scoring:

```text
No price data                                      0 points, but show badge
Close above EMA20 and EMA20 rising                 0 points
Close above EMA20 but EMA20 flat/down              +3
Close below EMA20                                  +8
Close below EMA20 for >= 3 consecutive closes      +12
Close > 5% below EMA20 and EMA20 falling           +15
```

Suggested grade guardrails:

```text
Below EMA20 cannot force D by itself.
Below EMA20 should usually cap a clean A at B.
Extended below EMA20 should usually cap at B and can push a borderline B to C.
If the fundamental/thesis grade is already C, persistent below-EMA weakness can
push the final risk score into D, but only when there is also fundamental,
financial, or thesis-clause evidence.
If price recovers above EMA20 for 2-3 consecutive closes, remove the technical
penalty unless the fundamental evidence remains impaired.
```

Implementation detail:

Use the same daily-close series that currently feeds the sparkline. Compute EMA20
server-side so the API, dashboard, email, and rating engine all use the same
technical value.

Add:

```text
src/sma_monitor/decision/technicals.py
```

Core functions:

```python
ema(values: list[float], window: int = 20) -> list[float]
technical_state(closes: list[float]) -> TechnicalSnapshot
technical_risk_points(snapshot: TechnicalSnapshot) -> int
```

### Component 6: Data Quality / Uncertainty

Penalize confidence when evidence quality is weak.

Examples:

```text
uncorroborated FMP-only claim                cannot force D
uncorroborated Semantic Scholar-only claim   cannot force D
low-source-tier article only                 cap grade at C unless repeated
missing thesis sidecar                       no rating
placeholder thesis                           show STUB THESIS and suppress A
stale IBKR position pull                     show stale data flag
missing price history                        show no technical signal
```

This is important because the app is meant to protect capital. A noisy third-
party feed should not trigger a false sell signal.

## Hard Breakers

Some events should bypass normal point scoring and force `D`, assuming the
source is primary or strongly corroborated.

Examples:

```text
Failed pivotal primary endpoint for the lead thesis asset
FDA complete response letter on the lead asset
Clinical hold on the central program
Going-concern warning or runway clearly below required catalyst window
Bankruptcy, delisting, or major liquidity impairment
Accounting restatement or fraud investigation that breaks trust in numbers
Confirmed discontinuation of the thesis-driving program
Major commercial collapse that directly contradicts the launch thesis
```

Implementation:

```python
hard_breaker = detect_hard_breaker(candidate, llm_assessment)
if hard_breaker:
    final_grade = "D"
```

## Role Of Codex LLM

Codex should remain central, but its role should be better defined.

Current role:

```text
Codex can directly return hold/watch/sell.
```

Updated recommended role:

```text
Codex reads the evidence, maps it to thesis clauses, uses deterministic scores
and EMA20 data as inputs, then returns the final grade, note, drivers, and
confidence. The deterministic resolver only supplies fallback behavior when
Codex is unavailable or returns an invalid grade.
```

Codex output schema should become:

```json
{
  "llm_grade": "A | B | C | D",
  "thesis_clause_impacts": [
    {
      "clause_id": "cash_runway",
      "impact": "none | low | medium | high | critical",
      "evidence": "short source-grounded explanation"
    }
  ],
  "hard_breaker": {
    "present": false,
    "type": "",
    "evidence": ""
  },
  "technical_assessment": {
    "uses_ema20": true,
    "interpretation": "short explanation of whether price action confirms or weakens the thesis read-through",
    "should_affect_grade": "none | cap_A_at_B | push_one_notch | no_sell_without_fundamental_confirmation"
  },
  "note": "plain-English rating explanation",
  "drivers": ["driver 1", "driver 2"],
  "confidence": 0.0
}
```

Then the LLM-final resolver decides:

```text
valid Codex llm_grade -> final grade
invalid/missing Codex grade -> deterministic fallback grade
```

Guidance for Codex to weigh inside the prompt:

```text
Hard breakers, red-team severity, source quality, and EMA20 are decision inputs.
Codex may disagree with deterministic scores if the evidence quality or thesis
context warrants it.
Codex should not assign D from below-EMA price action alone.
Codex should explain material disagreements with deterministic risk in the note
or drivers.
```

This gives the app the best of both worlds:

- deterministic transparency
- LLM judgment as the final CRO decision
- auditability
- backtestability
- clear human review points

## Data Model Changes

Preferred approach: create a new table rather than mutate the old one in place.

New table:

```sql
CREATE TABLE IF NOT EXISTS position_ratings (
    event_id              TEXT PRIMARY KEY,
    ticker                TEXT NOT NULL,
    action                TEXT NOT NULL,     -- hold | sell
    grade                 TEXT NOT NULL,     -- A | B | C | D
    attention_state       TEXT NOT NULL,     -- clean | monitor | watch | broken
    risk_score            REAL NOT NULL,
    risk_components       TEXT NOT NULL,     -- JSON
    latest_close          REAL,
    ema20                 REAL,
    price_vs_ema20_pct    REAL,
    technical_state       TEXT NOT NULL,
    deterministic_grade   TEXT NOT NULL,
    llm_grade             TEXT,
    note                  TEXT NOT NULL,
    drivers               TEXT,
    confidence            REAL NOT NULL,
    thesis_hash           TEXT NOT NULL,
    inputs_hash           TEXT NOT NULL,
    model_used            TEXT NOT NULL,
    rating_version        TEXT NOT NULL,
    decided_at            TEXT NOT NULL,
    UNIQUE (ticker, inputs_hash, rating_version)
);
```

Keep `position_decisions` temporarily for backward compatibility, but move the
dashboard and email to `position_ratings`.

## Backend Implementation Plan

### Phase 0: Add Technical Data Plumbing

Files:

```text
src/sma_monitor/news/fmp_client.py
src/sma_monitor/decision/technicals.py
src/sma_monitor/decision/engine.py
src/sma_monitor/api/routes/positions.py
src/sma_monitor/api/schemas.py
frontend/src/components/Sparkline.tsx
tests/test_technicals.py
```

Tasks:

1. Reuse `price_series.closes` as the source of daily closes.
2. Add `decision/technicals.py` with pure functions for:
   - EMA20 calculation.
   - latest close versus EMA20.
   - consecutive closes below EMA20.
   - 5-day EMA20 slope.
   - technical risk points.
3. Add technical snapshot fields to the rating candidate.
4. Feed technical risk into the deterministic rating engine.
5. Add technical fields to the API.
6. Add EMA20 to the sparkline payload.
7. Keep offline behavior safe: if price history is missing, show `no_price_data`
   and do not penalize the grade.

Minimum tests:

```text
EMA20 math matches a hand-checked fixture
short price series returns no_price_data
above EMA20 gives 0 or low technical risk
below EMA20 gives positive technical risk
extended below EMA20 caps A at B
below EMA20 alone cannot produce D
fundamental C + persistent below EMA20 can become D only with nontechnical risk
```

### Phase 1: Define The Rating Contract

Files:

```text
src/sma_monitor/decision/schema.py
src/sma_monitor/api/schemas.py
frontend/src/types.ts
tests/test_decision_engine.py
tests/test_api.py
```

Tasks:

1. Add `PositionRating` and `RatingCandidate` models.
2. Define grade/action mapping in one backend function.
3. Add tests proving:
   - `A/B/C -> action hold`
   - `D -> action sell`
   - no tile can show `HOLD D`
   - no tile can show `SELL A/B/C`
   - `C` maps to "hold on watch"
   - technical status is carried with the same rating object

### Phase 2: Build Deterministic Rating Engine

Files:

```text
src/sma_monitor/decision/rating.py
tests/test_rating_engine.py
```

Tasks:

1. Implement `risk_components(candidate)`.
2. Implement `grade_from_score(score)`.
3. Implement hard-breaker checks.
4. Implement source-quality caps.
5. Implement technical trend adjustment from EMA20.
6. Return a complete transparent rating object.

Minimum test matrix:

```text
quiet holding -> A / hold
moderate red-team severity 3 -> B or C, never A
severity 4 -> C minimum
severity 5 -> D unless source-quality cap applies
overdue catalyst alone -> B or C, not D
uncorroborated FMP-only issue -> cannot force D
placeholder thesis -> cannot be A
hard breaker from primary source -> D / sell
below EMA20 alone -> at most B/C, never D
extended below EMA20 + real thesis pressure -> can worsen C/D
```

### Phase 3: Refactor Codex Decision Prompt

Files:

```text
src/sma_monitor/decision/prompt.py
src/sma_monitor/decision/engine.py
tests/test_decision_prompt.py
tests/test_decision_engine.py
```

Tasks:

1. Change Codex output schema from `verdict` to structured grade assessment.
2. Ask Codex to map evidence to thesis clauses.
3. Ask Codex for `llm_grade`, `hard_breaker`, `note`, `drivers`, `confidence`.
4. Ask Codex to comment on whether the EMA20 break confirms, contradicts, or is
   irrelevant to the fundamental evidence.
5. Add resolver:

```python
deterministic = rate_deterministically(candidate)
llm = assess_with_codex(candidate)
final = resolve_rating(deterministic, llm, source_policy)
```

6. Persist both deterministic and LLM grade for audit.

### Phase 4: Persist Ratings

Files:

```text
src/sma_monitor/decision/store.py
src/sma_monitor/db.py
schema.md
tests/test_decision_store.py
```

Tasks:

1. Add `position_ratings` schema.
2. Add `save_rating`.
3. Add `latest_rating`.
4. Add `latest_ratings`.
5. Preserve old `position_decisions` until all consumers migrate.
6. Ensure `inputs_hash` still changes when:
   - thesis changes
   - uploaded thesis docs change
   - score evidence changes
   - red-team evidence changes
   - FMP snapshot changes
   - price series / EMA20 status changes
   - rating version changes

### Phase 5: API Migration

Files:

```text
src/sma_monitor/api/routes/positions.py
src/sma_monitor/api/schemas.py
tests/test_api.py
```

Tasks:

1. Return `rating` instead of or alongside `decision`.
2. During transition, keep `decision` derived from `rating`:

```python
legacy_verdict = {
    "A": "hold",
    "B": "hold",
    "C": "watch",
    "D": "sell",
}[grade]
```

3. Add API fields:

```text
action
grade
attention_state
risk_score
risk_components
latest_close
ema20
price_vs_ema20_pct
technical_state
deterministic_grade
llm_grade
grade_delta
previous_grade
```

4. Return sparkline data as an object rather than a raw close array:

```json
{
  "closes": [1.0, 1.1],
  "ema20": [null, null, "..."],
  "latest_close": 10.0,
  "latest_ema20": 9.5,
  "price_vs_ema20_pct": 0.0526,
  "technical_state": "above_ema20"
}
```

5. Update tests so grade/action sync and EMA fields are enforced at API level.

### Phase 6: Frontend Tile Redesign

Files:

```text
frontend/src/components/PositionCard.tsx
frontend/src/components/DecisionChip.tsx
frontend/src/components/GradeBadge.tsx
frontend/src/components/DetailDrawer.tsx
frontend/src/types.ts
frontend/src/theme.ts
```

Tasks:

1. Replace separate grade badge + verdict chip with one synchronized rating
   display:

```text
HOLD A
HOLD B
HOLD C
SELL D
```

2. Use color ladder:

```text
A: green
B: yellow-green / amber
C: orange
D: red
```

3. Show the attention state:

```text
clean
monitor
on watch
broken
```

4. Show grade movement:

```text
Downgraded A -> B
Downgraded B -> C
Upgraded C -> B
```

5. In the detail drawer, add a risk waterfall:

```text
Red-team severity: 18
Thesis clause impact: 22
Top article composite: 11
Catalyst pressure: 5
Technical trend: 12 (below EMA20 for 4 days)
Data-quality penalty: 0
Total risk score: 68 -> D
```

6. Draw two lines on the sparkline:

```text
price close line: existing green/red price stroke
EMA20 line: distinct blue/cyan or amber stroke
```

7. Add a small technical badge:

```text
above 20-EMA
below 20-EMA
extended below 20-EMA
no price data
```

8. On hover or detail view, show:

```text
latest close
EMA20
% above/below EMA20
consecutive closes below EMA20
EMA20 slope
```

### Phase 7: Morning Email Migration

Files:

```text
src/sma_monitor/outputs/thesis_email.py
tests/test_thesis_email.py
```

Tasks:

1. Sort by grade severity:

```text
D first, then C, then B, then A
```

2. Within each grade, sort by portfolio priority:

```text
%NAV descending
near catalyst first
fresh downgrade first
below EMA20 first within the same grade
```

3. Email language:

```text
SELL D: thesis broken / sell review
HOLD C: active watch
HOLD B: monitor
HOLD A: clean
```

### Phase 8: Calibration And Backtesting

Files:

```text
data/scores/calibration_set.yaml
src/sma_monitor/tuning/
tests/test_rating_calibration.py
```

Tasks:

1. Build a labeled historical set:
   - event title
   - source
   - holding
   - thesis at the time
   - correct grade after event
   - whether later evidence confirmed downgrade

2. Backtest:
   - false `D` rate
   - missed `D` rate
   - `C` to `D` conversion rate
   - average days from first `B/C` to thesis break
   - grade stability by source type
   - whether below-EMA signals improved downgrade timing or added false alarms
   - whether EMA20 breaks were more useful for commercial names than clinical
     binary-event names

3. Tune thresholds only from calibration evidence, not taste.
4. Calibrate technical impact separately by position type:
   - commercial / revenue names
   - clinical binary-event names
   - pre-revenue cash-runway names
   - non-biotech / defense / software names

## Other High-Value App Improvements

### 1. Thesis Quality Control

Many sidecars still contain placeholder thesis text. The app should not award an
`A` grade when the thesis is a stub.

Add:

```text
STUB THESIS badge
grade cap: max B until real thesis exists
missing thesis checklist in status bar
```

### 2. Thesis Clause Editor

Add a small editor for thesis checks:

```text
critical assumptions
medium assumptions
low assumptions
invalidators
```

This turns the app from "summarize news" into "monitor stated ownership reasons."

### 3. Grade History

Store and display grade history:

```text
VRTX: A -> B -> C
date of change
driver of change
evidence that caused it
```

This is critical because deterioration trend matters more than a single static
grade.

### 4. Portfolio Heat Map

Add a portfolio-level view:

```text
% NAV in A
% NAV in B
% NAV in C
% NAV in D
top downgrades today
largest C/D exposure
nearest catalysts among C names
% NAV below 20-EMA
largest positions below 20-EMA
C/D names also below 20-EMA
```

This is how a PM actually triages the book.

### 5. Technical Overlay And Trend Dashboard

The sparkline should become a compact price/technical diagnostic, not just a
decoration.

Add:

```text
price line
20-day EMA line
above/below EMA badge
% distance from EMA20
consecutive days below EMA20
filter: show all below-EMA positions
filter: show A/B names that broke below EMA20
filter: show C names where technicals confirm deterioration
```

Expert PM/quant view:

The 20-EMA should be treated as a risk-management and timing variable. It is
especially useful for:

- avoiding complacency in deteriorating holdings still marked HOLD;
- surfacing marginal B/C names before the fundamental evidence fully breaks;
- avoiding adds or aggressive holds while the daily trend is weak;
- prioritizing review time across a 40-60 position book.

It should be less decisive for:

- binary clinical names where the catalyst dominates the chart;
- very illiquid stocks where daily closes are noisy;
- names with stale price history or bad split-adjustment data.

### 6. Evidence Quality Badges

Every rating should show source quality:

```text
Primary source
Corroborated
Aggregator-only
Uncorroborated
Low-quality source
```

This prevents false confidence.

### 7. Human Override And Audit Trail

Add an override path:

```text
manager_override_grade
override_reason
override_expires_at
```

Do not silently replace the model rating. Show both:

```text
System: HOLD C
Manager override: HOLD B until next earnings call
```

### 8. Decision Diffing

When a grade changes, show what changed:

```text
new evidence
removed/resolved evidence
changed thesis text
changed FMP/SEC data
changed EMA20 status
price crossed above/below EMA20
new red-team severity
```

This makes the app operationally useful instead of merely descriptive.

## Testing Strategy

Core invariants:

```text
grade D always implies action sell
action sell always implies grade D
grade A/B/C always implies action hold
grade C is displayed as hold/on-watch, not sell
hard breaker forces D
uncorroborated API data cannot force D
placeholder thesis cannot be A
severity >= 4 cannot be A/B
below EMA20 alone cannot force D
below EMA20 caps a clean A at B unless explicitly configured otherwise
missing price data does not penalize rating
Codex disagreement > one notch creates review flag
```

Recommended test files:

```text
tests/test_rating_engine.py
tests/test_technicals.py
tests/test_decision_engine.py
tests/test_api.py
tests/test_thesis_email.py
frontend component tests if/when a frontend test runner is added
```

## Rollout Plan

Use shadow mode first.

### Step 1: Build V2 Rating In Parallel

For each holding, compute:

```text
old verdict: hold/watch/sell
old grade: A/B/C/D
new rating: HOLD A/B/C or SELL D
```

Do not switch UI yet.

### Step 2: Compare For One Week

Track disagreements:

```text
old HOLD + new HOLD C
old WATCH + new HOLD C
old SELL + new SELL D
old HOLD + new SELL D
old HOLD A + below EMA20 -> new HOLD B
old HOLD C + below EMA20 + weak fundamentals -> new SELL D
```

Manually inspect any severe disagreement.

### Step 3: Switch Dashboard

Replace top tile display with:

```text
HOLD A
HOLD B
HOLD C
SELL D
```

Keep old verdict hidden in the API for one release.

### Step 4: Switch Morning Email

Email should use the new rating system after dashboard validation.

### Step 5: Remove Old Decision Contract

After validation, deprecate standalone `hold/watch/sell` as the primary
backend concept.

## Priority Order

Highest-impact implementation sequence:

1. Add canonical rating contract and tests.
2. Add deterministic rating engine.
3. Add rating persistence.
4. Refactor Codex decision prompt to structured grade assessment.
5. Sync API output.
6. Redesign tile to show one synchronized signal.
7. Add grade history and grade deltas.
8. Add EMA20 sparkline overlay and technical dashboard filters.
9. Add thesis-clause editor.
10. Add calibration/backtesting.
11. Add portfolio heat map.

## Updated Recommendation

Let the LLM be the final source of truth for the grade when it returns valid
structured output. As an AI Agent CRO, the application should not reduce Codex
to a narrative layer after deterministic math has already made the decision.
The deterministic system should make the LLM better informed and make the final
decision easier to audit.

Best structure:

```text
Data ingestion -> scorer -> red team -> 20-EMA technical snapshot
             -> Codex evidence assessment and final grade
             -> final rating -> UI/email
```

That design will make the app feel much more like an institutional monitoring
system: consistent enough for a quant, nuanced enough for a PM, and transparent
enough to trust when a position starts moving from `A` to `B` to `C` before it
becomes a `D`. A close below the 20-day EMA should be one of the early warning
inputs that moves a name from "clean hold" to "pay attention," especially when
the fundamental evidence is also starting to soften.
