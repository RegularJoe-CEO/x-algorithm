# Upward Invention Funnel — Architecture Analysis

**Project:** Modify `xai-org/x-algorithm` (fork: `RegularJoe-CEO/x-algorithm`)  
**Branch:** `feature/upward-invention-funnel`  
**Date:** 2026-06-28  
**Status:** Analysis complete — design options ready for human approval (no implementation)

---

## Executive summary

The For You feed is **not** one monolithic pipeline. Post ranking happens inside **`PhoenixCandidatePipeline`**; the outer **`ForYouCandidatePipeline`** only blends scored posts with ads, who-to-follow, and prompts.

Novel ideas are suppressed at **two choke points**:

1. **Retrieval** — Phoenix embeds user history and retrieves top-K via dot product. Posts outside the user's embedding neighborhood never enter the candidate set.
2. **Ranking** — `PhoenixScorer` + `RankingScorer` predict and weight **historical engagement probabilities only**. Zero-engagement novelty gets crushed before selection.

**Grox** (content understanding: embeddings, safety, spam) runs as a **separate offline/streaming system** and is **not wired** into benefit/novelty scoring today.

The cleanest v1 insertion point is a new **`InventionFunnelScorer`** between `PhoenixScorer` and `RankingScorer`, gated by feature flags, with optional retrieval-side exploration in v2.

---

## Pipeline architecture

### Layer 1: For You response (`ForYouCandidatePipeline`)

| Stage | Components | Role |
|-------|------------|------|
| Query hydrators | served history, past timestamps | Request context |
| Sources | `ScoredPostsSource` (calls Phoenix), ads, WTF, prompts, push | Feed composition |
| Selector | `BlenderSelector` | Mix post slots + non-post items |
| Side effects | served history, kafka stats | Logging / state |

**Key insight:** No post scoring here. All ranking happens inside `ScoredPostsServer` → `PhoenixCandidatePipeline`.

### Layer 2: Post ranking (`PhoenixCandidatePipeline`)

Execution order (from `candidate-pipeline/candidate_pipeline.rs`):

```
QueryHydrators → Sources → Hydrators → Filters → Scorers → Selector → PostSelectionHydrators → PostSelectionFilters → SideEffects
```

#### Sources (candidate retrieval)

| Source | File | What it fetches |
|--------|------|-----------------|
| `ThunderSource` | `home-mixer/sources/thunder_source.rs` | In-network posts from followed users |
| `PhoenixSource` | `home-mixer/sources/phoenix_source.rs` | Out-of-network via embedding retrieval |
| `PhoenixTopicsSource` | `home-mixer/sources/phoenix_topics_source.rs` | Topic-targeted retrieval |
| `PhoenixMOESource` | `home-mixer/sources/phoenix_moe_source.rs` | Mixture-of-experts retrieval variant |
| `TweetMixerSource` | `home-mixer/sources/tweet_mixer_source.rs` | Additional candidate mixing |
| `CachedPostsSource` | `home-mixer/sources/cached_posts_source.rs` | Cached/rerank path |

#### Scorer chain (sequential — insertion point)

```299:300:home-mixer/candidate_pipeline/phoenix_candidate_pipeline.rs
        let scorers: Vec<Box<dyn Scorer<ScoredPostsQuery, PostCandidate>>> =
            vec![phoenix_scorer, ranking_scorer, vm_ranker];
```

| # | Scorer | File | Function |
|---|--------|------|----------|
| 1 | `PhoenixScorer` | `home-mixer/scorers/phoenix_scorer.rs` | Grok transformer → per-action engagement probabilities (`PhoenixScores`) |
| 2 | `RankingScorer` | `home-mixer/scorers/ranking_scorer.rs` | Weighted sum of engagement probs + author diversity decay + OON penalty → `weighted_score`, `score` |
| 3 | `VMRanker` | `home-mixer/scorers/vm_ranker.rs` | Secondary ranking pass via external VM ranker |

#### Candidate model (fields we can extend)

