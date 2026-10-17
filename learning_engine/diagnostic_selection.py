from dataclasses import dataclass
from decimal import Decimal

from curriculum.models import Skill
from learning_engine.diagnostic_blueprint import (
    DIAGNOSTIC_BLUEPRINT_V1,
    DIAGNOSTIC_TARGET_QUESTION_COUNT,
    validate_diagnostic_blueprint,
)
from learning_engine.models import (
    AssessmentSession,
    SessionQuestion,
)
from learning_engine.question_selection import (
    QuestionCandidate,
    rank_question_candidates,
)
from questionbank.models import QuestionSkill


@dataclass(frozen=True)
class DiagnosticQuestionPlanSlot:
    """
    One fully resolved diagnostic assessment slot.

    focus Skill comes from the frozen diagnostic
    blueprint.

    Question selection is deterministic and this
    object does not write anything to the database.
    """

    slot_number: int
    round_number: int
    anchor_order: int

    skill_id: int
    skill_code: str
    cefr_level: str

    target_difficulty: Decimal

    question_id: int
    source_id: str

    question_difficulty: Decimal
    difficulty_distance: Decimal

    was_seen_before: bool


def _load_diagnostic_session(
    *,
    user,
    session_id,
):
    if user.pk is None:
        raise ValueError(
            "User must be saved before diagnostic "
            "Questions can be planned."
        )

    try:
        session = AssessmentSession.objects.get(
            pk=session_id,
            user=user,
        )
    except AssessmentSession.DoesNotExist as exc:
        raise ValueError(
            "Assessment session does not exist "
            "or does not belong to this user."
        ) from exc

    if session.status != AssessmentSession.Status.ACTIVE:
        raise ValueError(
            "Diagnostic Question planning requires "
            "an active assessment session."
        )

    if session.mode != AssessmentSession.Mode.DIAGNOSTIC:
        raise ValueError(
            "Diagnostic Question planning requires "
            "diagnostic mode."
        )

    # Initial diagnostic is assembled as one
    # deterministic 15-question unit.
    #
    # Resume must reload existing SessionQuestions,
    # not plan another diagnostic.
    if session.session_questions.exists():
        raise ValueError(
            "Diagnostic Question planning requires "
            "an empty assessment session."
        )

    return session


