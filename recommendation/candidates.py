"""Raw recommendation-candidate generation.

R2 owns candidate discovery only.

It intentionally does not decide whether a candidate is pedagogically
eligible for a specific learner, does not calculate recommendation
features, and does not rank candidates. Those responsibilities belong to
later recommendation phases.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from django.db.models import Exists, OuterRef
from django.utils import timezone

from curriculum.models import Lesson, LessonProgress, LessonSkill
from learning_engine.models import AssessmentSession
from vocabulary.models import VocabularySRSState


class RecommendationCandidateType(str, Enum):
    RESUME_ASSESSMENT = "resume_assessment"
    CONTINUE_LESSON = "continue_lesson"
    START_LESSON = "start_lesson"
    START_ADAPTIVE_ASSESSMENT = "start_adaptive_assessment"
    VOCABULARY_REVIEW = "vocabulary_review"


@dataclass(frozen=True)
class RecommendationCandidate:
    """One raw, actionable recommendation candidate.

    ``candidate_key`` is a stable identity used for deterministic tests and
    later recommendation logging. No score or rank belongs to R2.
    """

    candidate_key: str
    candidate_type: RecommendationCandidateType

    lesson_id: int | None = None
    lesson_progress_id: int | None = None
    lesson_title: str | None = None
    lesson_type: str | None = None
    lesson_display_order: int | None = None

    assessment_session_id: int | None = None
    assessment_mode: str | None = None

    skill_ids: tuple[int, ...] = ()

    vocabulary_due_count: int = 0
    vocabulary_oldest_due_at: datetime | None = None


def _validate_user(user):
    if user.pk is None:
        raise ValueError(
            "User must be saved before recommendation candidates "
            "can be generated."
        )


def _validate_as_of(as_of):
    if as_of is None:
        return timezone.now()

    if timezone.is_naive(as_of):
        raise ValueError("as_of must be timezone-aware.")

    return as_of


def _structurally_actionable_lessons():
    """Return active Lessons whose mapped Skills are all active.

    R2 applies structural validity only. Learner-specific prerequisite
    readiness is deliberately deferred to R3.
    """

    all_links = LessonSkill.objects.filter(
        lesson_id=OuterRef("pk"),
    )

    active_links = all_links.filter(
        skill__is_active=True,
    )

    inactive_links = all_links.filter(
        skill__is_active=False,
    )

    return (
        Lesson.objects
        .filter(is_active=True)
        .annotate(
            _has_skill_mapping=Exists(all_links),
            _has_active_skill=Exists(active_links),
            _has_inactive_skill=Exists(inactive_links),
        )
        .filter(
            _has_skill_mapping=True,
            _has_active_skill=True,
            _has_inactive_skill=False,
        )
        .prefetch_related("lesson_skills__skill")
        .order_by("display_order", "lesson_id")
    )


def _lesson_candidates(*, user):
    lessons = list(_structurally_actionable_lessons())

    if not lessons:
        return []

    lesson_ids = [lesson.pk for lesson in lessons]

    progress_by_lesson_id = {
        progress.lesson_id: progress
        for progress in (
            LessonProgress.objects
            .filter(
                user=user,
                lesson_id__in=lesson_ids,
            )
            .order_by("lesson_id")
        )
    }

    candidates = []

    for lesson in lessons:
        progress = progress_by_lesson_id.get(lesson.pk)

        if (
            progress is not None
            and progress.status == LessonProgress.Status.COMPLETED
        ):
            continue

        skill_ids = tuple(
            sorted(
                link.skill_id
                for link in lesson.lesson_skills.all()
                if link.skill.is_active
            )
        )

        if (
            progress is not None
            and progress.status == LessonProgress.Status.IN_PROGRESS
        ):
            candidate_type = (
                RecommendationCandidateType.CONTINUE_LESSON
            )
            candidate_key = f"lesson:continue:{lesson.pk}"
            lesson_progress_id = progress.pk

        else:
            candidate_type = RecommendationCandidateType.START_LESSON
            candidate_key = f"lesson:start:{lesson.pk}"
            lesson_progress_id = None

        candidates.append(
            RecommendationCandidate(
                candidate_key=candidate_key,
                candidate_type=candidate_type,
                lesson_id=lesson.pk,
                lesson_progress_id=lesson_progress_id,
                lesson_title=lesson.title,
                lesson_type=lesson.lesson_type,
                lesson_display_order=lesson.display_order,
                skill_ids=skill_ids,
            )
        )

    return candidates


def _assessment_candidates(*, user):
    candidates = []

    active = (
        AssessmentSession.objects
        .filter(
            user=user,
            status=AssessmentSession.Status.ACTIVE,
            mode__in=(
                AssessmentSession.Mode.ADAPTIVE,
                AssessmentSession.Mode.DIAGNOSTIC,
            ),
        )
        .order_by("started_at", "session_id")
        .first()
    )

    if active is not None:
        candidates.append(
            RecommendationCandidate(
                candidate_key=f"assessment:resume:{active.pk}",
                candidate_type=(
                    RecommendationCandidateType.RESUME_ASSESSMENT
                ),
                assessment_session_id=active.pk,
                assessment_mode=active.mode,
            )
        )

    # R2 deliberately generates the raw start candidate even when another
    # assessment is active or the learner is not yet eligible. R3 will own
    # learner-specific eligibility filtering.
    candidates.append(
        RecommendationCandidate(
            candidate_key="assessment:start:adaptive",
            candidate_type=(
                RecommendationCandidateType.START_ADAPTIVE_ASSESSMENT
            ),
            assessment_mode=AssessmentSession.Mode.ADAPTIVE,
        )
    )

    return candidates


def _vocabulary_candidates(*, user, as_of):
    due = (
        VocabularySRSState.objects
        .filter(
            user=user,
            item__is_active=True,
            item__topic_links__topic__is_active=True,
            next_review_at__lte=as_of,
        )
        .distinct()
        .order_by("next_review_at", "pk")
    )

    due_count = due.count()

    if due_count == 0:
        return []

    oldest = due.first()

    return [
        RecommendationCandidate(
            candidate_key="vocabulary:review",
            candidate_type=(
                RecommendationCandidateType.VOCABULARY_REVIEW
            ),
            vocabulary_due_count=due_count,
            vocabulary_oldest_due_at=oldest.next_review_at,
        )
    ]


def generate_recommendation_candidates(*, user, as_of=None):
    """Generate all raw recommendation candidates for one learner.

    R2 guarantees:
    - candidates correspond to implemented product actions;
    - completed Lessons are not offered as new/continue actions;
    - structurally invalid Lessons are excluded;
    - due vocabulary is aggregated into one actionable review candidate;
    - active assessments can be resumed;
    - the function performs no writes;
    - output ordering is deterministic and is *not* a recommendation rank.

    R2 does *not* apply prerequisite readiness, learner-entry routing,
    mastery-based eligibility, feature calculation, scoring, diversification,
    or ranking.
    """

    _validate_user(user)
    as_of = _validate_as_of(as_of)

    candidates = []
    candidates.extend(_lesson_candidates(user=user))
    candidates.extend(_assessment_candidates(user=user))
    candidates.extend(
        _vocabulary_candidates(
            user=user,
            as_of=as_of,
        )
    )

    return tuple(
        sorted(
            candidates,
            key=lambda candidate: candidate.candidate_key,
        )
    )
