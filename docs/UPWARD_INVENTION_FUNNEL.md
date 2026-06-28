# Upward Invention Funnel — Engineering Specification

**Status:** Proposal + reference implementation (not production-ready Rust integration)  
**Fork:** `RegularJoe-CEO/x-algorithm`  
**Branch:** `feature/upward-invention-funnel`  
**Upstream:** `xai-org/x-algorithm`

---

## What this PR contains

| Deliverable | Path | Purpose |
|-------------|------|---------|
| Scoring logic (Python) | `phoenix/invention_funnel.py` | Spec-faithful Phase 1/2/veto state machine |
| Runnable demo | `phoenix/invention_funnel_sim.py` | Rank displacement proof for reviewers |
| This document | `docs/UPWARD_INVENTION_FUNNEL.md` | Engineer-facing design spec |

**Not in this PR:** Rust `home-mixer` changes, Grox wiring, exploration index, or production feature flags. Those are the implementation roadmap below.

```bash
cd phoenix && python3 invention_funnel_sim.py
```

---

## Problem statement

The For You algorithm suppresses novel, low-engagement posts at two choke points:

1. **Retrieval** (`PhoenixSource`, `phoenix/run_pipeline.py`) — `corpus_repr @ user_repr` dot product; posts outside the user's embedding neighborhood never enter the candidate pool.
2. **Ranking** (`RankingScorer`) — final score is a weighted sum of predicted engagement probabilities only; zero-history inventors score near zero.

Inventors announcing work-in-progress (no papers, no cluster, no audience) are structurally excluded. This proposal adds a **two-phase funnel** to surface earnest novel ideas to domain-matched users, then amplify when domain experts validate.

---

## How For You works today (relevant paths)

```
ForYouCandidatePipeline          ← blends posts + ads + WTF (no ranking)
    └── ScoredPostsSource
            └── PhoenixCandidatePipeline   ← all post scoring happens here
                    Sources → Hydrators → Filters → Scorers → TopKScoreSelector
```

### Scorer chain (insertion point)

```299:300:home-mixer/candidate_pipeline/phoenix_candidate_pipeline.rs
        let scorers: Vec<Box<dyn Scorer<ScoredPostsQuery, PostCandidate>>> =
            vec![phoenix_scorer, ranking_scorer, vm_ranker];
```

| Scorer | File | Output |
|--------|------|--------|
| `PhoenixScorer` | `home-mixer/scorers/phoenix_scorer.rs` | `PhoenixScores` (per-action probabilities) |
| `RankingScorer` | `home-mixer/scorers/ranking_scorer.rs` | `weighted_score`, `score` |
| `VMRanker` | `home-mixer/scorers/vm_ranker.rs` | Secondary rank pass |

`PostCandidate` (`home-mixer/models/candidate.rs`) already has `tweet_text`, `fav_count`, `reply_count`, `score` — sufficient for v1 scoring without new hydrators.

### Grox (not wired to ranking today)

`grox/` runs offline/streaming classifiers (spam, safety, embeddings). Outputs are **not consumed** by `PhoenixCandidatePipeline` scorers. Future work: hydrator reading Grox annotations for tier assignment.

---

## Proposed system: two-phase funnel

```
                    ┌─────────────────────────────────────┐
                    │  ScamBlocker (hard block only)      │
                    │  Grox safety / spam patterns        │
                    └─────────────────┬───────────────────┘
                                      │ pass
                    ┌─────────────────▼───────────────────┐
                    │  InventionSurfacer (tier assign)    │
                    │  Rules floor + constrained LLM      │
                    └─────────────────┬───────────────────┘
                                      │
          ┌───────────────────────────┼───────────────────────────┐
          │                           │                           │
    Tier 0 (none)              Phase 1 — Surface           Phase 2 — Glump
    baseline ranking           exploration + additive      score × 100
                               24h float window            (after cluster validation)
```

### Constants (locked for v1 calibration)

| Parameter | Value |
|-----------|-------|
| Tier 1 additive boost | +0.40 |
| Tier 1.5 additive boost | +0.25 |
| Tier 2 additive boost | +0.15 |
| Glump multiplier | **100×** (on `engagement_score` after cluster validation) |
| Phase 2 trigger | ≥3 domain-relevant positives; kicks in immediately if within 24h; window extends to 48h |
| Float window | 24h — engagement-based negatives ignored |
| Confirmed veto | ≥5 domain-relevant negatives from diverse clusters after float |
| Hard block | Grox safety labels only (instant) |

### Tier assignment (Phase 1 entry)

