from dataclasses import dataclass

from learning_engine.cold_start import (
    LearnerEntryRoute,
    determine_learner_entry_route,
)
from learning_engine.models import (
    AssessmentSession,
    SessionQuestion,
)
from django.contrib.auth import get_user_model

@dataclass(frozen=True)
class LearnerEntryState:
    """
    Authoritative learner-entry state resolved
    from persisted assessment data plus the
    onboarding experience answer.

    active_session_id:
        Present only when an active assessment
        must be resumed.

    prior_russian_experience:
        None  -> onboarding question unanswered
        False -> true beginner
        True  -> learner reports prior study
    """

    route: LearnerEntryRoute

    active_session_id: int | None

    active_assessment_mode: str | None

    has_finalized_assessment_evidence: bool

    prior_russian_experience: bool | None

def resolve_learner_entry_state(
    *,
    user,
):
    """
    Resolve the learner's current product entry
    state from authoritative persisted data.

    Database policy
    ---------------
    1. prior_russian_experience is read from the
       persisted User row, not supplied by caller.
    2. At most one ACTIVE assessment may be
       resolved for entry.
    3. ACTIVE assessment wins over every other
       route and must be resumed.
    4. Finalized evidence means at least one
       actually viewed SessionQuestion from a
       COMPLETED adaptive/diagnostic assessment.
    5. ABANDONED assessments do not count as
       mastery evidence.
    6. Selected-but-never-viewed Questions do not
       count as evidence.
    7. A viewed unanswered Question in a COMPLETED
       assessment does count as evidence.

    This function performs no database writes.
    """

    if user.pk is None:
        raise ValueError(
            "User must be saved before learner "
            "entry state can be resolved."
        )

    # =========================================
    # Persisted onboarding state
    # =========================================
    #
    # Read directly from the database rather than
    # trusting a possibly stale / modified in-memory
    # User instance.

    UserModel = get_user_model()

    try:
        prior_russian_experience = (
            UserModel.objects
            .values_list(
                "prior_russian_experience",
                flat=True,
            )
            .get(
                pk=user.pk,
            )
        )

    except UserModel.DoesNotExist as exc:
        raise ValueError(
            "User does not exist."
        ) from exc

    assessment_modes = (
        AssessmentSession.Mode.ADAPTIVE,
        AssessmentSession.Mode.DIAGNOSTIC,
    )

    # =========================================
    # Active assessment
    # =========================================

    active_sessions = list(
        AssessmentSession.objects
        .filter(
            user=user,
            status=AssessmentSession.Status.ACTIVE,
            mode__in=assessment_modes,
        )
        .order_by(
            "started_at",
            "session_id",
        )
        .values(
            "session_id",
            "mode",
        )[:2]
    )

    # More than one active assessment makes the
    # resume route ambiguous. Do not silently pick
    # one and hide inconsistent persisted state.
    if len(active_sessions) > 1:
        raise ValueError(
            "Learner has more than one active "
            "assessment session."
        )

    if active_sessions:
        active_session_id = (
            active_sessions[0][
                "session_id"
            ]
        )

        active_assessment_mode = (
            active_sessions[0][
                "mode"
            ]
        )

    else:
        active_session_id = None
        active_assessment_mode = None

    # =========================================
    # Finalized assessment evidence
    # =========================================

    has_finalized_assessment_evidence = (
        SessionQuestion.objects.filter(
            session__user=user,
            session__status=(
                AssessmentSession.Status.COMPLETED
            ),
            session__mode__in=assessment_modes,
            first_viewed_at__isnull=False,
        )
        .exists()
    )

    # =========================================
    # Pure routing policy
    # =========================================

    route = determine_learner_entry_route(
        active_assessment_mode=(
            active_assessment_mode
        ),
        has_finalized_assessment_evidence=(
            has_finalized_assessment_evidence
        ),
        prior_russian_experience=(
            prior_russian_experience
        ),
    )

    return LearnerEntryState(
        route=route,

        active_session_id=(
            active_session_id
        ),

        active_assessment_mode=(
            active_assessment_mode
        ),

        has_finalized_assessment_evidence=(
            has_finalized_assessment_evidence
        ),

        prior_russian_experience=(
            prior_russian_experience
        ),
    )