from django.urls import path

from learning_engine import views


app_name = "learning_engine"


urlpatterns = [
    # 9E.1 Learner entry
    path(
        "entry/",
        views.learner_entry_view,
        name="learner-entry",
    ),
    # 9E.2 Onboarding
    path(
        "onboarding/prior-experience/",
        views.submit_prior_experience_view,
        name="submit-prior-experience",
    ),
    # 9E.3A Lesson detail
    path(
        "lessons/<int:lesson_id>/",
        views.lesson_detail_view,
        name="lesson-detail",
    ),
    # 9E.3B Start / resume Lesson
    path(
        "lessons/<int:lesson_id>/start/",
        views.start_or_resume_lesson_view,
        name="lesson-start",
    ),
    path(
        "lessons/<int:lesson_id>/progress/",
        views.update_lesson_progress_view,
        name="lesson-progress-update",
    ),

    path(
        "lessons/<int:lesson_id>/complete/",
        views.complete_lesson_view,
        name="lesson-complete",
    ),
    path(
        "assessments/<int:session_id>/",
        views.active_assessment_detail_view,
        name="assessment-detail",
    ),
    path(
        (
            "assessments/<int:session_id>/"
            "questions/<int:session_question_id>/viewed/"
        ),
        views.assessment_question_viewed_view,
        name="assessment-question-viewed",
    ),

    path(
        (
            "assessments/<int:session_id>/"
            "questions/<int:session_question_id>/active-time/"
        ),
        views.assessment_question_active_time_view,
        name="assessment-question-active-time",
    ),
    path(
        (
            "assessments/<int:session_id>/"
            "questions/<int:session_question_id>/answer/"
        ),
        views.assessment_question_answer_view,
        name="assessment-question-answer",
    ),
    path(
        "assessments/<int:session_id>/complete/",
        views.complete_assessment_view,
        name="assessment-complete",
    ),

    path(
        "assessments/<int:session_id>/abandon/",
        views.abandon_assessment_view,
        name="assessment-abandon",
    ),
    path(
        "assessments/<int:session_id>/result/",
        views.assessment_result_view,
        name="assessment-result",
    ),
]