| Tier | Criteria | Phase 1 effect |
|------|----------|----------------|
| **1** | Novel + direct evidence (preprint, numbers+method, public repo) | +0.40 additive |
| **1.5** | Novel + verifiable in principle (patent #, mechanism, private repo described) | +0.25 additive |
| **2** | Novel + coherent + earnest, pre-evidence | +0.15 additive |
| **0** | Not novel, or unfalsifiable hype | No boost |

**Anti-skeptic rule:** Absence of papers, scholars, media coverage, or "others discussing it" must **never** lower tier. LLM and rules are split:

- `ScamBlocker` — skeptical (blocks harm/spam only)
- `InventionSurfacer` — generous to earnest novelty; rules set tier **floor**, LLM cannot lower floor due to missing external validation

### Phase 2 — Glump trigger

Domain-relevant positive signals (not generic likes):

- Reply/quote from users with topic overlap
- Follow-author from domain-relevant accounts
- Dwell from matching Grok topic clusters
- `mutual_follow_jaccard` proximity (existing hydrator)

When ≥3 such signals arrive within 48h (immediate if within 24h):

```
final_score = engagement_score × 100
```

Reference sim shows cold-start score 0.04 → 4.00, beating viral posts at 0.58.

### Anti-brigading (veto)

Single downvote / political animus / competitor click → **ignored**.

Confirmed demotion requires **all** of:

1. Domain relevance of vetoing users
2. ≥5 independent domain-negative signals over ≥24h (post-float)
3. Vetoing accounts from ≥3 distinct clusters (anti-coordination)
4. Domain positives ≤ domain negatives
5. Vetoer not a chronic downvoter (>80% negative on same author)

Unrelated-topic negatives (e.g. politics follower on a cooling-tech post) are discarded.

---

## Components to build (implementation roadmap)

### Phase A — This PR (done)

- [x] Python reference scorer (`invention_funnel.py`)
- [x] Runnable simulation (`invention_funnel_sim.py`)
- [x] Engineering spec (this document)

### Phase B — home-mixer (Rust)

| Component | Location | Work |
|-----------|----------|------|
| `InventionFunnelScorer` | `home-mixer/scorers/invention_funnel_scorer.rs` | Insert between `PhoenixScorer` and `RankingScorer` in `phoenix_candidate_pipeline.rs` |
| Feature flags | `xai_feature_switches` params | `EnableInventionFunnel`, `GlumpMultiplier`, tier boosts |
| Post metadata | Strato/Redis store | Persist `phase1_flagged`, `tier`, `domain_positive_count`, `glump_active` per tweet |
| `InventionFunnelSideEffect` | `home-mixer/side_effects/` | Track engagement events; evaluate Phase 2 trigger and veto bar |

### Phase C — Retrieval (required for cold-start inventors)

| Component | Location | Work |
|-----------|----------|------|
| `ExplorationSource` | `home-mixer/sources/exploration_source.rs` | Inject N low-engagement tier-flagged posts per request, bypassing embedding top-K |
| Benefit index | New service or Grox sink | Recent posts + tier annotations for exploration pool |

Without Phase C, additive boost only helps posts **already retrieved** by Phoenix — the dominant failure mode for inventors.

### Phase D — Tier assignment (Grox integration)

| Component | Location | Work |
|-----------|----------|------|
| `InventionSurfacer` task | `grox/tasks/` | Async tier classification with anti-skeptic prompt |
| Rules engine | `grox/lib/` or scorer | Patent #, DOI, unit detection → tier floor |
| Hydrator | `home-mixer/candidate_hydrators/` | Read tier label onto `PostCandidate` before scoring |

---

## Scoring pseudocode (matches `invention_funnel.py`)

```python
if safety_hard_block:
    return DEMOTED

if not phase1_flagged:
    return engagement_score  # baseline

if hours_since_post >= 24 and confirmed_veto(events):
    return engagement_score * 0.01  # demoted

if domain_positive_count >= 3 within 48h:
    return engagement_score * 100  # glump

return engagement_score + tier_additive_boost  # phase 1 surface
```

---

## Telemetry & success metrics

Hook into existing side effects:

| Existing | File | Use |
|----------|------|-----|
| `ScoredStatsSideEffect` | `side_effects/scored_stats_side_effect.rs` | Rank position at K=1,10,35 |
| `RerankingKafkaSideEffect` | `side_effects/reranking_kafka_side_effect.rs` | Top-50 candidate export |
| `ServedCandidatesKafkaSideEffect` | outer pipeline | Served impressions |
| `ClientEventsKafkaSideEffect` | outer pipeline | Engagement feedback loop |

Proposed metrics: boosted reach, expert cluster formation rate (72h), harm rate (report/block), glump conversion (organic sustain after 7d).

---

## Risks & mitigations

| Risk | Mitigation |
|------|------------|
| Feed takeover by glump | Cap simultaneous glump posts; 7-day decay |
| Gaming tier assignment | Specificity requirements; ScamBlocker; human review queue during calibration |
| LLM skepticism bias | Separate evaluators; rules floor; explicit anti-skeptic prompt |
| Brigading / false veto | 24h float; 5-signal corroboration bar; chronic downvoter discount |
| Retrieval gap | Phase C exploration source (required for full funnel) |

---

## Reviewer quick-start

1. Read this document (10 min)
2. Run `python3 phoenix/invention_funnel_sim.py` (30 sec)
3. Skim `phoenix/invention_funnel.py` — constants match tables above
4. See `home-mixer/candidate_pipeline/phoenix_candidate_pipeline.rs:299` for scorer insertion point

---

*Offered in the spirit of the open-source x-algorithm release. Feedback welcome from the XAI team.*