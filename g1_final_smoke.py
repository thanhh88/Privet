from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from curriculum.models import (
    Lesson,
    LessonProgress,
    LessonSkill,
    Skill,
)
from learning_engine.adaptive_eligibility import (
    resolve_adaptive_eligible_skill_ids,
)
from learning_engine.learner_entry import (
    resolve_learner_entry_state,
)
from learning_engine.models import (
    SessionQuestion,
)
from learning_engine.services import (
    start_or_resume_adaptive_assessment,
)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


User = get_user_model()
EMAIL = "g1-final-smoke-v2@example.com"

# Everything is executed in one outer transaction and intentionally rolled
# back at the end, so this smoke test does not leave test data behind.
with transaction.atomic():
    require(
        not User.objects.filter(email=EMAIL).exists(),
        f"Smoke-test user already exists: {EMAIL}",
    )

    user = User.objects.create_user(
        email=EMAIL,
        password="smoke-test-password",
        prior_russian_experience=False,
    )

    # =================================================
    # 1. Canonical beginner curriculum
    # =================================================

    lessons = list(
        Lesson.objects
        .filter(is_active=True)
        .order_by("display_order", "lesson_id")
    )

    mapped_skill_ids = set(
        LessonSkill.objects
        .filter(lesson__in=lessons)
        .values_list("skill_id", flat=True)
    )

    print("active_lessons=", len(lessons))
    print("mapped_distinct_skills=", len(mapped_skill_ids))

    require(
        len(lessons) == 17,
        f"Expected 17 active lessons, got {len(lessons)}",
    )
    require(
        len(mapped_skill_ids) == 30,
        (
            "Expected 30 distinct beginner skills, "
            f"got {len(mapped_skill_ids)}"
        ),
    )

    # =================================================
    # 2. Before completion -> BEGINNER_PATH
    # =================================================

    before = resolve_learner_entry_state(user=user)

    print("route_before_completion=", before.route.value)
    print(
        "evidence_before_completion=",
        before.has_finalized_assessment_evidence,
    )

    require(
        before.route.value == "beginner_path",
        (
            "Expected beginner_path before lesson completion, "
            f"got {before.route.value}"
        ),
    )
    require(
        before.has_finalized_assessment_evidence is False,
        "Lesson learner unexpectedly has assessment evidence.",
    )

    # =================================================
    # 3. Complete all beginner lessons
    # =================================================

    now = timezone.now()

    for lesson in lessons:
        LessonProgress.objects.create(
            user=user,
            lesson=lesson,
            status=LessonProgress.Status.COMPLETED,
            completion_ratio=Decimal("1.0000"),
            completed_at=now,
            last_accessed_at=now,
        )

    completed_count = (
        LessonProgress.objects
        .filter(
            user=user,
            status=LessonProgress.Status.COMPLETED,
        )
        .count()
    )

    print("completed_lessons=", completed_count)

    require(
        completed_count == 17,
        f"Expected 17 completed lessons, got {completed_count}",
    )

    # =================================================
    # 4. After completion -> ADAPTIVE
    # =================================================

    after = resolve_learner_entry_state(user=user)

    print("route_after_completion=", after.route.value)
    print(
        "evidence_after_completion=",
        after.has_finalized_assessment_evidence,
    )

    require(
        after.route.value == "adaptive",
        (
            "Expected adaptive after completing beginner curriculum, "
            f"got {after.route.value}"
        ),
    )
    require(
        after.has_finalized_assessment_evidence is False,
        (
            "Lesson completion must not count as finalized "
            "assessment evidence."
        ),
    )

    # =================================================
    # 5. Beginner adaptive eligibility
    # =================================================

    eligible_ids = resolve_adaptive_eligible_skill_ids(user=user)
    eligible_set = set(eligible_ids)

    eligible_levels = set(
        Skill.objects
        .filter(pk__in=eligible_ids)
        .values_list("cefr_level", flat=True)
    )

    eligible_extensions = (
        Skill.objects
        .filter(
            pk__in=eligible_ids,
            is_extension=True,
        )
        .count()
    )

    print("adaptive_eligible_count=", len(eligible_ids))
    print("adaptive_levels=", sorted(eligible_levels))
    print("adaptive_extensions=", eligible_extensions)

    require(
        len(eligible_ids) == 30,
        (
            "Expected 30 learned Skills in first beginner adaptive "
            f"assessment, got {len(eligible_ids)}"
        ),
    )
    require(
        eligible_set == mapped_skill_ids,
        "Adaptive eligibility does not match completed lesson Skills.",
    )
    require(
        eligible_levels == {"A1"},
        f"Expected only A1 eligibility, got {sorted(eligible_levels)}",
    )
    require(
        eligible_extensions == 0,
        "Extension Skills leaked into beginner adaptive eligibility.",
    )

    # =================================================
    # 6. Actually assemble the first adaptive assessment
    # =================================================

    opened = start_or_resume_adaptive_assessment(user=user)

    print("assessment_action=", opened.action.value)
    print("assessment_session_id=", opened.session_id)
    print("assessment_question_count=", opened.question_count)

    require(
        opened.question_count == 10,
        (
            "Expected a 10-question adaptive assessment, "
            f"got {opened.question_count}"
        ),
    )

    session_questions = list(
        SessionQuestion.objects
        .filter(session_id=opened.session_id)
        .order_by("sequence_number")
    )

    require(
        len(session_questions) == 10,
        (
            "Expected 10 persisted SessionQuestion rows, "
            f"got {len(session_questions)}"
        ),
    )

    # =================================================
    # 7. Frozen snapshots may contain only learned Skills
    # =================================================

    snapshot_skill_ids = set()

    for session_question in session_questions:
        for snapshot in session_question.skill_snapshot or []:
            snapshot_skill_ids.add(int(snapshot["skill_id"]))

    unexpected_snapshot_skill_ids = (
        snapshot_skill_ids - eligible_set
    )

    print("snapshot_skill_count=", len(snapshot_skill_ids))
    print(
        "unexpected_snapshot_skill_ids=",
        sorted(unexpected_snapshot_skill_ids),
    )

    require(
        not unexpected_snapshot_skill_ids,
        (
            "Adaptive assessment contains frozen Skill snapshots "
            "outside the completed beginner curriculum: "
            f"{sorted(unexpected_snapshot_skill_ids)}"
        ),
    )

    print()
    print("G1 FINAL SMOKE PASS")
    print(
        "17 lessons -> 30 learned A1 skills "
        "-> adaptive -> 10 questions"
    )

    # Intentionally remove every write from this smoke test.
    transaction.set_rollback(True)
