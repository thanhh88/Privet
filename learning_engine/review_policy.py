from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from django.conf import settings
from learning_engine.mastery import (
    ensure_skill_mastery_current,
)
from learning_engine.priority import (
    build_user_skill_priority,
)
from django.utils import timezone

@dataclass(frozen=True)
class ReviewSkillCandidate:
    skill_id: int
    skill_code: str

    mastery_estimate: Decimal
    observation_count: int

    last_review_evidence_at: datetime | None


def build_review_due_pool(
    *,
    candidates,
    as_of,
):
    """
    Return review-due Skills in deterministic order.

    A Skill is review-due when:
    - observation_count >= min_observations;
    - mastery_estimate >= mastered_threshold;
    - qualifying review evidence exists;
    - review age >= review_after_days.

    Caller is responsible for providing only Skills
    that are eligible for the learner.
    """

    config = settings.MASTERY_RULE_V1

    min_observations = int(
        config["min_observations"]
    )

    mastered_threshold = Decimal(
        str(config["mastered_threshold"])
    )

    review_after_days = int(
        config["review_after_days"]
    )

    review_cutoff = (
        as_of
        - timedelta(
            days=review_after_days
        )
    )

    due = []

    for candidate in candidates:
        if (
            candidate.observation_count
            < min_observations
        ):
            continue

        if (
            candidate.mastery_estimate
            < mastered_threshold
        ):
            continue

        if candidate.last_review_evidence_at is None:
            continue

        if (
            candidate.last_review_evidence_at
            > review_cutoff
        ):
            continue

        due.append(
            candidate
        )

    return sorted(
        due,
        key=lambda candidate: (
            candidate.last_review_evidence_at,
            candidate.mastery_estimate,
            candidate.skill_code,
            candidate.skill_id,
        ),
    )

def build_review_backlog_metrics(
    *,
    due_pool,
    as_of,
):
    """
    Calculate observability metrics from an already
    canonical D2 review-due pool.

    Eligibility is intentionally NOT recalculated here.
    The caller must supply the output of
    build_review_due_pool() or build_user_review_due_pool().
    """

    if not isinstance(
        as_of,
        datetime,
    ):
        raise ValueError(
            "as_of must be a datetime."
        )

    due_pool = tuple(
        due_pool
    )

    if not due_pool:
        return ReviewBacklogMetrics(
            backlog_size=0,
            oldest_review_age=None,
        )

    oldest_review_evidence_at = None

    for candidate in due_pool:
        if not isinstance(
            candidate,
            ReviewSkillCandidate,
        ):
            raise ValueError(
                "Each due-pool item must be a "
                "ReviewSkillCandidate."
            )

        review_evidence_at = (
            candidate.last_review_evidence_at
        )

        if review_evidence_at is None:
            raise ValueError(
                "A review-due candidate must have "
                "last_review_evidence_at."
            )

        if review_evidence_at > as_of:
            raise ValueError(
                "last_review_evidence_at must not "
                "be later than as_of."
            )

        if (
            oldest_review_evidence_at is None
            or review_evidence_at
            < oldest_review_evidence_at
        ):
            oldest_review_evidence_at = (
                review_evidence_at
            )

    return ReviewBacklogMetrics(
        backlog_size=len(
            due_pool
        ),
        oldest_review_age=(
            as_of
            - oldest_review_evidence_at
        ),
    )

def determine_review_slot_count(
    *,
    pool_size,
):
    """
    Return the number of Skill slots reserved for
    review under Review Policy V1 — P2-8.

    Policy:
    - pool == 0 -> 0 review slots
    - pool 1..threshold-1 -> 1 review slot
    - pool >= threshold -> 2 review slots

    The result must never exceed pool_size.
    """

    if (
        isinstance(pool_size, bool)
        or not isinstance(pool_size, int)
    ):
        raise ValueError(
            "Review pool_size must be an integer."
        )

    if pool_size < 0:
        raise ValueError(
            "Review pool_size must not be negative."
        )

    if pool_size == 0:
        return 0

    config = settings.MASTERY_RULE_V1

    backlog_threshold = int(
        config["review_backlog_threshold"]
    )

    if backlog_threshold < 1:
        raise ValueError(
            "Review backlog threshold must be "
            "at least 1."
        )

    if pool_size >= backlog_threshold:
        return min(
            2,
            pool_size,
        )

    return 1

def build_user_review_due_pool(
    *,
    user,
    eligible_skill_ids,
    as_of,
):
    """
    Build the deterministic review-due pool for one user.

    Only Skill IDs already eligible for this learner are
    considered here. Review eligibility itself is delegated
    to build_review_due_pool().
    """

    candidates = []

    for skill_id in eligible_skill_ids:
        state = ensure_skill_mastery_current(
            user=user,
            skill_id=skill_id,
            as_of=as_of,
        )

        priority = build_user_skill_priority(
            user=user,
            skill_id=skill_id,
            as_of=as_of,
        )

        candidates.append(
            ReviewSkillCandidate(
                skill_id=skill_id,
                skill_code=priority.skill_code,
                mastery_estimate=(
                    priority.mastery_estimate
                ),
                observation_count=(
                    priority.observation_count
                ),
                last_review_evidence_at=(
                    state.last_review_evidence_at
                ),
            )
        )

    return build_review_due_pool(
        candidates=candidates,
        as_of=as_of,
    )

def build_user_review_backlog_metrics(
    *,
    user,
    eligible_skill_ids,
    as_of=None,
):
    """
    Build current D2 review-backlog metrics for one user.

    Canonical review eligibility remains owned by
    build_user_review_due_pool().
    """

    if as_of is None:
        as_of = timezone.now()

    due_pool = build_user_review_due_pool(
        user=user,
        eligible_skill_ids=eligible_skill_ids,
        as_of=as_of,
    )

    return build_review_backlog_metrics(
        due_pool=due_pool,
        as_of=as_of,
    )

@dataclass(frozen=True)
class ReviewBacklogMetrics:
    """
    Current canonical D2 review-backlog metrics.

    backlog_size:
        Number of Skills currently review-due.

    oldest_review_age:
        Elapsed time since the oldest
        last_review_evidence_at among due Skills.

        None when the due pool is empty.
    """

    backlog_size: int
    oldest_review_age: timedelta | None

