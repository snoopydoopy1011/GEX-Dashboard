# Alerts Attention Engine Plan

## Why this exists

The current Live Alerts lane is useful when a single event fires, but it breaks down in two common cases:

1. Alerts disappear as soon as the backend stops re-emitting them on the next poll.
2. Adjacent-strike bursts create a wall of visually similar cards, so the user loses the important story.

This plan introduces a more durable "attention engine" for alerts and flow pulse without overstating what the signal knows. The goal is better persistence, prioritization, and directional context. The goal is not to claim precise trader intent from incomplete chain metadata.

## Ground rules

- No analytical formula changes to GEX/DEX/Vanna/Charm/flow math.
- No framework changes; keep vanilla JS + CSS.
- Preserve the single-file `ezoptionsschwab.py` structure.
- Keep alert colors on existing design tokens only.
- Any markup change in the flow-event lane must be mirrored in both the server-rendered HTML and the rebuild path around `ensurePriceChartDom()` / `buildAlertsPanelHtml()` when applicable.

## Current diagnosis

### Data path

- `compute_trader_stats()` rebuilds `out['alerts']` on every refresh.
- `renderRailAlerts(list)` renders directly from the current payload.
- The current 15-minute frontend age filter only applies to alerts that are still present in `stats.alerts`.

### Practical effect

- If an alert condition fires on one tick and clears on the next, the alert disappears immediately even if it is still actionable for the user.
- The Live Alerts lane behaves like a raw trigger feed, not a durable view of what has mattered recently.
- Flow Pulse already has richer per-contract context than most alert types, so it is the best first place to add directional lean later.

## Design principles

1. Improve attention, not certainty.
2. Keep ambiguous alerts neutral or structural rather than forcing a bull/bear story.
3. Persist meaningful events long enough to be useful, but not so long that the lane becomes stale.
4. Prefer clustering and summarization over more tiles.
5. Roll out in phases so each step is verifiable in live market conditions.

## Proposed rollout

### Phase 1: Persistent alert buffer and retention tiers

#### Goal

Stop losing alerts between ticks and make the lane hold onto the most important events long enough to matter.

#### Changes

- Add a client-side alert buffer keyed by `(ticker, alert.id)`.
- Merge each new `stats.alerts` payload into the buffer instead of replacing the rendered list outright.
- Track:
  - `firstSeenMs`
  - `lastSeenMs`
  - `refreshCount`
  - `priority`
  - `tier`
- Apply per-tier retention:
  - `critical`: 10 minutes
  - `active`: 5 minutes
  - `recent`: 2 minutes
- Scope the buffer per ticker so switching `SPY -> QQQ` clears stale symbols cleanly.
- Render the lane from the buffered list, not the raw payload.

#### Initial priority rules

- `critical`
  - wall shifts
  - flow-pulse burst alerts
  - short-gamma / long-gamma regime changes
- `active`
  - IV surge
  - heavy vol/OI
  - large volume spike
- `recent`
  - near wall / near gamma flip
  - low-detail informational alerts

#### UI shape for Phase 1

- One pinned lead card for the top alert.
- Up to three supporting cards after the pinned card.
- One overflow summary card when additional active alerts remain buffered.
- Keep severity coloring on the existing left-border system.
- Add a subtle "refreshed" state when the same alert re-fires.

#### Verification

- Alert remains visible after the backend stops re-emitting it, until its retention tier expires.
- Re-fired alert updates its timestamp instead of creating duplicates.
- Switching tickers clears the prior symbol's buffered alerts.
- Alerts badge count reflects buffered unseen alerts instead of only the latest payload.

### Phase 2: Adjacent-strike clustering

#### Goal

Collapse visually repetitive bursts into one summary card so the user sees the story, not a row of near-identical strikes.

#### Changes

- Cluster alerts when all are true:
  - same ticker
  - same alert family (`flow_pulse`, `iv_surge`, `voi_ratio`, later `vol_spike` if enriched)
  - same option side when known
  - strikes are adjacent or near-adjacent within the active strike interval
  - timestamps fall within the active retention window
- Cluster card fields:
  - strike range
  - count
  - strongest strike
  - strongest magnitude
  - combined estimated premium when available

#### Notes

- Do not cluster ambiguous alerts that lack a reliable `option_type`.
- `vol_spike` remains unclustered until the payload distinguishes call-side vs put-side activity.

#### Verification

- A burst across neighboring strikes renders as one cluster card instead of many nearly identical tiles.
- Underlying individual alerts still exist in the buffer for overflow/detail views.

### Phase 3: Flow Pulse directional lean

#### Goal

Add a conservative directional read where the metadata already supports it.

#### Changes

- Compute a per-row `lean` and `lean_score` for `flow_pulse` rows.
- Use inputs already available or easy to derive:
  - `option_type`
  - `strike`
  - `moneyness_pct`
  - `side`
  - `premium_delta_1m`
  - `pace_1m`
  - expiry / DTE bucket
- Render a row-level pill:
  - `Bullish`
  - `Bearish`
  - `Hedge`
  - `Mixed`
- Add a header aggregate:
  - premium-weighted net 1-minute lean across visible pulse rows

#### Guardrails

- Far OTM puts default to `Hedge`, not `Bullish`.
- Structural signals remain separate from directional pulse reads.
- Low-confidence rows can render `Mixed` rather than forcing a direction.

### Phase 4: Alert payload enrichment and alert-level lean

#### Goal

Only classify alert types that carry enough metadata to support it.

#### Payload additions

- `alert_type`
- `option_type` where known
- `side` where known
- `expiry_iso`
- `dte_bucket`
- `moneyness_pct`
- `moneyness_band`
- `premium_est`
- `pace`
- `direction_classifiable`
- `direction_label`
- `direction_score`

#### Backend targets

- `compute_flow_alerts()`
- flow-pulse alert creation inside `compute_trader_stats()`

#### Rules

- `vol_spike` stays unclassified until its source data is split by option side.
- Wall shifts and gamma flip proximity can render as `Structural` with optional secondary copy, not forced bull/bear.
- Regime alerts stay volatility-structural, not directional.

### Phase 5: Detail surfaces and polish

#### Goal

Expose buffered and clustered context without overwhelming the main lane.

#### Candidates

- Expandable overflow card or popover
- Full buffered-history drawer
- User controls for retention windows
- Toggle between `Pinned / Active / Recent` and `Raw feed`

## Implementation anchors

### Frontend

- `renderRailAlerts`
- `renderFlowPulse`
- `ensurePriceChartDom`
- flow-event lane markup near `.flow-event-lane`, `.rail-alerts-list`, `.rail-pulse-list`

### Backend

- `compute_trader_stats`
- `compute_flow_alerts`
- `build_flow_pulse_snapshot`

## Delivery sequence

1. Phase 1 client buffer + retention tiers
2. Phase 1 lane layout refresh
3. Phase 2 adjacent-strike clustering
4. Phase 3 pulse lean and aggregate
5. Phase 4 enriched alert payloads and alert-level lean

## Risks

- Ticker switch leakage if buffer scope is not reset correctly.
- Duplicate DOM drift if lane markup changes are not mirrored in rebuild paths.
- Overconfident directional labels if payload enrichment lags behind UI work.
- Mobile/medium-width layouts if the pinned card becomes too wide.

## Definition of done for the first slice

- Live alerts persist across backend refreshes for a tier-based interval.
- The lane always shows a single top alert plus a manageable number of supporting cards.
- Overflow is summarized instead of flooding the viewport.
- Existing alert severity semantics remain intact.
- No changes to underlying flow/GEX calculations.
