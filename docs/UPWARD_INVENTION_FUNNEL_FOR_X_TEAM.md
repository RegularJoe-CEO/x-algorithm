# Upward Invention Funnel — PR Summary

**Full spec:** [UPWARD_INVENTION_FUNNEL.md](./UPWARD_INVENTION_FUNNEL.md)

## One-paragraph pitch

Novel inventors with zero engagement are excluded by Phoenix retrieval (embedding similarity) and near-zero ranking scores. This PR adds a **reference implementation** of a two-phase funnel: **Phase 1** surfaces flagged ideas to domain-matched users (additive boost + 24h float); **Phase 2** applies **100× glump** when ≥3 domain experts engage within 48h. Anti-brigading veto requires ≥5 corroborated domain negatives. Rust home-mixer integration is spec'd but not included — this PR is the sim + design for review.

## Run the demo

```bash
cd phoenix && python3 invention_funnel_sim.py
```

## Files changed

- `phoenix/invention_funnel.py` — scoring state machine
- `phoenix/invention_funnel_sim.py` — demo scenarios
- `docs/UPWARD_INVENTION_FUNNEL.md` — engineering specification