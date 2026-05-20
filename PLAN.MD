Implementation Plan v2
System purpose: A news/event monitoring agent for a biotech-heavy SMA. Ingests articles per-holding, scores severity with multipliers, runs a red-team pass against a warning-signs library, and produces (a) real-time alerts above threshold and (b) an evening digest. Not a recourse engine, knowledge graph, thesis extractor, or action recommender.
Custodian: Interactive Brokers (SMA structure). Positions range 0.5%–24% NAV. Mix of clinical-stage and commercial-stage biotech/pharma.

Phase 0 — Foundations (scoping & secrets)

Decide runtime host (local cron, scheduled remote agents, or small VM) — deferred until Phase 1 review.
Inventory secrets needed: IBKR Flex Query token, Exa API key, Claude API key, alert channel credentials. Pick a secret store (env file with restricted perms, or OS keyring).
Define the data directory layout: portfolio/, news_cache/, scores/, digests/, warning_signs/, logs/, factor_buckets/ (taxonomy + query templates per bucket).
Decide identity model: every artifact (article, score, alert, digest entry) must carry a stable event_id (sha256 of canonical fields). Needed for idempotency in Phase 6 and feedback loop in Phase 7.


Phase 1 — Portfolio state layer

Stand up the IBKR Flex Query: choose the report template (positions + cost basis + account NAV), generate the token, confirm the XML/CSV shape.
Write a normalization spec: one row per ticker with qty, market_value, pct_nav, cost_basis, pulled_at.
Define the manual sidecar schema: ticker, conviction_tier (1–5 with explicit definitions per tier), thesis, catalysts[] (date, type, description, confidence).
Decide pull cadence: e.g., 9:00 ET (pre-open) + 15:30 ET (pre-close). Flex Query has minimum re-run windows — confirm those.
Build the joined view (positions ⨝ metadata) as the canonical input every downstream stage reads from.
Define stage classification per holding: clinical_stage vs commercial_stage vs hybrid. This drives bucket weighting in Phase 3 — same event class has different severity by stage.
Sidecar maintenance protocol: when catalyst dates pass without event, mark resolved or roll forward with reason. Stale catalyst lists corrupt the proximity boost.


