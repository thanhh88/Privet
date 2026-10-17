from dataclasses import dataclass
from enum import Enum

from django.db.models import Exists, OuterRef

from curriculum.models import (
    Lesson,
    LessonProgress,
    LessonSkill,
    Skill,
)


class BeginnerLessonAction(str, Enum):
    START_LESSON = "start_lesson"
    RESUME_LESSON = "resume_lesson"
    NO_AVAILABLE_LESSON = "no_available_lesson"


class BeginnerLessonUnavailableReason(str, Enum):
    NO_ELIGIBLE_LESSONS = "no_eligible_lessons"
    ALL_COMPLETED = "all_completed"


@dataclass(frozen=True)
class BeginnerLessonResolution:
    action: BeginnerLessonAction

    lesson_id: int | None
    lesson_title: str | None
    lesson_display_order: int | None

    lesson_progress_id: int | None

    unavailable_reason: (
        BeginnerLessonUnavailableReason | None
    )


def _eligible_beginner_lessons():
    """
    Return the canonical core-A1 Lesson queryset.

    A beginner Lesson must:
    - be active;
    - have at least one LessonSkill mapping;
    - have no mapped Skill outside active,
      non-extension A1 curriculum.

    This intentionally rejects mixed A1/A2 Lessons.
    """

    all_links = LessonSkill.objects.filter(
        lesson_id=OuterRef("pk"),
    )

    valid_links = all_links.filter(
        skill__is_active=True,
        skill__cefr_level=Skill.CefrLevel.A1,
        skill__is_extension=False,
    )

    invalid_links = all_links.exclude(
        skill__is_active=True,
        skill__cefr_level=Skill.CefrLevel.A1,
        skill__is_extension=False,
    )

    return (
        Lesson.objects
        .filter(
            is_active=True,
        )
        .annotate(
            _has_skill_mapping=Exists(
                all_links
            ),
            _has_valid_skill=Exists(
                valid_links
            ),
            _has_invalid_skill=Exists(
                invalid_links
            ),
        )
        .filter(
            _has_skill_mapping=True,
            _has_valid_skill=True,
            _has_invalid_skill=False,
        )
    )


def resolve_beginner_lesson(
    *,
    user,
):
    """
    Resolve the exact Lesson a true beginner
    should open next.

    Selection policy
    ----------------
    1. Resume the most recently accessed eligible
       IN_PROGRESS Lesson.
    2. Otherwise choose the earliest eligible
       Lesson not already COMPLETED.
    3. Return an explicit unavailable state when
       there is no suitable Lesson.

    This function performs no database writes.
    """

    if user.pk is None:
        raise ValueError(
            "User must be saved before beginner "
            "Lesson resolution."
        )

    # =========================================
    # Canonical eligible curriculum
    # =========================================

    eligible_lessons = (
        _eligible_beginner_lessons()
    )

    # =========================================
    # 1. Resume existing progress first.
    #
    # last_accessed_at DESC means the learner
    # returns to the Lesson they used most
    # recently.
    #
    # Remaining fields make ties deterministic.
    # =========================================

    progress = (
        LessonProgress.objects
        .filter(
            user=user,
            status=(
                LessonProgress.Status.IN_PROGRESS
            ),
            lesson_id__in=(
                eligible_lessons.values(
                    "lesson_id"
                )
            ),
        )
        .select_related(
            "lesson"
        )
        .order_by(
            "-last_accessed_at",
            "lesson__display_order",
            "lesson__lesson_id",
        )
        .first()
    )

    if progress is not None:
        lesson = progress.lesson

        return BeginnerLessonResolution(
            action=(
                BeginnerLessonAction.RESUME_LESSON
            ),

            lesson_id=lesson.pk,
            lesson_title=lesson.title,
            lesson_display_order=(
                lesson.display_order
            ),

            lesson_progress_id=progress.pk,

            unavailable_reason=None,
        )

    # =========================================
    # 2. No resumable Lesson.
    #
    # Detect whether beginner curriculum exists
    # at all before checking completion.
    # =========================================

    if not eligible_lessons.exists():
        return BeginnerLessonResolution(
            action=(
                BeginnerLessonAction
                .NO_AVAILABLE_LESSON
            ),

            lesson_id=None,
            lesson_title=None,
            lesson_display_order=None,

            lesson_progress_id=None,

            unavailable_reason=(
                BeginnerLessonUnavailableReason
                .NO_ELIGIBLE_LESSONS
            ),
        )

    completed_lesson_ids = (
        LessonProgress.objects
        .filter(
            user=user,
            status=(
                LessonProgress.Status.COMPLETED
            ),
            lesson_id__in=(
                eligible_lessons.values(
                    "lesson_id"
                )
            ),
        )
        .values_list(
            "lesson_id",
            flat=True,
        )
    )

    next_lesson = (
        eligible_lessons
        .exclude(
            lesson_id__in=completed_lesson_ids
        )
        .order_by(
            "display_order",
            "lesson_id",
        )
        .first()
    )

    # =========================================
    # 3. Entire eligible beginner curriculum
    #    has been completed.
    # =========================================

    if next_lesson is None:
        return BeginnerLessonResolution(
            action=(
                BeginnerLessonAction
                .NO_AVAILABLE_LESSON
            ),

            lesson_id=None,
            lesson_title=None,
            lesson_display_order=None,

            lesson_progress_id=None,

            unavailable_reason=(
                BeginnerLessonUnavailableReason
                .ALL_COMPLETED
            ),
        )

    # =========================================
    # 4. Start earliest unfinished Lesson.
    #
    # Do not create LessonProgress here.
    # Opening/starting the Lesson will own that
    # write operation later.
    # =========================================

    return BeginnerLessonResolution(
        action=(
            BeginnerLessonAction.START_LESSON
        ),

        lesson_id=next_lesson.pk,
        lesson_title=next_lesson.title,
        lesson_display_order=(
            next_lesson.display_order
        ),

        lesson_progress_id=None,

        unavailable_reason=None,
    )