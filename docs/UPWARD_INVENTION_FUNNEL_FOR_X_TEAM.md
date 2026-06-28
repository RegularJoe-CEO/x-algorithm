# Upward Invention Funnel — Proposal for X For You Algorithm

**Fork:** [github.com/RegularJoe-CEO/x-algorithm](https://github.com/RegularJoe-CEO/x-algorithm)  
**Branch:** `feature/upward-invention-funnel`  
**Contact context:** Independent research proposal to address structural bias against novel, beneficial ideas in engagement-based recommendation.

---

## Problem

The open-sourced x-algorithm ranks posts by predicted engagement from user history. Novel scientific and technical ideas — especially from inventors with no existing cluster — are suppressed at:

1. **Retrieval** — embedding similarity excludes posts outside the user's neighborhood
2. **Ranking** — zero historical engagement → near-zero score

Breakthrough ideas often come from *outside* existing clusters. The system cannot wait for organic cluster formation.

---

## Proposal: Two-Phase "Upward Invention Funnel"

### Phase 1 — Surface (modest)

XAI flags post as potential novel + beneficial idea (energy efficiency, harm reduction).

- Exploration injection into candidate pool
- Additive tier boost (Tier 1: +0.40, Tier 1.5: +0.25, Tier 2: +0.15)
- **24h float** — unrelated negatives ignored
- Goal: show to the *right* domain-matched users, not masses

### Phase 2 — Glump (100×)

When **≥3 domain-relevant positive engagements** occur within **24h** (or by **48h** max), multiply ranking score by **100×**.

- Goal: rapidly expand proven-good ideas to all users who should see them
- Trigger requires domain expertise signals (replies, follows, dwell from matching clusters) — not generic likes

### Confirmed veto (high bar)

After 24h float, demote only if **≥5 domain-relevant negative** engagements from diverse clusters — or hard safety block.

- Single downvote / political animus / competitor sabotage: **ignored**
- Chronic downvoters, topic-mismatch vetoes, coordinated bursts: **discounted**

---

## Anti-Skeptic LLM Design

Grok-style models default to "no papers → discount." This funnel uses:

- **ScamBlocker** (skeptical) — separate from surfacing
- **InventionSurfacer** (generous to earnest novelty) — explicit prompt: absence of external validation is *expected*
- **Rules floor** — patent #, mechanism, falsifiable claim can set tier even if LLM is skeptical

---

## Insertion Points (x-algorithm codebase)

| Component | Path | Change |
|-----------|------|--------|
| Exploration source | `home-mixer/sources/` | New source for low-engagement high-benefit posts |
| Invention scorer | `home-mixer/scorers/` | New `InventionFunnelScorer` between PhoenixScorer and RankingScorer |
| Cluster monitor | `home-mixer/side_effects/` | Phase 2 trigger + veto tracking |
| Offline sim | `phoenix/invention_funnel_sim.py` | Runnable reference implementation |

---

## Run the demo

```bash
cd phoenix
python invention_funnel_sim.py
```

Shows rank displacement: cold-start inventor (0.04) → 100× glump (4.0) beating viral posts (0.58) after domain validation.

---

## Full specification

See `docs/upward_invention_funnel_architecture_analysis.md` for complete architecture, tier definitions, anti-brigading rules, and implementation plan.

---

*This proposal is offered in the spirit of the open-source x-algorithm release. Feedback welcome from the XAI / x-algorithm team.*