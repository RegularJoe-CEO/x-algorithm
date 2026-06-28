#!/usr/bin/env python3
"""Upward Invention Funnel — runnable demo for X team review.

Demonstrates Phase 1 surface, Phase 2 glump (100×), veto bar, and rank displacement
without requiring Phoenix model artifacts.

Usage:
    python invention_funnel_sim.py
"""

from invention_funnel import (
    EngagementEvent,
    EngagementKind,
    FunnelState,
    GLUMP_MULTIPLIER,
    InventionPost,
    Tier,
    rank_posts,
)


def demo_scenarios() -> None:
    # Typical engagement scores from Phoenix-weighted ranking (illustrative)
    viral_cluster = 0.62
    mid_engagement = 0.28
    inventor_cold_start = 0.04

    scenarios = [
        (
            "Inventor cold-start (Tier 1.5, no engagement yet)",
            InventionPost(
                post_id="inv-001",
                tweet_text="Patent pending cooling layout — 23% less rack thermal load, prototype Q3.",
                tier=Tier.TIER_1_5,
                engagement_score=inventor_cold_start,
                events=[],
            ),
            6.0,
        ),
        (
            "Same post — 3 domain experts engage at 18h → GLUMP",
            InventionPost(
                post_id="inv-001b",
                tweet_text="Patent pending cooling layout — 23% less rack thermal load, prototype Q3.",
                tier=Tier.TIER_1_5,
                engagement_score=inventor_cold_start,
                events=[
                    EngagementEvent(EngagementKind.DOMAIN_POSITIVE, 8.0, "datacenter_ops"),
                    EngagementEvent(EngagementKind.DOMAIN_POSITIVE, 14.0, "materials_science"),
                    EngagementEvent(EngagementKind.DOMAIN_POSITIVE, 18.0, "hvac_engineering"),
                ],
            ),
            20.0,
        ),
        (
            "Politics follower brigade (ignored during 24h float)",
            InventionPost(
                post_id="inv-002",
                tweet_text="New solid-state cooling approach for edge data centers — earnest disclosure.",
                tier=Tier.TIER_2,
                engagement_score=inventor_cold_start,
                events=[
                    EngagementEvent(EngagementKind.UNRELATED_NEGATIVE, 1.0, "politics_a"),
                    EngagementEvent(EngagementKind.CHRONIC_ANIMUS, 2.0, "politics_a"),
                    EngagementEvent(EngagementKind.UNRELATED_NEGATIVE, 3.0, "politics_b"),
                ],
            ),
            12.0,
        ),
        (
            "Confirmed veto after float — 5 domain experts negative",
            InventionPost(
                post_id="inv-003",
                tweet_text="Perpetual motion data center — zero energy compute.",
                tier=Tier.TIER_2,
                engagement_score=0.06,
                events=[
                    EngagementEvent(EngagementKind.DOMAIN_NEGATIVE, 26.0, "physics_a"),
                    EngagementEvent(EngagementKind.DOMAIN_NEGATIVE, 28.0, "physics_b"),
                    EngagementEvent(EngagementKind.DOMAIN_NEGATIVE, 30.0, "thermo_c"),
                    EngagementEvent(EngagementKind.DOMAIN_NEGATIVE, 32.0, "eng_d"),
                    EngagementEvent(EngagementKind.DOMAIN_NEGATIVE, 34.0, "eng_e"),
                ],
            ),
            36.0,
        ),
    ]

    print("=" * 100)
    print("UPWARD INVENTION FUNNEL — REFERENCE SIMULATION")
    print(f"Glump: {GLUMP_MULTIPLIER:.0f}× | Phase 2: ≥3 domain positives (24h kick-in, 48h window)")
    print(f"Veto: ≥5 domain negatives after 24h float | Unrelated negatives ignored")
    print("=" * 100)

    for title, post, hours in scenarios:
        state = rank_posts([post], hours)[0][2]
        print(f"\n### {title}")
        print(f"  Hours since post: {hours}")
        print(f"  Baseline engagement score: {post.engagement_score:.4f}")
        print(f"  Tier: {post.tier.name}")
        print(f"  → Final score: {state.final_score:.4f}")
        print(f"  → Phase: {state.phase} | {state.rank_note}")

    # Rank displacement: inventor vs viral cluster post
    print("\n" + "=" * 100)
    print("RANK DISPLACEMENT — inventor with GLUMP vs normal viral post")
    print("=" * 100)

    pool = [
        InventionPost("viral-1", "Celebrity cluster drama", Tier.NONE, 0.58, phase1_flagged=False),
        InventionPost("viral-2", "Incremental tech rehash", Tier.NONE, 0.45, phase1_flagged=False),
        InventionPost("mid-1", "Solid mid engagement post", Tier.NONE, 0.32, phase1_flagged=False),
        InventionPost(
            "inventor-glump",
            "Novel energy invention — domain validated",
            Tier.TIER_1,
            inventor_cold_start,
            events=[
                EngagementEvent(EngagementKind.DOMAIN_POSITIVE, 6.0, "energy_a"),
                EngagementEvent(EngagementKind.DOMAIN_POSITIVE, 10.0, "energy_b"),
                EngagementEvent(EngagementKind.DOMAIN_POSITIVE, 15.0, "energy_c"),
            ],
        ),
        InventionPost(
            "inventor-phase1",
            "Novel invention — not yet validated by cluster",
            Tier.TIER_1_5,
            inventor_cold_start,
            events=[],
        ),
    ]

    print(f"\n{'Rank':<6} {'Score':<12} {'Phase':<8} {'Post'}")
    print("-" * 80)
    for rank, post, state in rank_posts(pool, hours_since_post=20.0):
        label = post.post_id
        print(f"{rank:<6} {state.final_score:<12.4f} {state.phase:<8} {label} — {state.rank_note[:50]}")

    print("\n" + "=" * 100)
    print("KEY TAKEAWAY")
    print("=" * 100)
    print(
        f"  Cold-start inventor (score {inventor_cold_start}) loses to viral (0.58) in Phase 1.\n"
        f"  Same inventor with 3 domain positives → score {inventor_cold_start * GLUMP_MULTIPLIER:.2f} — "
        f"dominates feed.\n"
        f"  Production requires: exploration source (retrieval) + InventionFunnelScorer (home-mixer)."
    )


if __name__ == "__main__":
    demo_scenarios()