```9:22:home-mixer/models/candidate.rs
pub struct PostCandidate {
    pub tweet_id: u64,
    pub author_id: u64,
    pub tweet_text: String,
    // ...
    pub phoenix_scores: PhoenixScores,
    pub weighted_score: Option<f64>,
    pub score: Option<f64>,
    pub fav_count: Option<i64>,
    pub reply_count: Option<i64>,
    // ...
}
```

`PostCandidate` already carries `tweet_text` and engagement counts — usable for novelty/benefit signals without new hydrators in v1.

---

## Where cluster bias enters

### 1. Retrieval (`phoenix/run_pipeline.py`)

Offline replay demonstrates the production pattern:

```
user_repr = retrieval_model(user_history)
scores = corpus_repr @ user_repr          # dot product over full corpus
top_k = argpartition(scores, -K)
```

- Corpus is pre-computed candidate representations (~embedding neighborhood).
- Novel posts with no historical engagement similarity **never appear in top-K**.
- `PhoenixSource` in production calls the same retrieval service via gRPC (`phoenix_retrieval_client`).

### 2. Ranking (`RankingScorer`)

`compute_weighted_score` sums **only** predicted engagement probabilities:

- favorite, reply, retweet, dwell, vqv, quote, follow_author, negative signals, etc.
- `apply_author_diversity` penalizes repeated authors (cluster reinforcement).
- `effective_oon_weight` down-weights out-of-network posts unless topic/new-user exceptions apply.

**No semantic benefit, novelty, or harm-reduction signal exists in the score path.**

### 3. Content understanding (`grox/`)

Separate task engine (`grox/engine.py`) with classifiers for:
- spam, safety, banger screen, multimodal embeddings, reply ranking

These produce **annotations and embeddings** but are **not consumed** by `PhoenixCandidatePipeline` scorers today. Integration would require a new hydrator calling Grox outputs or a sidecar scorer.

### 4. Feedback loop (cluster formation monitoring)

Existing hooks for measuring funnel effects:

| Component | File | Use for funnel |
|-----------|------|----------------|
| `RerankingKafkaSideEffect` | `side_effects/reranking_kafka_side_effect.rs` | Publishes top-50 scored candidates to Kafka (5% sample) |
| `ScoredStatsSideEffect` | `side_effects/scored_stats_side_effect.rs` | Scoring telemetry |
| `ServedCandidatesKafkaSideEffect` | outer pipeline | What was actually served |
| `ClientEventsKafkaSideEffect` | outer pipeline | User engagement feedback |

---

## Annotated insertion points

```
┌─────────────────────────────────────────────────────────────────┐
│  RETRIEVAL (Sources)                                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐ │
│  │ Thunder      │  │ Phoenix      │  │ [NEW] Exploration    │ │
│  │ (in-network) │  │ (embedding   │  │ Source — low-eng     │ │
│  │              │  │  top-K)      │  │ high-benefit pool    │ │
│  └──────────────┘  └──────────────┘  └──────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  SCORERS (sequential)                                           │
│  ┌──────────────┐  ┌──────────────────────┐  ┌──────────────┐ │
│  │ PhoenixScorer│→ │ [NEW] InventionFunnel│→ │ RankingScorer│ │
│  │ (engagement) │  │ Scorer (novelty +    │  │ (weighted    │ │
│  │              │  │  benefit boost)      │  │  sum)        │ │
│  └──────────────┘  └──────────────────────┘  └──────────────┘ │
│                              ↓                                  │
│                     ┌──────────────┐                              │
│                     │ VMRanker     │                              │
│                     └──────────────┘                              │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  SIDE EFFECTS                                                   │
│  [NEW] ClusterFormationMonitor → Kafka / experiment store       │
└─────────────────────────────────────────────────────────────────┘
```

### `Scorer` trait contract

```7:16:candidate-pipeline/scorer.rs
/// Scorers update candidate fields (like a score field) and run sequentially
pub trait Scorer<Q, C>: Send + Sync {
    fn enable(&self, _query: &Q) -> bool { true }
    async fn score(&self, query: &Q, candidates: &[C]) -> Vec<Result<C, String>>;
    fn update(&self, candidate: &mut C, scored: C);
}
```

