"""Learner-specific eligibility for recommendation candidates.

R3 owns filtering only.

It consumes the raw, actionable candidates discovered by R2 and decides
which of them are currently allowed for one learner.  R3 deliberately does
not calculate recommendation features, scores, diversification, or rank.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from curriculum.models import SkillPrerequisite
from learning_engine.adaptive_eligibility import (
    resolve_adaptive_eligible_skill_ids,
)
from learning_engine.beginner_path import (
    BeginnerLessonAction,
    resolve_beginner_lesson,
)
from learning_engine.cold_start import LearnerEntryRoute
from learning_engine.learner_entry import resolve_learner_entry_state
from learning_engine.models import SkillMastery
from recommendation.candidates import (
    RecommendationCandidate,
    RecommendationCandidateType,
    generate_recommendation_candidates,
)


class RecommendationEligibilityReason(str, Enum):
    ELIGIBLE = "eligible"
    ONBOARDING_REQUIRED = "onboarding_required"
    DIAGNOSTIC_REQUIRED = "diagnostic_required"
    ACTIVE_ASSESSMENT_MUST_BE_RESUMED = (
        "active_assessment_must_be_resumed"
    )
    BEGINNER_SEQUENCE_ONLY = "beginner_sequence_only"
    ROUTE_MISMATCH = "route_mismatch"
    ADAPTIVE_SCOPE_EMPTY = "adaptive_scope_empty"
    PREREQUISITE_NOT_READY = "prerequisite_not_ready"


class PrerequisiteBlockerReason(str, Enum):
    MASTERY_MISSING = "mastery_missing"
    MASTERY_BELOW_REQUIRED = "mastery_below_required"
    PREREQUISITE_INACTIVE = "prerequisite_inactive"
    GRAPH_CYCLE = "graph_cycle"


@dataclass(frozen=True)
class PrerequisiteBlocker:
    """One prerequisite condition that prevents starting a Lesson."""

    dependent_skill_id: int
    prerequisite_skill_id: int
    required_mastery: Decimal
    actual_mastery: Decimal | None
    reason: PrerequisiteBlockerReason


@dataclass(frozen=True)
class RecommendationEligibilityDecision:
    candidate: RecommendationCandidate
    is_eligible: bool
    reason: RecommendationEligibilityReason
    prerequisite_blockers: tuple[PrerequisiteBlocker, ...] = ()


def _validate_user(user):
    if user.pk is None:
        raise ValueError(
            "User must be saved before recommendation eligibility "
            "can be evaluated."
        )


def _validate_candidates(candidates):
    candidates = tuple(candidates)
    seen_keys = set()

    for candidate in candidates:
        if not isinstance(candidate, RecommendationCandidate):
            raise TypeError(
                "candidates must contain RecommendationCandidate "
                "instances only."
            )

        if candidate.candidate_key in seen_keys:
            raise ValueError(
                "Recommendation candidate keys must be unique."
            )

        seen_keys.add(candidate.candidate_key)

    return candidates


def _decision(
    candidate,
    *,
    eligible,
    reason,
    prerequisite_blockers=(),
):
    return RecommendationEligibilityDecision(
        candidate=candidate,
        is_eligible=eligible,
        reason=reason,
        prerequisite_blockers=tuple(prerequisite_blockers),
    )


def _resolve_prerequisite_blockers(*, user, skill_ids):
    """Return all unmet transitive prerequisite conditions.

    Active graph edges are followed transitively.  Every edge keeps its own
    ``required_mastery`` threshold, so a deep prerequisite may require a
    different mastery score from its parent edge.

    R1 prevents cycles through the supported write paths.  R3 remains
    defensive against legacy/imported rows that bypassed model validation:
    a reachable cycle blocks eligibility instead of recursing forever.
    """

    root_skill_ids = tuple(sorted({int(skill_id) for skill_id in skill_ids}))
    if not root_skill_ids:
        return ()

    edges = list(
        SkillPrerequisite.objects.filter(is_active=True)
        .select_related(
            "prerequisite_skill",
            "prerequisite_skill__topic",
        )
        .order_by(
            "skill_id",
            "prerequisite_skill_id",
            "skill_prerequisite_id",
        )
    )

    adjacency = {}
    for edge in edges:
        adjacency.setdefault(edge.skill_id, []).append(edge)

    reachable_edges = []
    cycle_edges = []
    seen_edge_ids = set()

    def walk(skill_id, path):
        for edge in adjacency.get(skill_id, ()):
            next_skill_id = edge.prerequisite_skill_id

            if next_skill_id in path:
                cycle_edges.append(edge)
                continue

            if edge.pk not in seen_edge_ids:
                seen_edge_ids.add(edge.pk)
                reachable_edges.append(edge)

            walk(
                next_skill_id,
                path | {next_skill_id},
            )

    for root_skill_id in root_skill_ids:
        walk(root_skill_id, {root_skill_id})

    prerequisite_skill_ids = {
        edge.prerequisite_skill_id
        for edge in reachable_edges
    }

    mastery_by_skill_id = {
        row.skill_id: row.mastery_score
        for row in SkillMastery.objects.filter(
            user=user,
            skill_id__in=prerequisite_skill_ids,
        ).only("skill_id", "mastery_score")
    }

    blockers = []

    for edge in reachable_edges:
        prerequisite = edge.prerequisite_skill
        actual_mastery = mastery_by_skill_id.get(
            edge.prerequisite_skill_id
        )

        if (
            not prerequisite.is_active
            or not prerequisite.topic.is_active
        ):
            blocker_reason = (
                PrerequisiteBlockerReason.PREREQUISITE_INACTIVE
            )
        elif actual_mastery is None:
            blocker_reason = PrerequisiteBlockerReason.MASTERY_MISSING
        elif actual_mastery < edge.required_mastery:
            blocker_reason = (
                PrerequisiteBlockerReason.MASTERY_BELOW_REQUIRED
            )
        else:
            continue

        blockers.append(
            PrerequisiteBlocker(
                dependent_skill_id=edge.skill_id,
                prerequisite_skill_id=edge.prerequisite_skill_id,
                required_mastery=edge.required_mastery,
                actual_mastery=actual_mastery,
                reason=blocker_reason,
            )
        )

    for edge in cycle_edges:
        blockers.append(
            PrerequisiteBlocker(
                dependent_skill_id=edge.skill_id,
                prerequisite_skill_id=edge.prerequisite_skill_id,
                required_mastery=edge.required_mastery,
                actual_mastery=mastery_by_skill_id.get(
                    edge.prerequisite_skill_id
                ),
                reason=PrerequisiteBlockerReason.GRAPH_CYCLE,
            )
        )

    # Shared prerequisites can be reached through more than one path.  Keep
    # decisions deterministic and do not emit duplicate blocker records.
    unique = {}
    for blocker in blockers:
        key = (
            blocker.dependent_skill_id,
            blocker.prerequisite_skill_id,
            blocker.reason.value,
            blocker.required_mastery,
        )
        unique[key] = blocker

    return tuple(
        sorted(
            unique.values(),
            key=lambda blocker: (
                blocker.dependent_skill_id,
                blocker.prerequisite_skill_id,
                blocker.reason.value,
                blocker.required_mastery,
            ),
        )
    )


def _resume_candidate_matches_state(candidate, state):
    if (
        candidate.candidate_type
        != RecommendationCandidateType.RESUME_ASSESSMENT
    ):
        return False

    if candidate.assessment_session_id != state.active_session_id:
        return False

    if candidate.assessment_mode != state.active_assessment_mode:
        return False

    return True


def _beginner_lesson_candidate_matches_resolution(
    candidate,
    beginner_resolution,
):
    if candidate.lesson_id != beginner_resolution.lesson_id:
        return False

    if beginner_resolution.action == BeginnerLessonAction.START_LESSON:
        return (
            candidate.candidate_type
            == RecommendationCandidateType.START_LESSON
        )

    if beginner_resolution.action == BeginnerLessonAction.RESUME_LESSON:
        return (
            candidate.candidate_type
            == RecommendationCandidateType.CONTINUE_LESSON
        )

    return False


def _evaluate_with_state(*, user, candidate, state):
    route = state.route

    # Onboarding/diagnostic are authoritative product gates.  Recommendation
    # must not route around them.
    if route == LearnerEntryRoute.ASK_PRIOR_EXPERIENCE:
        return _decision(
            candidate,
            eligible=False,
            reason=RecommendationEligibilityReason.ONBOARDING_REQUIRED,
        )

    if route == LearnerEntryRoute.START_DIAGNOSTIC:
        return _decision(
            candidate,
            eligible=False,
            reason=RecommendationEligibilityReason.DIAGNOSTIC_REQUIRED,
        )

    # An active assessment has strict precedence over every other action.
    if route in {
        LearnerEntryRoute.RESUME_DIAGNOSTIC,
        LearnerEntryRoute.RESUME_ADAPTIVE,
    }:
        if _resume_candidate_matches_state(candidate, state):
            return _decision(
                candidate,
                eligible=True,
                reason=RecommendationEligibilityReason.ELIGIBLE,
            )

        return _decision(
            candidate,
            eligible=False,
            reason=(
                RecommendationEligibilityReason
                .ACTIVE_ASSESSMENT_MUST_BE_RESUMED
            ),
        )

    if route == LearnerEntryRoute.BEGINNER_PATH:
        # Vocabulary review is orthogonal to lesson sequencing and can be
        # surfaced alongside the canonical beginner lesson when it is due.
        if (
            candidate.candidate_type
            == RecommendationCandidateType.VOCABULARY_REVIEW
        ):
            return _decision(
                candidate,
                eligible=True,
                reason=RecommendationEligibilityReason.ELIGIBLE,
            )

        if candidate.candidate_type in {
            RecommendationCandidateType.START_LESSON,
            RecommendationCandidateType.CONTINUE_LESSON,
        }:
            beginner_resolution = resolve_beginner_lesson(user=user)

            if _beginner_lesson_candidate_matches_resolution(
                candidate,
                beginner_resolution,
            ):
                # Beginner progression is intentionally completion-based.
                # Lesson completion is not assessment evidence, so applying
                # mastery prerequisites here would dead-lock true beginners.
                return _decision(
                    candidate,
                    eligible=True,
                    reason=RecommendationEligibilityReason.ELIGIBLE,
                )

        return _decision(
            candidate,
            eligible=False,
            reason=(
                RecommendationEligibilityReason.BEGINNER_SEQUENCE_ONLY
            ),
        )

    if route != LearnerEntryRoute.ADAPTIVE:
        return _decision(
            candidate,
            eligible=False,
            reason=RecommendationEligibilityReason.ROUTE_MISMATCH,
        )

    # -------------------------------------------------------------
    # ADAPTIVE route
    # -------------------------------------------------------------

    if (
        candidate.candidate_type
        == RecommendationCandidateType.RESUME_ASSESSMENT
    ):
        return _decision(
            candidate,
            eligible=False,
            reason=RecommendationEligibilityReason.ROUTE_MISMATCH,
        )

    if (
        candidate.candidate_type
        == RecommendationCandidateType.START_ADAPTIVE_ASSESSMENT
    ):
        eligible_skill_ids = resolve_adaptive_eligible_skill_ids(user=user)

        if not eligible_skill_ids:
            return _decision(
                candidate,
                eligible=False,
                reason=(
                    RecommendationEligibilityReason.ADAPTIVE_SCOPE_EMPTY
                ),
            )

        return _decision(
            candidate,
            eligible=True,
            reason=RecommendationEligibilityReason.ELIGIBLE,
        )

    if (
        candidate.candidate_type
        == RecommendationCandidateType.VOCABULARY_REVIEW
    ):
        return _decision(
            candidate,
            eligible=True,
            reason=RecommendationEligibilityReason.ELIGIBLE,
        )

    if (
        candidate.candidate_type
        == RecommendationCandidateType.CONTINUE_LESSON
    ):
        # Do not lock a learner out of a Lesson already in progress if
        # prerequisite thresholds or curriculum mappings change later.
        return _decision(
            candidate,
            eligible=True,
            reason=RecommendationEligibilityReason.ELIGIBLE,
        )

    if (
        candidate.candidate_type
        == RecommendationCandidateType.START_LESSON
    ):
        blockers = _resolve_prerequisite_blockers(
            user=user,
            skill_ids=candidate.skill_ids,
        )

        if blockers:
            return _decision(
                candidate,
                eligible=False,
                reason=(
                    RecommendationEligibilityReason.PREREQUISITE_NOT_READY
                ),
                prerequisite_blockers=blockers,
            )

        return _decision(
            candidate,
            eligible=True,
            reason=RecommendationEligibilityReason.ELIGIBLE,
        )

    return _decision(
        candidate,
        eligible=False,
        reason=RecommendationEligibilityReason.ROUTE_MISMATCH,
    )


def evaluate_recommendation_eligibility(
    *,
    user,
    candidates=None,
    as_of=None,
):
    """Evaluate raw R2 candidates without scoring or ranking.

    ``as_of`` is forwarded only when R2 candidates must be generated.  It is
    intentionally not used to decay mastery: prerequisite readiness consumes
    the persisted no-decay mastery snapshot from D2 rule_v3.
    """

    _validate_user(user)

    if candidates is None:
        candidates = generate_recommendation_candidates(
            user=user,
            as_of=as_of,
        )

    candidates = _validate_candidates(candidates)
    state = resolve_learner_entry_state(user=user)

    return tuple(
        _evaluate_with_state(
            user=user,
            candidate=candidate,
            state=state,
        )
        for candidate in candidates
    )


def filter_eligible_recommendation_candidates(
    *,
    user,
    candidates=None,
    as_of=None,
):
    """Return only candidates that pass R3 learner eligibility."""

    decisions = evaluate_recommendation_eligibility(
        user=user,
        candidates=candidates,
        as_of=as_of,
    )

    return tuple(
        decision.candidate
        for decision in decisions
        if decision.is_eligible
    )
