# Optimizer Eval Review Queue

This file contains all (input, output) pairs from the dry-run baseline.
**GG: please rate each block as `good`, `borderline`, or `bad`.**

## How to review

For each block:
- **Label correct?** Do the archetype labels (best-value / best-experience) make sense given the flight data?
- **Explanation quality?** (dry-run uses deterministic fallback text; live LLM explanations pending API keys)
- **Value trade-off?** Does the price/quality split seem reasonable for the route?

---

## Scenario opt-001 — DEL-DXB (2026-06-01)

**Input:** 10 flights, route DEL-DXB

### Archetype: best-value
- Flight: G8G8-412
- Price: INR 18,500
- Duration: 480min, 2 stop(s)
- Departs: 2026-06-01T02:30:00+00:00
- Score: value=0.921, exp=0.507
- Explanation: Lowest price at INR 18,500 with 2 stop(s). Best choice if budget is the priority.

### Archetype: best-experience
- Flight: EKEK-511
- Price: INR 43,500
- Duration: 225min, 0 stop(s)
- Departs: 2026-06-01T10:30:00+00:00
- Score: value=0.812, exp=0.810
- Explanation: Fastest option at 3h total, non-stop. Best choice for comfort and convenience.

**GG Rating:** [ ] good  [ ] borderline  [ ] bad

**Notes:**

---

## Scenario opt-002 — DEL-DXB (2026-06-08)

**Input:** 10 flights, route DEL-DXB

### Archetype: best-value
- Flight: G8G8-412
- Price: INR 18,500
- Duration: 480min, 2 stop(s)
- Departs: 2026-06-08T02:30:00+00:00
- Score: value=0.921, exp=0.507
- Explanation: Lowest price at INR 18,500 with 2 stop(s). Best choice if budget is the priority.

### Archetype: best-experience
- Flight: EKEK-511
- Price: INR 43,500
- Duration: 225min, 0 stop(s)
- Departs: 2026-06-08T10:30:00+00:00
- Score: value=0.812, exp=0.810
- Explanation: Fastest option at 3h total, non-stop. Best choice for comfort and convenience.

**GG Rating:** [ ] good  [ ] borderline  [ ] bad

**Notes:**

---

## Scenario opt-003 — DEL-DXB (2026-06-15)

**Input:** 10 flights, route DEL-DXB

### Archetype: best-value
- Flight: G8G8-412
- Price: INR 18,500
- Duration: 480min, 2 stop(s)
- Departs: 2026-06-15T02:30:00+00:00
- Score: value=0.921, exp=0.507
- Explanation: Lowest price at INR 18,500 with 2 stop(s). Best choice if budget is the priority.

### Archetype: best-experience
- Flight: EKEK-511
- Price: INR 43,500
- Duration: 225min, 0 stop(s)
- Departs: 2026-06-15T10:30:00+00:00
- Score: value=0.812, exp=0.810
- Explanation: Fastest option at 3h total, non-stop. Best choice for comfort and convenience.

**GG Rating:** [ ] good  [ ] borderline  [ ] bad

**Notes:**

---

## Scenario opt-004 — DEL-DXB (2026-06-22)

**Input:** 10 flights, route DEL-DXB

### Archetype: best-value
- Flight: G8G8-412
- Price: INR 18,500
- Duration: 480min, 2 stop(s)
- Departs: 2026-06-22T02:30:00+00:00
- Score: value=0.921, exp=0.507
- Explanation: Lowest price at INR 18,500 with 2 stop(s). Best choice if budget is the priority.

### Archetype: best-experience
- Flight: EKEK-511
- Price: INR 43,500
- Duration: 225min, 0 stop(s)
- Departs: 2026-06-22T10:30:00+00:00
- Score: value=0.812, exp=0.810
- Explanation: Fastest option at 3h total, non-stop. Best choice for comfort and convenience.

**GG Rating:** [ ] good  [ ] borderline  [ ] bad

**Notes:**

---

## Scenario opt-009 — DEL-SIN (2026-06-01)

**Input:** 8 flights, route DEL-SIN

### Archetype: best-value
- Flight: TRTR-455
- Price: INR 24,600
- Duration: 540min, 2 stop(s)
- Departs: 2026-06-01T01:30:00+00:00
- Score: value=0.902, exp=0.440
- Explanation: Lowest price at INR 24,600 with 2 stop(s). Best choice if budget is the priority.

### Archetype: best-experience
- Flight: SQSQ-401
- Price: INR 56,200
- Duration: 330min, 0 stop(s)
- Departs: 2026-06-01T09:30:00+00:00
- Score: value=0.722, exp=0.810
- Explanation: Fastest option at 5h total, non-stop. Best choice for comfort and convenience.

**GG Rating:** [ ] good  [ ] borderline  [ ] bad

**Notes:**

---

## Scenario opt-010 — DEL-SIN (2026-06-08)

**Input:** 8 flights, route DEL-SIN

### Archetype: best-value
- Flight: TRTR-455
- Price: INR 24,600
- Duration: 540min, 2 stop(s)
- Departs: 2026-06-08T01:30:00+00:00
- Score: value=0.902, exp=0.440
- Explanation: Lowest price at INR 24,600 with 2 stop(s). Best choice if budget is the priority.

### Archetype: best-experience
- Flight: SQSQ-401
- Price: INR 56,200
- Duration: 330min, 0 stop(s)
- Departs: 2026-06-08T09:30:00+00:00
- Score: value=0.722, exp=0.810
- Explanation: Fastest option at 5h total, non-stop. Best choice for comfort and convenience.

**GG Rating:** [ ] good  [ ] borderline  [ ] bad

**Notes:**

---

## Scenario opt-011 — DEL-SIN (2026-06-15)

**Input:** 8 flights, route DEL-SIN

### Archetype: best-value
- Flight: TRTR-455
- Price: INR 24,600
- Duration: 540min, 2 stop(s)
- Departs: 2026-06-15T01:30:00+00:00
- Score: value=0.902, exp=0.440
- Explanation: Lowest price at INR 24,600 with 2 stop(s). Best choice if budget is the priority.

### Archetype: best-experience
- Flight: SQSQ-401
- Price: INR 56,200
- Duration: 330min, 0 stop(s)
- Departs: 2026-06-15T09:30:00+00:00
- Score: value=0.722, exp=0.810
- Explanation: Fastest option at 5h total, non-stop. Best choice for comfort and convenience.

**GG Rating:** [ ] good  [ ] borderline  [ ] bad

**Notes:**

---

## Scenario opt-012 — DEL-SIN (2026-06-22)

**Input:** 8 flights, route DEL-SIN

### Archetype: best-value
- Flight: TRTR-455
- Price: INR 24,600
- Duration: 540min, 2 stop(s)
- Departs: 2026-06-22T01:30:00+00:00
- Score: value=0.902, exp=0.440
- Explanation: Lowest price at INR 24,600 with 2 stop(s). Best choice if budget is the priority.

### Archetype: best-experience
- Flight: SQSQ-401
- Price: INR 56,200
- Duration: 330min, 0 stop(s)
- Departs: 2026-06-22T09:30:00+00:00
- Score: value=0.722, exp=0.810
- Explanation: Fastest option at 5h total, non-stop. Best choice for comfort and convenience.

**GG Rating:** [ ] good  [ ] borderline  [ ] bad

**Notes:**

---