def plan_diagnostic_questions(
    *,
    user,
    session_id,
    blueprint=DIAGNOSTIC_BLUEPRINT_V1,
):
    """
    Resolve the frozen diagnostic blueprint into
    exactly 15 unique Questions.

    Selection policy
    ----------------
    - session: ACTIVE + DIAGNOSTIC + empty;
    - Skill must exist, be active, and match the
      CEFR level frozen in the blueprint;
    - Question must be active;
    - selected Skill must be the Question's
      PRIMARY Skill;
    - historically unseen Questions are preferred;
    - then nearest target difficulty;
    - then source_id ASC;
    - then question_id ASC;
    - Question cannot appear twice in the same
      diagnostic plan.

    No database writes occur here.
    """

    blueprint = validate_diagnostic_blueprint(
        blueprint
    )

    session = _load_diagnostic_session(
        user=user,
        session_id=session_id,
    )

    # -----------------------------------------
    # Resolve the five frozen anchor Skills.
    # -----------------------------------------

    anchor_codes = tuple(
        dict.fromkeys(
            slot.skill_code
            for slot in blueprint
        )
    )

    skills = list(
        Skill.objects.filter(
            skill_code__in=anchor_codes,
            is_active=True,
        )
    )

    skills_by_code = {
        skill.skill_code: skill
        for skill in skills
    }

    if len(
        skills_by_code
    ) != len(
        anchor_codes
    ):
        missing_codes = [
            code
            for code in anchor_codes
            if code not in skills_by_code
        ]

        raise ValueError(
            "Diagnostic anchor Skill does not "
            "exist or is inactive: "
            + ", ".join(missing_codes)
        )

    # Blueprint CEFR must also agree with the
    # current curriculum taxonomy.
    for slot in blueprint:
        skill = skills_by_code[
            slot.skill_code
        ]

        if skill.cefr_level != slot.cefr_level:
            raise ValueError(
                f"Diagnostic anchor "
                f"{slot.skill_code} has CEFR "
                f"{skill.cefr_level!r} in the "
                f"curriculum but "
                f"{slot.cefr_level!r} in the "
                f"blueprint."
            )

    # -----------------------------------------
    # Load all PRIMARY Question mappings once.
    # -----------------------------------------

    mappings = list(
        QuestionSkill.objects
        .filter(
            skill_id__in=[
                skill.pk
                for skill in skills
            ],
            is_primary=True,
            question__is_active=True,
        )
        .select_related(
            "question",
            "skill",
        )
        .order_by(
            "skill__skill_code",
            "question__source_id",
            "question_id",
        )
    )

    all_candidate_question_ids = [
        mapping.question_id
        for mapping in mappings
    ]

    # -----------------------------------------
    # Historical exposure.
    #
    # "Seen" means actual view, not merely having
    # once been selected into another assessment.
    # -----------------------------------------

    previously_seen_question_ids = set(
        SessionQuestion.objects
        .filter(
            session__user=user,
            question_id__in=(
                all_candidate_question_ids
            ),
            first_viewed_at__isnull=False,
        )
        .exclude(
            session=session,
        )
        .values_list(
            "question_id",
            flat=True,
        )
        .distinct()
    )

    candidates_by_skill_id = {
        skill.pk: []
        for skill in skills
    }

    for mapping in mappings:
        candidates_by_skill_id[
            mapping.skill_id
        ].append(
            QuestionCandidate(
                question_id=(
                    mapping.question_id
                ),
                source_id=(
                    mapping.question.source_id
                ),
                difficulty_score=(
                    mapping.question.difficulty_score
                ),
                # Eligibility already requires
                # primary mapping.
                is_primary=True,
                was_seen_before=(
                    mapping.question_id
                    in previously_seen_question_ids
                ),
            )
        )

    # -----------------------------------------
    # Resolve blueprint slots.
    # -----------------------------------------

    used_question_ids = set()

    plan = []

    for blueprint_slot in blueprint:
        skill = skills_by_code[
            blueprint_slot.skill_code
        ]

        available_candidates = [
            candidate
            for candidate in (
                candidates_by_skill_id[
                    skill.pk
                ]
            )
            if (
                candidate.question_id
                not in used_question_ids
            )
        ]

        if not available_candidates:
            raise ValueError(
                f"No eligible primary Question "
                f"remains for diagnostic slot "
                f"{blueprint_slot.slot_number} "
                f"({blueprint_slot.skill_code})."
            )

        ranked = rank_question_candidates(
            candidates=available_candidates,
            target_difficulty=(
                blueprint_slot.target_difficulty
            ),
        )

        if not ranked:
            raise ValueError(
                f"Diagnostic slot "
                f"{blueprint_slot.slot_number} "
                f"could not select a Question."
            )

        selected = ranked[0]

        used_question_ids.add(
            selected.question_id
        )

        plan.append(
            DiagnosticQuestionPlanSlot(
                slot_number=(
                    blueprint_slot.slot_number
                ),
                round_number=(
                    blueprint_slot.round_number
                ),
                anchor_order=(
                    blueprint_slot.anchor_order
                ),

                skill_id=skill.pk,
                skill_code=skill.skill_code,
                cefr_level=skill.cefr_level,

                target_difficulty=(
                    blueprint_slot.target_difficulty
                ),

                question_id=(
                    selected.question_id
                ),
                source_id=(
                    selected.source_id
                ),

                question_difficulty=(
                    selected.difficulty_score
                ),
                difficulty_distance=(
                    selected.difficulty_distance
                ),

                was_seen_before=(
                    selected.was_seen_before
                ),
            )
        )

    # -----------------------------------------
    # Defensive final invariants
    # -----------------------------------------

    if len(
        plan
    ) != DIAGNOSTIC_TARGET_QUESTION_COUNT:
        raise ValueError(
            "Diagnostic plan must contain "
            "exactly 15 Questions."
        )

    if len(
        used_question_ids
    ) != DIAGNOSTIC_TARGET_QUESTION_COUNT:
        raise ValueError(
            "Diagnostic plan must contain "
            "15 unique Questions."
        )

    return tuple(
        plan
    )