Phase 2 — News ingestion
2A. Factor bucket taxonomy (recognition surface)
The ingestion layer must tag every article into one or more of the twelve factor buckets below. Buckets are source-agnostic — they describe what is being detected, not where it comes from. All twelve are v1; dropping a bucket means going blind to a category, which is unacceptable for an SMA with positions up to ~24% NAV.
1. Clinical Development. Trial conduct and readout events. Enrollment pace, protocol amendments, partial/full clinical holds, DSMB/IDMC decisions, primary/secondary/subgroup readouts, OS updates on accelerated-approval drugs, safety signals (deaths, SAEs, dose modifications), site GCP issues, investigator-initiated trials revealing real-world signal. Includes competitor trial events when MoA-adjacent (read-through risk lives here).
2. Regulatory Interaction. FDA/EMA/PMDA actions and pathway state changes. PDUFA actions and delays, CRLs, AdCom scheduling and outcomes, BTD/RMAT grants, accelerated approval confirmatory-trial status, label negotiations, REMS modifications, post-marketing requirements, Type A/B/C meeting minutes, advisory committee briefing docs. Distinct from Clinical because these are agency state, not sponsor state.
3. Manufacturing, CMC & Supply. Production capability and continuity. Form 483 observations and Warning Letters (sponsor or CMO), drug shortage list movements, CMO bankruptcy or consolidation, API single-sourcing events, comparability issues, batch failures, cold-chain disruptions, BIOSECURE-driven CDMO transitions. Distinct from Regulatory because supply continuity has commercial impact independent of agency action.
4. Commercial Performance & Revenue Quality [critical, historically under-weighted]. The unit economics underneath net product revenue. Gross-to-net drift, channel inventory days, specialty pharmacy or distributor concentration, formulary changes (commercial, Part D, Medicaid), prior-auth dynamics, 340B exposure shifts, copay assistance enrollment, IRA negotiation list exposure, new patient starts vs. refill rates, pricing actions (WAC, contract), discontinuation rates, sales-force changes, launch-trajectory benchmarking. Sub-pattern granularity belongs in warning_signs.md, not at bucket layer — resist subdividing.
5. Competitive Landscape. Structural threat to TAM/share. Competitor approvals and label expansions, biosimilar/generic ANDA filings and launches, head-to-head trials initiated against your asset, KOL preference shifts in treatment guidelines, new MoA entrants in indication, pricing wars in shared indications. Competitor mechanism failure read-through lives in Clinical; competitor commercial success lives here.
6. Intellectual Property & Litigation. Defensibility and overhang. Patent expiry runway changes, IPR institutions and outcomes, ANDA/Paragraph IV filings, Orange Book listing disputes, infringement suits filed/settled, securities class actions, derivative suits, FDA citizen petitions, DOJ/HHS-OIG/SEC investigations and subpoenas, qui tam unsealings.
7. Capital Structure & Liquidity. Financing capacity and dilution risk. Runway disclosures and runway delta, ATM activations and pace of use, S-3 shelf filings, definitive offerings (secondary, convertible, royalty financing, PIPE), debt covenant disclosures, going-concern auditor language, NOL utilization commentary, royalty/milestone monetization deals. For clinical-stage names this often leads other buckets.
8. Corporate Governance & Insider Behavior. Signal from people closest to the company. Form 4 clusters and 10b5-1 plan modifications, C-suite departures (CEO/CFO/CMO/CSO are differential), board resignations, auditor change, material weakness or restatement, related-party transactions, whistleblower disclosures, unusual 8-K activity outside earnings windows. Cybersecurity/breach events fold here as 8-K surface; promote to own bucket only if tuning shows it earns it.
9. Strategic Transactions & Partnerships. Corporate-action surface. M&A rumors and definitive deals (your name as target or acquirer), license deals in/out, partnership terminations, option exercise/non-exercise, milestone triggers paid or missed, royalty step-up triggers, collaboration scope changes, opt-in/opt-out on shared programs.
10. Scientific Literature & KOL Discourse. Independent third-party signal that can move standard of care. Peer-reviewed publications (NEJM, JAMA, Lancet, JCO, Blood) involving your drug or close analog, conference presentations and abstracts (ASCO, ASH, AACR, ESMO, EASL, AASLD, ADA, AHA), real-world evidence studies, meta-analyses, guideline body updates (NCCN, ESMO, ACC/AHA, IDSA), KOL editorials. Separate because timing is detached from sponsor disclosure cycles.
11. Policy & Regulatory Environment. Rule-of-the-road changes affecting the asset class. IRA negotiation lists and inflation rebate triggers, Part D redesign mechanics, FDA budget or shutdown risk, BIOSECURE Act developments, tariffs on pharma APIs, PBM reform legislation, 340B revisions, ACA-related coverage changes, election-cycle proposals with implementation risk. Scored per-position with low weight; revisit as portfolio-level overlay in Phase 8.
12. Market Microstructure & Flow [deliberately deprioritized, not omitted]. Positioning extremes. Short interest extremes and days-to-cover, unusual options activity (size, strike, expiry clustering), 13F filings showing concentrated buying/selling, index inclusion/exclusion, sell-side rating-distribution extremes, borrow cost spikes. Mostly lagging or noisy; included so it can be explicitly down-weighted in the scorer rather than ignored and re-emerging ad-hoc.
2B. Query template design

Per-holding query templates per bucket: ticker + company name + known aliases/brands + key product lines + bucket-specific terms.
Build-order priority (query infrastructure richness on day 1):

