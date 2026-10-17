from dataclasses import dataclass
from enum import Enum


class LearnerEntryRoute(str, Enum):
    """
    Canonical entry routes for Privet.
    """

    ASK_PRIOR_EXPERIENCE = (
        "ask_prior_experience"
    )

    BEGINNER_PATH = (
        "beginner_path"
    )

    START_DIAGNOSTIC = (
        "start_diagnostic"
    )

    ADAPTIVE = (
        "adaptive"
    )

    RESUME_DIAGNOSTIC = (
        "resume_diagnostic"
    )

    RESUME_ADAPTIVE = (
        "resume_adaptive"
    )


@dataclass(frozen=True)
class ColdStartRoutingInput:
    """
    Pure learner-entry state.

    active_assessment_mode:
        None
        "adaptive"
        "diagnostic"

    has_finalized_assessment_evidence:
        True when the learner has at least one
        finalized mastery observation.

    prior_russian_experience:
        None  -> onboarding question unanswered
        False -> true beginner
        True  -> learner reports prior knowledge
    """

    active_assessment_mode: str | None

    has_finalized_assessment_evidence: bool

    prior_russian_experience: bool | None


def _validate_optional_bool(
    *,
    value,
    field_name,
):
    if (
        value is not None
        and type(value) is not bool
    ):
        raise ValueError(
            f"{field_name} must be bool or None."
        )


def determine_learner_entry_route(
    *,
    active_assessment_mode=None,
    has_finalized_assessment_evidence=False,
    prior_russian_experience=None,
):
    """
    Determine the learner's next product route.

    Precedence
    ----------
    1. Resume an active assessment.
    2. Existing finalized assessment evidence
       means the learner is no longer cold-start.
    3. If onboarding experience is unknown,
       ask the onboarding question.
    4. Prior Russian experience -> diagnostic.
    5. True beginner -> beginner curriculum path.

    Pure function:
    no database reads or writes.
    """

    _validate_optional_bool(
        value=(
            has_finalized_assessment_evidence
        ),
        field_name=(
            "has_finalized_assessment_evidence"
        ),
    )

    if (
        has_finalized_assessment_evidence
        is None
    ):
        raise ValueError(
            "has_finalized_assessment_evidence "
            "must be bool."
        )

    _validate_optional_bool(
        value=prior_russian_experience,
        field_name=(
            "prior_russian_experience"
        ),
    )

    if active_assessment_mode not in {
        None,
        "adaptive",
        "diagnostic",
    }:
        raise ValueError(
            "active_assessment_mode must be "
            "None, 'adaptive', or 'diagnostic'."
        )

    # =========================================
    # 1. Resume always wins.
    # =========================================

    if (
        active_assessment_mode
        == "diagnostic"
    ):
        return (
            LearnerEntryRoute.RESUME_DIAGNOSTIC
        )

    if (
        active_assessment_mode
        == "adaptive"
    ):
        return (
            LearnerEntryRoute.RESUME_ADAPTIVE
        )

    # =========================================
    # 2. Existing assessment evidence means
    #    cold-start has ended.
    # =========================================

    if has_finalized_assessment_evidence:
        return LearnerEntryRoute.ADAPTIVE

    # =========================================
    # 3. Need onboarding answer.
    # =========================================

    if prior_russian_experience is None:
        return (
            LearnerEntryRoute
            .ASK_PRIOR_EXPERIENCE
        )

    # =========================================
    # 4. Learner already studied Russian.
    # =========================================

    if prior_russian_experience:
        return (
            LearnerEntryRoute.START_DIAGNOSTIC
        )

    # =========================================
    # 5. True beginner.
    # =========================================

    return LearnerEntryRoute.BEGINNER_PATH