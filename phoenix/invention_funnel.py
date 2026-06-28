# Copyright 2026 — Upward Invention Funnel reference implementation (RegularJoe-CEO fork)
# Spec-faithful scoring logic for offline simulation and future home-mixer port.

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class Tier(Enum):
    NONE = 0
    TIER_2 = 2       # novel + earnest, pre-evidence — 1.5× quality
    TIER_1_5 = 15    # verifiable in principle — 2× quality
    TIER_1 = 1       # evidenced — 3× quality


TIER_ADDITIVE_BOOST = {
    Tier.NONE: 0.0,
    Tier.TIER_2: 0.15,
    Tier.TIER_1_5: 0.25,
    Tier.TIER_1: 0.40,
}

TIER_QUALITY_MULTIPLIER = {
    Tier.NONE: 0.0,
    Tier.TIER_2: 1.5,
    Tier.TIER_1_5: 2.0,
    Tier.TIER_1: 3.0,
}

GLUMP_MULTIPLIER = 100.0
PHASE2_MIN_DOMAIN_POSITIVES = 3
PHASE2_WINDOW_HOURS = 48
PHASE2_EARLY_KICKIN_HOURS = 24
VETO_CONFIRM_MIN = 5
FLOAT_HOURS = 24


class EngagementKind(Enum):
    DOMAIN_POSITIVE = "domain_positive"
    DOMAIN_NEGATIVE = "domain_negative"
    UNRELATED_NEGATIVE = "unrelated_negative"
    SAFETY_HARD_BLOCK = "safety_hard_block"
    CHRONIC_ANIMUS = "chronic_animus"
    COORDINATED_BURST = "coordinated_burst"


@dataclass
class EngagementEvent:
    kind: EngagementKind
    hours_after_post: float
    user_cluster_id: str = ""


@dataclass
class InventionPost:
    post_id: str
    tweet_text: str
    tier: Tier
    engagement_score: float
    phase1_flagged: bool = True
    events: List[EngagementEvent] = field(default_factory=list)


@dataclass
class FunnelState:
    phase: int  # 0=baseline, 1=surface, 2=glump, -1=demoted
    final_score: float
    rank_note: str
    glump_active: bool = False
    veto_confirmed: bool = False
    domain_positive_count: int = 0
    counted_negative_count: int = 0


def count_domain_positives(events: List[EngagementEvent], within_hours: float) -> int:
    return sum(
        1
        for e in events
        if e.kind == EngagementKind.DOMAIN_POSITIVE and e.hours_after_post <= within_hours
    )


def count_confirmed_veto_negatives(events: List[EngagementEvent], within_hours: float) -> int:
    """Only domain-relevant negatives count; unrelated/chronic/coordinated ignored."""
    ignored = {
        EngagementKind.UNRELATED_NEGATIVE,
        EngagementKind.CHRONIC_ANIMUS,
        EngagementKind.COORDINATED_BURST,
    }
    clusters = set()
    count = 0
    for e in events:
        if e.hours_after_post > within_hours:
            continue
        if e.kind == EngagementKind.SAFETY_HARD_BLOCK:
            return VETO_CONFIRM_MIN  # instant demotion path
        if e.kind in ignored:
            continue
        if e.kind == EngagementKind.DOMAIN_NEGATIVE:
            if e.user_cluster_id:
                clusters.add(e.user_cluster_id)
            count += 1
    # diversity: need signals from more than one cluster if N>1
    if count >= VETO_CONFIRM_MIN and len(clusters) >= min(3, count):
        return count
    return count if count >= VETO_CONFIRM_MIN else 0


def phase2_triggered(events: List[EngagementEvent]) -> bool:
    """≥3 domain positives within 24h kicks in immediately; up to 48h to qualify."""
    early = count_domain_positives(events, PHASE2_EARLY_KICKIN_HOURS)
    if early >= PHASE2_MIN_DOMAIN_POSITIVES:
        return True
    late = count_domain_positives(events, PHASE2_WINDOW_HOURS)
    return late >= PHASE2_MIN_DOMAIN_POSITIVES


def veto_confirmed(events: List[EngagementEvent], hours_since_post: float) -> bool:
    if hours_since_post < FLOAT_HOURS:
        return False
    for e in events:
        if e.kind == EngagementKind.SAFETY_HARD_BLOCK:
            return True
    return count_confirmed_veto_negatives(events, hours_since_post) >= VETO_CONFIRM_MIN


def score_post(post: InventionPost, hours_since_post: float = 24.0) -> FunnelState:
    if not post.phase1_flagged or post.tier == Tier.NONE:
        return FunnelState(
            phase=0,
            final_score=post.engagement_score,
            rank_note="baseline Phoenix ranking",
        )

    if veto_confirmed(post.events, hours_since_post):
        return FunnelState(
            phase=-1,
            final_score=post.engagement_score * 0.01,
            rank_note=f"demoted — ≥{VETO_CONFIRM_MIN} confirmed domain negatives after {FLOAT_HOURS}h float",
            veto_confirmed=True,
            counted_negative_count=count_confirmed_veto_negatives(post.events, hours_since_post),
        )

    base = post.engagement_score
    additive = TIER_ADDITIVE_BOOST[post.tier]

    if phase2_triggered(post.events):
        glump_score = base * GLUMP_MULTIPLIER
        positives = count_domain_positives(post.events, PHASE2_WINDOW_HOURS)
        return FunnelState(
            phase=2,
            final_score=glump_score,
            rank_note=f"GLUMP {GLUMP_MULTIPLIER:.0f}× — {positives} domain positives (trigger ≤{PHASE2_EARLY_KICKIN_HOURS}h or by {PHASE2_WINDOW_HOURS}h)",
            glump_active=True,
            domain_positive_count=positives,
        )

    phase1_score = base + additive
    return FunnelState(
        phase=1,
        final_score=phase1_score,
        rank_note=f"Phase 1 surface — tier {post.tier.name} +{additive:.2f} additive (floating {FLOAT_HOURS}h)",
        domain_positive_count=count_domain_positives(post.events, hours_since_post),
    )


def rank_posts(posts: List[InventionPost], hours_since_post: float = 24.0) -> List[tuple[int, InventionPost, FunnelState]]:
    scored = [(p, score_post(p, hours_since_post)) for p in posts]
    scored.sort(key=lambda x: x[1].final_score, reverse=True)
    return [(i + 1, p, s) for i, (p, s) in enumerate(scored)]