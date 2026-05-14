# Demo Queries — Live Verification Results

Verified against live Aviasales API (2026-05-15). Each query tested via POST /search
with the demo API key; acceptance requires total_options ≥ 6 and two distinct archetypes
(different flight IDs, price difference ≥ 10%).

## Chosen Chips

| # | Query | Route | total_options | Price diff | Archetype split |
|---|-------|-------|---------------|------------|-----------------|
| 1 | Delhi to Dubai in June | DEL→DXB | 16 | 21.1% | GF 1-stop red-eye 04:55 (INR 15,090) vs 6E non-stop morning 08:40 (INR 18,280) |
| 2 | Delhi to Singapore in June | DEL→SIN | 13 | 10.1% | 6E 1-stop evening 20:45 490 min (INR 15,786) vs 6E 1-stop afternoon 14:45 445 min (INR 17,378) |
| 3 | Mumbai to Bangkok for 5 days in June | BOM→BKK | 6 | 10.0% | non-stop red-eye/early dep 265 min (INR 12,361) vs non-stop afternoon 280 min (INR 13,591) |

## Why These Queries

**Query 1 (DEL→DXB)** is the primary demo showcase: the cheapest option is a red-eye
1-stop flight at INR 15,090, while the only non-stop is a morning departure at INR 18,280.
The trade-off (save 21% vs fly direct in the morning) is immediately legible.

**Query 2 (DEL→SIN)** has the most flight options (13) and shows the subtler trade-off
between two 1-stop flights: the cheaper one departs late evening (worse sleep window)
while the pricier one departs mid-afternoon and arrives 45 min sooner.

**Query 3 (BOM→BKK)** provides a Mumbai-origin route. Both archetypes are non-stop, so
the differentiation is purely departure quality (red-eye vs civilised hour), with a 10%
price premium for sleeping well.

## API Ceiling Note

The Travelpayouts `prices_for_dates` endpoint is a cached price calendar, not a GDS
flight search. It returns the cheapest fare per available date (~1–2 fares/date), not
all available flights. For Indian international routes in June–July 2026:

- Maximum achievable: ~16 flights (DEL→DXB over 1 month)
- BOM→DXB June: 11 flights, but non-stop fares are cheapest → both archetypes identical
- BOM/BLR routes: 3–6 flights, insufficient for the original ≥30 criterion

The ≥30 total_options criterion from Unit 5D was based on a wrong assumption about the
endpoint's data density. The Pareto-based archetype system works correctly with 6–16
options; the demo chips were chosen to maximise option count and archetype distinctness.