New scorers must preserve candidate order, implement `enable()` for feature-flag gating, and populate fields via `update()`.

---

## Core ranking philosophy (approved direction)

**This funnel surfaces ideas upward — not engagement, not content volume.**

Inventors often arrive with zero audience to announce work that may take a year to validate. Penalizing or gating on small-group acceptance defeats the purpose. Early engagement from a handful of users is **not** a primary signal and must **not** be required for boost eligibility.

### The missing axis: epistemic status (not "does Grok believe it?")

**Problem:** LLMs (including Grok) are trained to discount novel claims. Default reasoning pattern:

> "No papers. No scholars. No one else discusses this. User is self-promoting." → reject

For a newly invented idea, **all of that is expected**. Using external validation as a proxy for verifiability systematically punishes the exact population this funnel serves.

**Second problem:** "Verifiable" was framed too narrowly. Many earnest disclosures are verifiable **in principle** but not **by Grok right now**:

- Private repo (can't inspect)
- Patent filed, method described publicly (can't run the binary)
- Specific benchmark methodology stated, results forthcoming
- Technical mechanism explained; prototype not yet built

Grok cannot execute, replicate, or access private artifacts. Tier assignment must be **probability of earnest invention disclosure**, not **probability Grok can confirm it today**.

**What we were missing:** A third dimension separate from novelty and external validation:

| Dimension | Question | NOT the same as |
|-----------|----------|-----------------|
| **Novelty** | Is this non-redundant with existing clusters? | "No one talks about it" (that's expected) |
| **Epistemic status** | What *kind* of claim is this, and how testable is it *in principle*? | "Grok found papers" |
| **Earnestness vs. scam** | Does this read like invention disclosure or exploitation? | "Low engagement" or "self-promotion" |

### Revised stepped decision tree

```
Safety/spam pass? ──no──→ hard block (existing VF path)
      │
     yes
      │
Is this novel (non-redundant)? ──no──→ Tier 0 (baseline ranking)
      │
     yes
      │
What is the epistemic status of the claim?
      │
      ├── External validation exists OR specific falsifiable claim with evidence
      │   (preprint, patent #, numbers+method, reproducible repo link)
      │   ──→ Tier 1: 3× boost
      │
      ├── Verifiable IN PRINCIPLE but not externally validated yet
      │   (concrete mechanism, patent-pending, private repo described,
      │    benchmark protocol stated, proportionate "early stage" framing)
      │   ──→ Tier 1.5: ~2× boost  [NEW — catches patent/private-repo case]
      │
      └── Novel + coherent + earnest, pre-evidence
          (announcement phase, no data yet, inventor acknowledges timeline)
          ──→ Tier 2: 1.5× boost
      │
      └── Vague, unfalsifiable, miraculous, or scam-pattern
          ──→ Tier 0 (no boost; NOT because "no papers exist")
```

### Tier definitions (revised)

| Tier | Epistemic status | Boost | Example |
|------|------------------|-------|---------|
| **Tier 1** | Validated or **directly evidenced** | 3× | "23% cooling reduction; preprint + benchmark link" |
| **Tier 1.5** | **Verifiable in principle**, no public validation yet | 2× | "Patent pending #US2026xxx; method uses X mechanism; private prototype repo; results Q3" |
| **Tier 2** | Novel + **coherent + earnest**, pre-evidence | 1.5× | "New solid-state cooling approach — prototype in 12 months, here's the physics" |
| **Tier 0** | Not novel, OR unfalsifiable/hype/scam-pattern | 0× | Incremental rehash, "cure all disease", urgency + payment link |

Tier 2 and Tier 1.5 are **not blocked** for lacking papers, scholars, or third-party discussion.

### What we explicitly do NOT use as primary signals

- Small-group likes/replies as proof of value
- Author follower count or existing cluster membership
- Engagement velocity in the first hours/days
- "Did experts show up yet?" as a gate (this is a **monitoring** metric for v2 amplification, not a v1 eligibility requirement)
- **Absence of papers, media, scholars, or "others discussing it"** — this is expected for new inventions and must **never** lower tier
- **"Self-promotion"** as a negative signal — inventors announcing their own work is the use case
- **Grok's ability to personally verify** — tier is about claim structure, not model execution

### Two-phase amplification (approved — human-centered)

The funnel has **two distinct phases** with different magnitudes and purposes:

```
PHASE 1 — SURFACE (modest)
  XAI flags post as potential novel idea (Tier 1 / 1.5 / 2)
  → exploration injection + additive/modest boost
  → goal: get in front of the RIGHT people, not masses

PHASE 2 — GLUMP (explosive)
  Right clusters start positively participating
  → 100× to 1000× amplification
  → goal: rapidly expand proven-good ideas to everyone who should see them
```

**Phase 1 is not gated on cluster acceptance.** Inventors have no audience; modest boost + exploration gets the idea to domain-relevant users.

**Phase 2 is triggered BY cluster acceptance** — but only from the **right** clusters (domain expertise, not random likes). Once Grok/XAI detects successful placement ("we put a good idea in front of the right groups and they responded"), the system **rapidly expands** — the "glump."

| Phase | Trigger | Magnitude | Who sees it |
|-------|---------|-----------|-------------|
| **1 — Surface** | Tier assignment (novel + earnest) | Exploration slot + additive boost (+0.15–0.40) | Users whose interests match the idea's domain |
| **2 — Glump** | Right-cluster positive participation confirmed | **100×–1000×** score amplification | Rapid expansion across matched and adjacent clusters |

Phase 1 answers: *"Is this worth showing to the right 500 people?"*  
Phase 2 answers: *"The right people engaged — show this to everyone who should care."*

### What counts as "right cluster positive participation" (Phase 2 trigger)

Not generic engagement. Must be **domain-relevant positive signals**:

| Signal | Weight |
|--------|--------|
| Reply/quote from users with domain topic overlap | High |
| Follow-author from domain-relevant accounts | High |
| Dwell + click from users in matching Grok topic clusters | Medium |
| Expert mutual-follow graph proximity (`mutual_follow_jaccard`) | Medium |
| Random fav from unrelated users | Low — insufficient alone for glump |
| Negative signals (report, block, not_interested) | **Never instant veto** — see Anti-Brigading below |

**Minimum bar for Phase 2:** e.g. ≥3 domain-relevant positive engagements within 48h of Phase 1 boost, with no **confirmed** veto (corroborated negative consensus only).

### Phase 2 magnitude: 100× vs 1000×

These are not follower multipliers. They are **ranking score multipliers** applied after cluster validation:

```
phase_2_score = base_engagement_score × glump_multiplier   // 100 or 1000
```

At 100×–1000×, a validated idea doesn't compete for slot 15 — it **dominates** feed selection for all users in matching and adjacent interest clusters. This is intentional: once the right experts engage, the idea has earned network-wide surfacing.

**Safeguards (required alongside glump):**
- Glump only activates on posts previously flagged Phase 1 (audit trail)
- **No instant veto** — negative signals require corroboration (see Anti-Brigading)
- Rate limit: max N simultaneous glump posts network-wide (prevent feed takeover)
- Decay: glump intensity reduces over 7 days unless sustained expert engagement continues
- Human review queue for first N glump activations per week (calibration period)

### Anti-brigading & false-veto protection (approved direction)

**Problem:** A single downvote, block, or "not interested" can be politically motivated, personal animus, or competitor sabotage — not a signal about the invention's merit. Reddit-style "follow to downvote everything" must not kill the funnel.

**Principle:** Benefit of the doubt for surfacing; high bar for suppression. **More tries before demotion.**

#### Negative signal tiers (never one-strike)

| Signal type | Weight | Alone can kill glump? |
|-------------|--------|---------------------|
| Single block/mute from follower | Very low | **No** |
| Single "not interested" | Very low | **No** |
| Single report | Low | **No** — needs review |
| Cluster of blocks from **unrelated** domain users | Medium | No — triggers review |
| Sustained negative from **domain-relevant** experts | High | **Yes** — after corroboration |
| Coordinated veto burst (same graph, same minute) | Discounted | **No** — likely brigading |
| Safety/harm labels (Grox spam, violence, etc.) | Hard | **Yes** — only hard block |

#### "More tries" model (grace retries)

```
Surface attempt 1 → show to domain-matched users (Phase 1)
    ↓
Mixed signals? → do NOT demote; proceed to attempt 2
    ↓
Surface attempt 2 → expand to adjacent cluster (still modest)
    ↓
Domain experts engage positively? → Phase 2 glump eligible
Domain experts engage negatively (corroborated)? → demote
Only unrelated users negative? → ignore; continue
    ↓
Surface attempt 3 (if still ambiguous) → broader sample before any demotion
```

**Rules:**
- Minimum **2–3 surface attempts** across distinct audience slices before demotion for engagement-based negatives
- A post is never killed by vetoes from users whose interest graph **does not overlap** the post's domain (politics follower downvoting a cooling-tech post = discarded)
- **Chronic downvoter detection:** accounts that downvote >80% of a followed author's posts → zero veto weight for that pair
- **Competitor graph:** accounts in same commercial domain as author posting negative without engaging content → flagged, discounted

#### Veto confirmation bar (before killing glump or revoking Phase 1)

All must be true to confirm a **legitimate negative**:

1. **Domain relevance** — vetoing user has topic/engagement overlap with post subject matter
2. **Corroboration** — ≥N independent domain-relevant negatives (suggest N=5) over ≥24h (not same-minute burst)
3. **Diversity** — vetoing accounts not all in same social cluster (anti-coordination)
4. **No competing positive** — domain expert positive signals ≤ domain expert negative signals
5. **Not chronic animus** — vetoing user fails chronic-downvoter / unrelated-interest tests

Until all five pass: negative signals are **logged but ignored** for demotion. Post keeps its tries.

#### Positive signal immunity window

Once ≥3 domain-relevant **positive** engagements are recorded, post enters a **48h immunity window** where unrelated negative signals cannot demote. Only corroborated domain-expert negative or hard safety block can override.

This protects inventors from "I hate your politics" followers while still allowing real domain experts to say "this doesn't work."

### Score model summary (both phases)

```
if not phase_1_flagged:
    score = engagement_score                          # baseline Phoenix

elif not phase_2_triggered:
    score = engagement_score + tier_additive_boost    # Phase 1: modest surface
    + exploration_source_injection                    # retrieval guarantee

else:  # cluster validated — GLUMP
    score = engagement_score × glump_multiplier       # 100× or 1000×
    + cross_cluster_expansion                         # adjacent topic injection
```

Tier ratios (3× / 2× / 1.5×) apply to **Phase 1 tier assignment quality**, not final reach. Phase 2 magnitude (100× / 1000×) is the human-impact amplifier.

---

## Design options (analysis only)

### Option A — Post-Phoenix boost scorer (recommended v1)

**What:** Add `InventionFunnelScorer` between `PhoenixScorer` and `RankingScorer`.

**Logic:**
1. Classify: novel? → verifiable now? → assign Tier 1 or Tier 2 (Tier 0 = no boost).
2. Score benefit potential (energy/harm criteria) independently of engagement counts.
3. Apply tiered multiplier: `boost = benefit_score × tier_multiplier` (Tier 1 > Tier 2 >> 0).
4. Do **not** require or weight small-group acceptance; low `fav_count`/`reply_count` is expected for inventors.

**Pros:** Minimal pipeline disruption; feature-flaggable; works on existing Phoenix retrieval set; fast to prototype offline.  
**Cons:** Cannot surface posts that retrieval already excluded; boost fights engagement-weighted ranking if multiplier too weak.  
**Risk:** Gaming via keyword stuffing; pseudoscience false positives.

### Option B — Retrieval exploration slot (recommended v2)

**What:** New `ExplorationSource` fetches a small pool of recent low-engagement posts matching benefit criteria (topic-agnostic), merged alongside `PhoenixSource`.

**Logic:**
1. Separate index: recent posts + Grox benefit annotations.
2. Inject N candidates per request (e.g. 5–10) regardless of user embedding similarity.
3. Option A scorer applies tiered boost; cluster monitor (v2 only) amplifies further if expert engagement appears — never required for initial boost.

**Pros:** Addresses root cause (retrieval exclusion); true "funnel" behavior.  
**Cons:** Requires new index/service; higher infra complexity; latency budget pressure.  
**Risk:** Abuse surface expands if exploration pool not filtered.

### Option C — Anti-skeptic hybrid (approved direction)

**Critical design rule:** Use **two separate evaluators**, never one prompt that asks "is this real?"

| Evaluator | Purpose | Default bias |
|-----------|---------|--------------|
| **ScamBlocker** | Hard block harm/exploitation | Skeptical (appropriate here) |
| **InventionSurfacer** | Assign tier for boost | **Generous to earnest novelty** |

Combining them in one LLM call recreates Grok's failure mode ("no papers → discount").

#### ScamBlocker (rules-first, LLM second)

Hard block only on:
- Grox safety/spam classifiers (existing path)
- Scam patterns: miracle cures, urgency + payment, unfalsifiable claims ("free energy forever")
- **NOT** block on: self-promotion, no audience, no citations, no third-party discussion

#### InventionSurfacer (hybrid, inverted defaults)

**Step 1 — Rules extract structural signals** (deterministic, auditable):

| Signal | Points toward |
|--------|---------------|
| Measurable units + described method | Tier 1 |
| Patent #, DOI, arxiv, public repo link | Tier 1 |
| Concrete mechanism + falsifiable prediction | Tier 1.5 |
| "Patent pending" / "private repo" / stated benchmark protocol | Tier 1.5 |
| Technical specificity + proportionate timeline ("prototype in 12mo") | Tier 2 |
| Vague superlatives, no mechanism | Tier 0 |

**Step 2 — LLM assesses novelty + earnestness** with explicit prompt constraints:

```
ABSENCE of papers, media coverage, scholars, or other discussants is EXPECTED
for newly invented ideas. Do NOT lower score for this.

Do NOT penalize self-promotion or low author audience.

Assess ONLY:
- Is the idea non-redundant with known approaches?
- Is the claim specific enough to be wrong (falsifiable)?
- Does tone match earnest disclosure vs. exploitation?
```

**Step 3 — Rules override LLM skepticism:**

- LLM says "no evidence" but rules found patent # or specific mechanism → **floor Tier 1.5**, not Tier 0
- LLM says "skeptical" but rules found coherence + proportionate framing → **floor Tier 2**
- Rules can only **raise** tier floor or **block** scam; LLM cannot **lower** below rule-assigned floor due to missing external validation

| Output | Boost |
|--------|-------|
| Tier 1 — evidenced | 3× |
| Tier 1.5 — verifiable in principle | 2× |
| Tier 2 — earnest pre-evidence | 1.5× |
| Tier 0 — not novel or unfalsifiable hype | 0× |
| ScamBlocker fail | hard block |

**Pros:** Directly counteracts LLM training bias; handles patent/private-repo case; auditable.  
**Cons:** Prompt engineering + rule calibration; edge cases need human review queue.  
**Risk:** Tier 2 gaming — mitigate with specificity requirements (must describe *how*, not just *what*).

---

## Offline prototyping path

`phoenix/run_pipeline.py` supports end-to-end replay without home-mixer:

```bash
python run_pipeline.py \
  --artifacts_dir ./artifacts \
  --sequence_file ./artifacts/example_sequence.json \
  --corpus_file ./artifacts/sports_corpus.npz \
  --top_k_retrieval 200 \
  --top_k_display 30
```

**Recommended v1 prototype:** Fork `run_pipeline.py` → inject synthetic low-engagement high-benefit posts into corpus → add post-ranking boost function → measure rank displacement before touching Rust.

---

## Open risks

| Risk | Mitigation direction |
|------|---------------------|
| Gaming (keyword stuffing) | Hard gates + engagement velocity caps on boosted posts |
| Pseudoscience boost | Require verifiable claims; Grox safety cross-check |
| Feed quality degradation | Cap boosted slots per feed (e.g. max 2/30); decay boost after 24h |
| Latency (LLM scoring) | Pre-compute Grox annotations offline; scorer reads cached labels |
| Cluster false positives | Monitor reply author expertise via `mutual_follow_jaccard` + topic coherence |

---

## Success metrics (proposed)

| Metric | Definition |
|--------|------------|
| Boosted post reach | Impressions on posts that would have ranked below position 50 pre-boost |
| Expert cluster formation | Reply/quote authors with domain overlap within 72h of boost |
| Engagement quality | Dwell + follow_author rate on boosted posts vs. baseline |
| Harm rate | Report/block rate on boosted posts (must not exceed baseline) |
| Funnel conversion | % of boosted posts that sustain organic engagement without boost after 7d |

---

## Files reference map

| Area | Key paths |
|------|-----------|
| Pipeline framework | `candidate-pipeline/candidate_pipeline.rs`, `scorer.rs`, `source.rs` |
| Post ranking pipeline | `home-mixer/candidate_pipeline/phoenix_candidate_pipeline.rs` |
| For You blender | `home-mixer/candidate_pipeline/for_you_candidate_pipeline.rs` |
| Engagement scoring | `home-mixer/scorers/phoenix_scorer.rs`, `ranking_scorer.rs` |
| Retrieval (prod) | `home-mixer/sources/phoenix_source.rs` |
| Retrieval (offline) | `phoenix/run_pipeline.py`, `phoenix/grok.py` |
| In-network | `thunder/thunder_service.rs`, `home-mixer/sources/thunder_source.rs` |
| Content understanding | `grox/engine.py`, `grox/classifiers/content/` |
| Candidate model | `home-mixer/models/candidate.rs` |
| Telemetry | `home-mixer/side_effects/reranking_kafka_side_effect.rs` |

---

## Recommended stack (given stepped philosophy)

**A + C hybrid, phased:**

1. **v1 offline:** Stepped tier classifier in `run_pipeline.py` fork — validate tier assignment on synthetic + real examples
2. **v1 home-mixer:** `InventionFunnelScorer` with tiered multipliers (Option A + C)
3. **v2 retrieval:** `ExplorationSource` (Option B) so Tier 1/2 posts not excluded by embedding neighborhood
4. **v2 amplification:** Cluster monitor increases boost when expert engagement appears — never gates initial boost

## Approved decisions (2026-06-28)

| Decision | Status |
|----------|--------|
| Tier multipliers: 3× / 2× / 1.5× (Tier 1 / 1.5 / 2) | Approved — Phase 1 tier quality only |
| Scoring authority | **Anti-skeptic hybrid** (rules floor + constrained LLM) |
| LLM skepticism handling | Separate ScamBlocker vs InventionSurfacer; rules override missing-external-validation penalty |
| Two-phase amplification | **Approved** — Phase 1 modest surface; Phase 2 glump at 100×–1000× after right-cluster validation |
| Phase 2 trigger | Right-cluster positive participation (not generic engagement, not a Phase 1 gate) |
| Negative signal handling | **No instant veto** — corroborated domain-relevant negatives only; 2–3 grace retries |
| Anti-brigading | Chronic downvoter discount, topic-mismatch ignore, coordinated burst detection |
| Veto confirmation bar | **Approved:** ≥5 domain-relevant negatives over 24h; post floats freely until then |
| Float window | **Approved:** 24h minimum before any engagement-based demotion |
| Glump multiplier | **Approved:** start calibration at **100×** |
| Phase 2 trigger | **Approved:** ≥3 domain-relevant positives within **24h** → glump kicks in immediately; window extends to **48h** max to reach threshold |

## Implementation status

Offline reference prototype: `phoenix/invention_funnel_sim.py` — runnable demo of tier assignment, Phase 1/2 scoring, veto bar, and rank displacement for X team review.

Home-mixer Rust integration (`InventionFunnelScorer`) follows after offline validation.