from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from curriculum.models import Lesson, LessonProgress, Skill
from learning_engine.cold_start import LearnerEntryRoute
from learning_engine.learner_entry import resolve_learner_entry_state
from learning_engine.models import AssessmentSession, SkillMastery
from tracking.models import StudySession
from vocabulary.models import VocabularySRSState


@dataclass(frozen=True)
class ProfileSummary:
    route: str
    route_label: str
    prior_experience_label: str
    completed_lessons: int
    in_progress_lessons: int
    total_lessons: int
    completed_assessments: int
    vocabulary_learned: int
    vocabulary_due: int
    mastered_skills: int
    total_skills: int
    streak_count: int
    total_study_minutes: int
    today_study_minutes: int
    daily_goal_minutes: int
    daily_goal_percent: int


_ROUTE_LABELS = {
    LearnerEntryRoute.ASK_PRIOR_EXPERIENCE: "Chưa hoàn tất onboarding",
    LearnerEntryRoute.BEGINNER_PATH: "Lộ trình Beginner",
    LearnerEntryRoute.START_DIAGNOSTIC: "Chờ đánh giá đầu vào",
    LearnerEntryRoute.ADAPTIVE: "Lộ trình Adaptive",
    LearnerEntryRoute.RESUME_DIAGNOSTIC: "Đang đánh giá đầu vào",
    LearnerEntryRoute.RESUME_ADAPTIVE: "Đang làm bài đánh giá thích ứng",
}


def _current_streak(*, user, today):
    recent_start = today - timedelta(days=90)
    study_dates = set(
        StudySession.objects.filter(
            user=user,
            active_seconds__gt=0,
            started_at__date__gte=recent_start,
            started_at__date__lte=today,
        ).values_list("started_at__date", flat=True)
    )

    if today in study_dates:
        cursor = today
    elif today - timedelta(days=1) in study_dates:
        cursor = today - timedelta(days=1)
    else:
        return 0

    count = 0
    while cursor in study_dates:
        count += 1
        cursor -= timedelta(days=1)
    return count


def build_profile_summary(*, user, user_settings, as_of=None):
    if user.pk is None:
        raise ValueError("User must be saved before profile summary can be built.")

    now = as_of or timezone.now()
    if timezone.is_naive(now):
        raise ValueError("as_of must be timezone-aware.")
    today = timezone.localdate(now)

    learner_state = resolve_learner_entry_state(user=user)

    completed_lessons = LessonProgress.objects.filter(
        user=user,
        status=LessonProgress.Status.COMPLETED,
    ).count()
    in_progress_lessons = LessonProgress.objects.filter(
        user=user,
        status=LessonProgress.Status.IN_PROGRESS,
    ).count()
    total_lessons = Lesson.objects.filter(is_active=True).count()

    completed_assessments = AssessmentSession.objects.filter(
        user=user,
        status=AssessmentSession.Status.COMPLETED,
    ).count()

    vocabulary_states = VocabularySRSState.objects.filter(user=user)
    vocabulary_learned = vocabulary_states.count()
    vocabulary_due = vocabulary_states.filter(next_review_at__lte=now).count()

    mastery_threshold = Decimal(str(settings.MASTERY_RULE_V1["mastered_threshold"]))
    mastery_min_observations = int(
        settings.MASTERY_RULE_V1["mastered_min_observations"]
    )
    total_skills = Skill.objects.filter(is_active=True).count()
    mastered_skills = SkillMastery.objects.filter(
        user=user,
        skill__is_active=True,
        observation_count__gte=mastery_min_observations,
        mastery_score__gte=mastery_threshold,
    ).count()

    total_seconds = (
        StudySession.objects.filter(user=user).aggregate(total=Sum("active_seconds"))[
            "total"
        ]
        or 0
    )
    today_seconds = (
        StudySession.objects.filter(
            user=user,
            started_at__date=today,
        ).aggregate(total=Sum("active_seconds"))["total"]
        or 0
    )

    daily_goal_minutes = user_settings.daily_goal_minutes
    today_study_minutes = int(today_seconds // 60)
    daily_goal_percent = min(
        100,
        int((today_seconds / max(daily_goal_minutes * 60, 1)) * 100),
    )

    prior = learner_state.prior_russian_experience
    if prior is None:
        prior_label = "Chưa trả lời"
    elif prior:
        prior_label = "Đã từng học tiếng Nga"
    else:
        prior_label = "Bắt đầu từ đầu"

    return ProfileSummary(
        route=learner_state.route.value,
        route_label=_ROUTE_LABELS[learner_state.route],
        prior_experience_label=prior_label,
        completed_lessons=completed_lessons,
        in_progress_lessons=in_progress_lessons,
        total_lessons=total_lessons,
        completed_assessments=completed_assessments,
        vocabulary_learned=vocabulary_learned,
        vocabulary_due=vocabulary_due,
        mastered_skills=mastered_skills,
        total_skills=total_skills,
        streak_count=_current_streak(user=user, today=today),
        total_study_minutes=int(total_seconds // 60),
        today_study_minutes=today_study_minutes,
        daily_goal_minutes=daily_goal_minutes,
        daily_goal_percent=daily_goal_percent,
    )