Tier A — rich queries day 1: #1, #2, #7, #9 (small number of known sources, ticker-name queries)
Tier B — moderate build: #3, #4, #5, #6, #8
Tier C — heavy build, real-time alerts mature late: #10 (conference calendars, journal RSS, abstract databases — works in evening digest day 1, doesn't drive real-time alerts until templates mature)
Tier D — sector-level templates, not per-holding: #11, #12


A bucket being Tier C/D in build order is not the same as being phased out — every bucket is ingested in v1; the question is query template richness, not bucket existence.

2C. Ingestion mechanics

Set polling cadence: every 10 min during market hours, hourly overnight, off-hours skip weekends.
Define dedup key: URL + sha256(title+lede). Persist a seen_ids table so re-runs only score new items.
Capture per-article: source, published_at, title, excerpt, full URL, ticker(s) matched, bucket(s) tagged, bucket-tagging confidence.
Multi-bucket articles allowed and expected (e.g., a CRL = #2 + #7 if it triggers a financing question).
Decide retention window: 90 days raw, indefinite for items that triggered alerts.
Source priority hierarchy: company-issued (8-K, press releases) > regulatory (FDA, EMA databases) > peer-reviewed > trade press > general financial press > aggregators. Lower-priority sources need corroboration before triggering Tier-A alerts.


Phase 3 — Severity scorer (neutral pass)

Write the scoring rubric explicitly — what does "8" mean vs "5"? Three axes: financial impact, narrative shift, time-criticality. Rubric is bucket-aware (a #1 event scores narrative shift differently than a #12 event).
Define the multipliers:

position_weight: linear or log scale on %NAV (log preferred — keeps a 24% position from drowning a 1% position's CRL).
conviction_multiplier: e.g., tier 5 = 1.5×, tier 1 = 0.7×.
catalyst_proximity_boost: +50% if any catalyst within 14 days, +100% within 3 days. Applies primarily to buckets #1 and #2 — applying to #4 events near earnings creates false urgency.
bucket_weight: per-bucket scalar in the composite. #12 carries a low weight by design so noise is visible-and-suppressed rather than invisible.
stage_interaction: clinical-stage × #7 (capital) = elevated; commercial-stage × #4 (revenue quality) = elevated. Stage classification from Phase 1.


Pick a composite formula and write it down. Pick threshold T for real-time alerts. Pick a lower secondary threshold T₂ for red-team-only escalation (Phase 4).
Decide model: Sonnet for scoring throughput, Opus for the evening synthesis.
Define the scorer output schema: raw scores per axis, composite, bucket(s), one-line rationale, confidence, inputs hash.
Calibration: hold a small set of historical events (your own portfolio's last 6 months) as a calibration set. Score them and check rank order against your post-hoc judgment before going live.


Phase 4 — Red team pass

Seed warning_signs.md library — start with 15–25 named patterns organized by bucket, so the red team can cite which bucket and which pattern matched. Each entry: name, bucket, definition, historical example, what would invalidate the match.
Initial pattern density target: #4 (revenue quality) gets the most patterns (channel-stuff, gross-to-net drift, specialty pharmacy concentration, etc.) since you flagged it as under-weighted. #1, #2, #8 each get 3–5 patterns. Others get 1–2 to start.
Write the red team prompt: same inputs as scorer, mandate to argue the short case, must cite which warning-sign entries (if any) the article matches.
Constrain the voice in the prompt: pattern-cite, no "sell" verbs, framed as "watch items."
Define red team output schema: bearish thesis paragraph, matched warning-sign IDs, matched bucket(s), severity-of-concern 1–5, what would invalidate the concern.
Red team runs on every article above T₂ (lower threshold), not every article (cost control). Articles above T get both scorer + red team in real-time pipeline; articles between T₂ and T get red team only for digest.
Library growth discipline: warning signs are added from misses in Phase 7, not invented prospectively. Every entry must have a real historical example.


Phase 5 — Outputs
Real-time alerts

Composite > T → format → send to chosen channel. Include: scorer rationale, bucket tag(s), red team note, link, position size, conviction tier, nearest catalyst.
Alert format must be glanceable on mobile — title line carries ticker + bucket + severity; body carries context.
Suppression rules: same event_id doesn't re-alert; same bucket+ticker within N hours requires escalation in severity to re-alert.

Evening digest assembler (~4:15pm ET)

Today's events ranked by composite, grouped by ticker.
Per-position red team summary with triggered warning signs (cite bucket + pattern ID).
"Quiet holdings" tail (no news today, but flag if catalyst is near).
Portfolio-level note: top 3 risk concentrations after today's events (by bucket — e.g., "three holdings hit #4 today").
Bucket-coverage audit footer: which buckets fired today, which were silent, any bucket that's been silent >14 days (possible ingestion gap).
Delivery: email default; mirror to markdown file in digests/YYYY-MM-DD.md for archive.

Feedback loop

A way to mark a past alert as "useful" or "noise" — feeds Phase 7 tuning. Marks persist by event_id.
Optional second mark: "missed" — for events you became aware of via other channels that the agent didn't surface. Critical input for warning-signs library growth.


Phase 6 — Orchestration & reliability

Schedule the pieces: positions pull, news poll, scorer, red team, digest. Define what runs in sequence vs. parallel.
Idempotency: every stage must be re-runnable without duplicate alerts or double-scoring. event_id is the hinge.
Failure handling:

Exa down → skip ingestion cycle, don't crash; flag in next digest.
Flex Query stale → use last known positions and flag the digest.
Scorer model error → retry once, then dead-letter and flag.
Bucket-tagging confidence below threshold → article still ingested, flagged for manual review in digest, not scored real-time.


Cost ceiling: per-day token budget; degrade gracefully (drop red team on T₂–T band first, then drop poll frequency, then drop #10/#11 query templates, then drop #12) if exceeded.
Logging: structured logs of every scoring call (inputs hash, outputs, cost, bucket tags, model used) — needed for Phase 7.


Phase 7 — Tuning loop

After 2 weeks of data, review alert precision: how many real-time alerts were actually meaningful? Break down by bucket — precision should be measured per-bucket, not globally, because buckets have different base rates.
Adjust threshold T, multipliers, rubric weights, and bucket weights based on what you ignored vs acted on.
Grow the warning-signs library from actual misses — every "I wish I'd noticed earlier" becomes a new entry with bucket assignment.
Quarterly: re-validate conviction tiers and catalyst dates against current reality.
Bucket-level review questions:

Is any bucket producing only noise? (Candidate for weight reduction, not removal.)
Is any bucket silent? (Probable ingestion gap — query template needs work.)
Is #10 still a separate bucket, or has it folded into #1+#5 in practice? (Revisit at 4 weeks.)
Should #11 move from per-position to portfolio-level overlay? (Decision point at 8 weeks.)
Is cybersecurity/breach earning its own bucket out of #8?




Phase 8 — Stretch (not for v1)

Cross-position correlation (one ticker's news that implicates a peer holding) — natural extension of bucket #5 read-through logic.
Earnings call transcript ingestion via Scite or similar on transcript days — primarily a #4 enrichment.
Macro overlay: a separate red team pass at the portfolio level, not per-position — likely absorbs #11.
Backtest the scorer against the last 6 months of holdings news to calibrate before going live (partially covered by Phase 3 calibration set; full backtest is stretch).
Promotion candidates: cybersecurity/breach to own bucket; specialty pharmacy concentration as its own sub-tracker under #4.


Open decisions before build

Phase 0: Runtime host.
Phase 1: Conviction tier definitions (what does tier 5 mean vs tier 3?), catalyst source-of-truth process.
Phase 2: Confirm all twelve buckets are v1. Confirm Tier A/B/C/D build-order priority for query templates.
Phase 3: Composite formula, threshold T, secondary threshold T₂, bucket weight initialization.
Phase 4: Initial 15–25 warning-sign patterns, with #4 carrying the heaviest density.
Phase 5: Alert channel selection (email + what else?).