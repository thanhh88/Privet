from dataclasses import dataclass
from datetime import (
    datetime,
    timedelta,
)
from decimal import (
    Decimal,
    localcontext,
)

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone

from curriculum.models import Skill
from learning_engine.models import (
    AssessmentSession,
    QuestionAttempt,
    SessionQuestion,
    SkillMastery,
)
from tracking.models import StudyEvent

@dataclass(frozen=True)
class SkillEvidence:
    session_question_id: int
    session_id: int
    sequence_number: int

    question_id: int
    source_id: str

    skill_id: int
    skill_code: str
    is_primary: bool
    skill_weight: Decimal

    difficulty_score: Decimal

    is_correct: bool
    was_unanswered: bool

    active_response_time_ms: int

    evidence_at: datetime

    final_attempt_id: int | None
    final_attempt_number: int | None


@dataclass(frozen=True)
class MasteryCalculation:
    """
    Complete rule-based learner-state calculation
    for one User x Skill.
    """

    mastery_estimate: Decimal
    mastery_score: Decimal | None

    observation_count: int
    correct_attempts: int

    recent_accuracy: Decimal | None
    historical_accuracy: Decimal | None

    consecutive_errors: int
    avg_response_time_ms: Decimal | None

    last_evidence_at: datetime | None
    calculated_at: datetime

    algorithm_version: str


@dataclass(frozen=True)
class SkillMasteryUpdateResult:
    mastery_id: int

    user_id: int
    skill_id: int

    # Internal M-hat. Always available.
    mastery_estimate: Decimal

    # Persisted score. NULL during cold start.
    mastery_score: Decimal | None

    observation_count: int
    correct_attempts: int

    recent_accuracy: Decimal | None
    historical_accuracy: Decimal | None

    consecutive_errors: int
    avg_response_time_ms: Decimal | None

    last_evidence_at: datetime | None
    calculated_at: datetime

    algorithm_version: str

    created: bool

@dataclass(frozen=True)
class SkillLearningRecencyUpdateResult:
    mastery_id: int
    user_id: int
    skill_id: int
    last_learning_activity_at: datetime | None
    created: bool

def collect_skill_evidence(
    *,
    user,
    skill_id=None,
):
    """
    Return finalized mastery evidence for a user.

    Evidence policy
    ---------------
    1. Only COMPLETED assessment sessions count.
    2. Only ADAPTIVE and DIAGNOSTIC sessions count.
    3. Never-viewed SessionQuestions do not count.
    4. If a viewed question has attempts, only the
       final attempt counts.
    5. If a viewed question has no attempts, it is
       treated as an incorrect observation.
    6. A Question contributes separately to every
       snapshotted Skill mapping according to its
       snapshotted weight.
    7. Difficulty and Skill mappings are read from
       SessionQuestion historical snapshots, never
       from the current Question Bank.
    8. evidence_at is the assessment completion time,
       not the individual answer timestamp.

    No mastery score is calculated here.
    This function only extracts normalized evidence.
    """

    attempts_queryset = (
        QuestionAttempt.objects
        .order_by(
            "attempt_number"
        )
    )

    session_questions = list(
        SessionQuestion.objects
        .filter(
            session__user=user,
            session__status=(
                AssessmentSession.Status.COMPLETED
            ),
            session__mode__in=[
                AssessmentSession.Mode.ADAPTIVE,
                AssessmentSession.Mode.DIAGNOSTIC,
            ],
            first_viewed_at__isnull=False,
        )
        .select_related(
            "session",
            "question",
        )
        .prefetch_related(
            Prefetch(
                "attempts",
                queryset=attempts_queryset,
                to_attr="_evidence_attempts",
            )
        )
        .order_by(
            "session__ended_at",
            "session_id",
            "sequence_number",
        )
    )

    if not session_questions:
        return []

    evidence_rows = []

    for session_question in session_questions:
        question = session_question.question

        evidence_at = (
            session_question.session.ended_at
        )

        if evidence_at is None:
            raise RuntimeError(
                "Completed assessment session "
                "must have ended_at."
            )

        if (
            session_question.difficulty_score_snapshot
            is None
        ):
            raise RuntimeError(
                "SessionQuestion is missing "
                "difficulty_score_snapshot."
            )

        difficulty_score = Decimal(
            str(
                session_question
                .difficulty_score_snapshot
            )
        )

        skill_snapshot = (
            session_question.skill_snapshot
        )

        if (
            not isinstance(skill_snapshot, list)
            or not skill_snapshot
        ):
            raise RuntimeError(
                "SessionQuestion must contain a "
                "non-empty skill_snapshot."
            )

        attempts = (
            session_question._evidence_attempts
        )

        if attempts:
            final_attempt = attempts[-1]

            is_correct = (
                final_attempt.is_correct
            )

            was_unanswered = False

            final_attempt_id = (
                final_attempt.pk
            )

            final_attempt_number = (
                final_attempt.attempt_number
            )

        else:
            # The learner saw the assessment item
            # but completed the test without answering.
            #
            # This is valid negative evidence.
            is_correct = False
            was_unanswered = True

            final_attempt_id = None
            final_attempt_number = None

        for skill_data in skill_snapshot:
            if not isinstance(
                skill_data,
                dict,
            ):
                raise RuntimeError(
                    "Each skill_snapshot item "
                    "must be an object."
                )

            try:
                snapshot_skill_id = (
                    skill_data["skill_id"]
                )
                snapshot_skill_code = (
                    skill_data["skill_code"]
                )
                snapshot_is_primary = (
                    skill_data["is_primary"]
                )
                snapshot_weight_raw = (
                    skill_data["weight"]
                )
            except KeyError as exc:
                raise RuntimeError(
                    "skill_snapshot item is missing "
                    f"required field {exc.args[0]!r}."
                ) from exc

            if (
                isinstance(
                    snapshot_skill_id,
                    bool,
                )
                or not isinstance(
                    snapshot_skill_id,
                    int,
                )
                or snapshot_skill_id < 1
            ):
                raise RuntimeError(
                    "skill_snapshot skill_id must "
                    "be a positive integer."
                )

            if (
                not isinstance(
                    snapshot_skill_code,
                    str,
                )
                or not snapshot_skill_code.strip()
            ):
                raise RuntimeError(
                    "skill_snapshot skill_code must "
                    "be a non-blank string."
                )

            if not isinstance(
                snapshot_is_primary,
                bool,
            ):
                raise RuntimeError(
                    "skill_snapshot is_primary must "
                    "be a boolean."
                )

            try:
                snapshot_weight = Decimal(
                    str(
                        snapshot_weight_raw
                    )
                )
            except Exception as exc:
                raise RuntimeError(
                    "skill_snapshot weight must "
                    "be a valid decimal value."
                ) from exc

            if not snapshot_weight.is_finite():
                raise RuntimeError(
                    "skill_snapshot weight must "
                    "be finite."
                )

            # Filtering must use historical snapshot
            # data, not current QuestionSkill rows.
            if (
                skill_id is not None
                and snapshot_skill_id != skill_id
            ):
                continue

            evidence_rows.append(
                SkillEvidence(
                    sequence_number=(
                        session_question.sequence_number
                    ),

                    is_primary=(
                        snapshot_is_primary
                    ),

                    session_question_id=(
                        session_question.pk
                    ),

                    session_id=(
                        session_question.session_id
                    ),

                    question_id=question.pk,
                    source_id=question.source_id,

                    skill_id=(
                        snapshot_skill_id
                    ),

                    skill_code=(
                        snapshot_skill_code
                    ),

                    skill_weight=(
                        snapshot_weight
                    ),

                    difficulty_score=(
                        difficulty_score
                    ),

                    is_correct=is_correct,

                    was_unanswered=(
                        was_unanswered
                    ),

                    active_response_time_ms=(
                        session_question
                        .active_response_time_ms
                    ),

                    evidence_at=evidence_at,

                    final_attempt_id=(
                        final_attempt_id
                    ),

                    final_attempt_number=(
                        final_attempt_number
                    ),
                )
            )

    return evidence_rows


_SCORE_QUANTUM = Decimal("0.00001")
_RESPONSE_TIME_QUANTUM = Decimal("0.01")
_SECONDS_PER_DAY = Decimal("86400")
_MICROSECONDS_PER_SECOND = Decimal("1000000")


def _weighted_accuracy(evidence):
    """
    Weighted correctness mean using
    the snapshotted Skill weight.

    Returns None when there is no evidence.
    """

    evidence = list(evidence)

    if not evidence:
        return None

    numerator = Decimal("0")
    denominator = Decimal("0")

    for row in evidence:
        weight = Decimal(
            str(row.skill_weight)
        )

        y = (
            Decimal("1")
            if row.is_correct
            else Decimal("0")
        )

        numerator += (
            weight
            * y
        )

        denominator += weight

    if denominator <= 0:
        raise ValueError(
            "Weighted accuracy requires "
            "positive total skill weight."
        )

    return (
        numerator
        / denominator
    ).quantize(
        _SCORE_QUANTUM
    )

def _average_response_time(evidence):
    """
    Mean active response time across valid viewed
    assessment observations.

    All SkillEvidence rows are viewed observations
    by construction.
    """

    evidence = list(evidence)

    if not evidence:
        return None

    total = Decimal("0")

    for row in evidence:
        value = row.active_response_time_ms

        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
        ):
            raise ValueError(
                "active_response_time_ms must be "
                "a non-negative integer."
            )

        total += Decimal(value)

    return (
        total
        / Decimal(len(evidence))
    ).quantize(
        _RESPONSE_TIME_QUANTUM
    )

def _count_consecutive_errors(evidence):
    """
    Count trailing qualifying incorrect evidence.

    Only evidence where:
        - the Skill is primary, OR
        - snapshotted Skill weight >= 0.5

    contributes to the streak.

    Low-weight secondary evidence is ignored.
    """

    count = 0

    for row in reversed(
        evidence
    ):
        weight = Decimal(
            str(row.skill_weight)
        )

        qualifies = (
            row.is_primary
            or weight >= Decimal("0.5")
        )

        if not qualifies:
            continue

        if row.is_correct:
            break

        count += 1

    return count


def _timedelta_days(delta):
    """
    Convert datetime.timedelta to Decimal days.

    Decimal arithmetic is used so that the mastery
    calculation does not depend on binary float
    rounding.
    """

    total_seconds = (
        Decimal(
            delta.days * 86400
            + delta.seconds
        )
        +
        (
            Decimal(
                delta.microseconds
            )
            / _MICROSECONDS_PER_SECOND
        )
    )

    return (
        total_seconds
        / _SECONDS_PER_DAY
    )


def calculate_mastery(
    evidence,
    *,
    as_of=None,
):
    """
    Calculate mastery and learner-state metrics from
    SkillEvidence rows.

    Historical mastery
    ------------------
    Evidence is evaluated at the newest evidence time:

        M_last =
            (alpha * m0 + SUM(a_i * y_i))
            /
            (alpha + SUM(a_i))

    where:

        a_i =
            w_i
            * c_i
            * 2 ^ (-delta_i / H)

        delta_i =
            last_evidence_at - evidence_at_i

        c_i =
            1
            + beta
            * (d_i - 0.5)
            * (2*y_i - 1)

    Personalized inactivity decay
    -----------------------------
    If M_last > m0:

        M_current =
            m0
            + (M_last - m0)
            * 2 ^ (-staleness / H)

    where:

        staleness =
            as_of - last_evidence_at

    If M_last <= m0:

        M_current = M_last

    Therefore inactivity can reduce previously
    demonstrated mastery above the prior, but can
    never improve a weak learner merely because no
    new evidence was observed.

    Additional learner-state metrics
    --------------------------------
    - observation_count:
        raw number of valid observations

    - correct_attempts:
        raw number of correct observations

    - historical_accuracy:
        weighted accuracy across all observations

    - recent_accuracy:
        weighted accuracy across the 10 most
        recent observations

    - consecutive_errors:
        trailing incorrect observations where
        the Skill is primary or weight >= 0.5

    The function is pure:
    - no database writes;
    - the caller's evidence collection is not mutated;
    - internal mastery_estimate always exists;
    - mastery_score is NULL while observation_count
      is below the configured minimum.
    """

    # Copy so sorting below does not mutate
    # the caller's collection.
    evidence = list(evidence)

    if as_of is None:
        as_of = timezone.now()

    config = settings.MASTERY_RULE_V1

    algorithm_version = str(
        config["algorithm_version"]
    )

    alpha = Decimal(
        str(
            config["alpha"]
        )
    )

    prior_mean = Decimal(
        str(
            config["prior_mean"]
        )
    )

    beta = Decimal(
        str(
            config["beta"]
        )
    )

    half_life_days = Decimal(
        str(
            config["half_life_days"]
        )
    )

    min_observations = int(
        config["min_observations"]
    )

    # -----------------------------------------
    # Configuration validation
    # -----------------------------------------

    if alpha <= 0:
        raise ValueError(
            "Mastery alpha must be positive."
        )

    if not (
        Decimal("0")
        <= prior_mean
        <= Decimal("1")
    ):
        raise ValueError(
            "Mastery prior_mean must be "
            "between 0 and 1."
        )

    if beta < 0:
        raise ValueError(
            "Mastery beta must not be negative."
        )

    if half_life_days <= 0:
        raise ValueError(
            "Mastery half_life_days "
            "must be positive."
        )

    if min_observations < 1:
        raise ValueError(
            "Mastery min_observations "
            "must be at least 1."
        )

    # -----------------------------------------
    # Evidence validation
    # -----------------------------------------

    skill_ids = {
        row.skill_id
        for row in evidence
    }

    # Empty evidence is valid cold start.
    # Non-empty evidence must represent only
    # one User x Skill calculation.
    if len(skill_ids) > 1:
        raise ValueError(
            "calculate_mastery() requires "
            "evidence for exactly one skill."
        )

    for row in evidence:
        if row.evidence_at is None:
            raise ValueError(
                "Skill evidence must have "
                "evidence_at."
            )

        if row.sequence_number < 1:
            raise ValueError(
                "Skill evidence sequence_number "
                "must be positive."
            )

    # Canonical chronology:
    #
    # assessment completion time
    # -> deterministic session tie-break
    # -> question order inside assessment
    evidence.sort(
        key=lambda row: (
            row.evidence_at,
            row.session_id,
            row.sequence_number,
        )
    )

    # -----------------------------------------
    # Historical anchor
    # -----------------------------------------
    #
    # Evidence history is evaluated at the moment
    # of the newest valid evidence.
    #
    # Inactivity after that moment is handled
    # separately below. This prevents weak learners
    # from drifting upward toward the prior merely
    # because time passes.

    last_evidence_at = None

    if evidence:
        last_evidence_at = (
            evidence[-1].evidence_at
        )

        if last_evidence_at > as_of:
            raise ValueError(
                "Newest evidence timestamp must not "
                "be later than as_of."
            )

    # -----------------------------------------
    # Mastery calculation
    # -----------------------------------------

    with localcontext() as context:
        # Keep substantially more precision than
        # the five decimal places stored in the DB.
        context.prec = 40

        numerator = (
            alpha
            * prior_mean
        )

        denominator = alpha

        for row in evidence:
            weight = Decimal(
                str(
                    row.skill_weight
                )
            )

            difficulty = Decimal(
                str(
                    row.difficulty_score
                )
            )

            if not (
                Decimal("0")
                < weight
                <= Decimal("1")
            ):
                raise ValueError(
                    "Skill evidence weight must "
                    "be in (0, 1]."
                )

            if not (
                Decimal("0")
                <= difficulty
                <= Decimal("1")
            ):
                raise ValueError(
                    "Difficulty score must be "
                    "between 0 and 1."
                )

            # Historical evidence ages only relative
            # to the newest evidence event.
            #
            # It must not continue to age every time
            # learner state is merely recalculated.
            historical_anchor = (
                last_evidence_at
                if last_evidence_at is not None
                else as_of
            )

            age = (
                historical_anchor
                - row.evidence_at
            )

            if age < timedelta(0):
                raise ValueError(
                    "Evidence timestamp must not "
                    "be later than the historical "
                    "anchor."
                )

            age_days = (
                _timedelta_days(
                    age
                )
            )

            y = (
                Decimal("1")
                if row.is_correct
                else Decimal("0")
            )

            # ---------------------------------
            # Difficulty adjustment
            #
            # c_i =
            # 1 + beta
            #     * (d_i - 0.5)
            #     * (2*y_i - 1)
            # ---------------------------------

            difficulty_factor = (
                Decimal("1")
                +
                beta
                * (
                    difficulty
                    - Decimal("0.5")
                )
                * (
                    Decimal("2")
                    * y
                    - Decimal("1")
                )
            )

            if difficulty_factor <= 0:
                raise ValueError(
                    "Difficulty adjustment "
                    "must remain positive."
                )

            # ---------------------------------
            # Historical time decay
            #
            # decay =
            # 2 ^ (-delta_days / H)
            #
            # delta_days is measured from this
            # evidence row to last_evidence_at.
            # ---------------------------------

            exponent = -(
                age_days
                / half_life_days
            )

            decay_factor = (
                (
                    Decimal("2").ln()
                    * exponent
                )
                .exp()
            )

            # ---------------------------------
            # Effective historical evidence weight
            #
            # a_i =
            # w_i * c_i * decay_i
            # ---------------------------------

            effective_weight = (
                weight
                * difficulty_factor
                * decay_factor
            )

            numerator += (
                effective_weight
                * y
            )

            denominator += (
                effective_weight
            )

        # IMPORTANT:
        # Everything from here onward is outside
        # the evidence loop. This also makes cold
        # start (evidence == []) valid.
        mastery_at_last_evidence = (
            numerator
            / denominator
        )

        # -----------------------------------------
        # Personalized inactivity decay
        # -----------------------------------------
        #
        # The learner's latest observed mastery is
        # the anchor.
        #
        # Knowledge above the prior may decay toward
        # the prior during inactivity.
        #
        # Knowledge at or below the prior never
        # improves merely because time passes.

        mastery_estimate = (
            mastery_at_last_evidence
        )

        if (
            last_evidence_at is not None
            and as_of > last_evidence_at
            and mastery_at_last_evidence
            > prior_mean
        ):
            inactivity_age = (
                as_of
                - last_evidence_at
            )

            inactivity_days = (
                _timedelta_days(
                    inactivity_age
                )
            )

            inactivity_exponent = -(
                inactivity_days
                / half_life_days
            )

            inactivity_decay = (
                (
                    Decimal("2").ln()
                    * inactivity_exponent
                )
                .exp()
            )

            mastery_estimate = (
                prior_mean
                +
                (
                    mastery_at_last_evidence
                    - prior_mean
                )
                * inactivity_decay
            )

        mastery_score = None

        if (
            len(evidence)
            >= min_observations
        ):
            mastery_score = (
                mastery_estimate.quantize(
                    _SCORE_QUANTUM
                )
            )

    # -----------------------------------------
    # Learner-state metrics
    # -----------------------------------------

    observation_count = len(
        evidence
    )

    correct_attempts = sum(
        1
        for row in evidence
        if row.is_correct
    )

    historical_accuracy = (
        _weighted_accuracy(
            evidence
        )
    )

    recent_accuracy = (
        _weighted_accuracy(
            evidence[-10:]
        )
    )

    consecutive_errors = (
        _count_consecutive_errors(
            evidence
        )
    )

    avg_response_time_ms = (
        _average_response_time(
            evidence
        )
    )

    return MasteryCalculation(
        mastery_estimate=(
            mastery_estimate
        ),

        mastery_score=(
            mastery_score
        ),

        observation_count=(
            observation_count
        ),

        correct_attempts=(
            correct_attempts
        ),

        recent_accuracy=(
            recent_accuracy
        ),

        historical_accuracy=(
            historical_accuracy
        ),

        consecutive_errors=(
            consecutive_errors
        ),

        avg_response_time_ms=(
            avg_response_time_ms
        ),

        last_evidence_at=(
            last_evidence_at
        ),

        calculated_at=as_of,

        algorithm_version=(
            algorithm_version
        ),
    )


@transaction.atomic
def recalculate_learning_recency(
    *,
    user,
    skill_ids,
):
    """
    Rebuild last_learning_activity_at for User x Skill
    from authoritative StudyEvent history.

    Only:
    - lesson_completed
    - practice_completed

    contribute to learning recency.

    No assessment-derived mastery metrics are changed.
    """

    if user.pk is None:
        raise ValueError(
            "User must be saved before "
            "learning recency can be recalculated."
        )

    normalized_skill_ids = sorted(
        set(skill_ids)
    )

    if not normalized_skill_ids:
        return []

    User = get_user_model()

    try:
        locked_user = (
            User.objects
            .select_for_update()
            .get(
                pk=user.pk
            )
        )
    except User.DoesNotExist as exc:
        raise ValueError(
            "User does not exist."
        ) from exc

    skills_by_id = Skill.objects.in_bulk(
        normalized_skill_ids
    )

    missing_skill_ids = [
        skill_id
        for skill_id in normalized_skill_ids
        if skill_id not in skills_by_id
    ]

    if missing_skill_ids:
        raise ValueError(
            "Unknown skill IDs: "
            + ", ".join(
                str(skill_id)
                for skill_id in missing_skill_ids
            )
        )

    results = []

    for skill_id in normalized_skill_ids:
        latest_event = (
            StudyEvent.objects
            .filter(
                user=locked_user,
                event_type__in=[
                    StudyEvent.EventType.LESSON_COMPLETED,
                    StudyEvent.EventType.PRACTICE_COMPLETED,
                ],
                lesson__lesson_skills__skill_id=skill_id,
            )
            .order_by(
                "-occurred_at",
                "-study_event_id",
            )
            .first()
        )

        last_learning_activity_at = (
            latest_event.occurred_at
            if latest_event is not None
            else None
        )

        try:
            state = (
                SkillMastery.objects
                .select_for_update()
                .get(
                    user=locked_user,
                    skill_id=skill_id,
                )
            )

            created = False

        except SkillMastery.DoesNotExist:
            state = SkillMastery(
                user=locked_user,
                skill=skills_by_id[skill_id],
            )

            created = True

        # Intentionally change ONLY learning recency.
        state.last_learning_activity_at = (
            last_learning_activity_at
        )

        state.full_clean()

        if created:
            state.save()
        else:
            state.save(
                update_fields=[
                    "last_learning_activity_at",
                ]
            )

        results.append(
            SkillLearningRecencyUpdateResult(
                mastery_id=state.pk,
                user_id=locked_user.pk,
                skill_id=skill_id,
                last_learning_activity_at=(
                    state.last_learning_activity_at
                ),
                created=created,
            )
        )

    return results

def _recalculate_skill_mastery_for_locked_user(
    *,
    user,
    skill,
    as_of,
):
    """
    Rebuild and persist one User x Skill mastery state.

    Caller must already hold the user row lock.

    The value is always rebuilt from finalized
    assessment history. No incremental accumulation
    is performed here.
    """

    evidence = collect_skill_evidence(
        user=user,
        skill_id=skill.pk,
    )

    calculation = calculate_mastery(
        evidence,
        as_of=as_of,
    )

    try:
        state = (
            SkillMastery.objects
            .select_for_update()
            .get(
                user=user,
                skill=skill,
            )
        )

        created = False

    except SkillMastery.DoesNotExist:
        state = SkillMastery(
            user=user,
            skill=skill,
        )

        created = True

    state.mastery_score = (
        calculation.mastery_score
    )

    state.observation_count = (
        calculation.observation_count
    )

    state.correct_attempts = (
        calculation.correct_attempts
    )

    state.recent_accuracy = (
        calculation.recent_accuracy
    )

    state.historical_accuracy = (
        calculation.historical_accuracy
    )

    state.consecutive_errors = (
        calculation.consecutive_errors
    )

    state.avg_response_time_ms = (
        calculation.avg_response_time_ms
    )

    state.last_evidence_at = (
        calculation.last_evidence_at
    )

    state.calculated_at = (
        calculation.calculated_at
    )

    state.algorithm_version = (
        calculation.algorithm_version
    )

    state.full_clean()
    state.save()

    return SkillMasteryUpdateResult(
        mastery_id=state.pk,

        user_id=user.pk,
        skill_id=skill.pk,

        mastery_estimate=(
            calculation.mastery_estimate
        ),

        mastery_score=(
            state.mastery_score
        ),

        observation_count=(
            state.observation_count
        ),

        correct_attempts=(
            state.correct_attempts
        ),

        recent_accuracy=(
            state.recent_accuracy
        ),

        historical_accuracy=(
            state.historical_accuracy
        ),

        consecutive_errors=(
            state.consecutive_errors
        ),

        avg_response_time_ms=(
            state.avg_response_time_ms
        ),

        last_evidence_at=(
            state.last_evidence_at
        ),

        calculated_at=(
            state.calculated_at
        ),

        algorithm_version=(
            state.algorithm_version
        ),

        created=created,
    )


@transaction.atomic
def recalculate_skill_masteries(
    *,
    user,
    skill_ids,
    as_of=None,
):
    """
    Recalculate and persist mastery for multiple Skills.

    Rules:
    - lock the learner once;
    - deduplicate skill IDs;
    - process Skills in ascending skill_id order;
    - rebuild every state from assessment history;
    - never increment a previous mastery value.
    """

    if user.pk is None:
        raise ValueError(
            "User must be saved before "
            "mastery can be recalculated."
        )

    if as_of is None:
        as_of = timezone.now()

    normalized_skill_ids = sorted(
        set(skill_ids)
    )

    if not normalized_skill_ids:
        return []

    User = get_user_model()

    try:
        locked_user = (
            User.objects
            .select_for_update()
            .get(
                pk=user.pk
            )
        )
    except User.DoesNotExist as exc:
        raise ValueError(
            "User does not exist."
        ) from exc

    skills_by_id = Skill.objects.in_bulk(
        normalized_skill_ids
    )

    missing_skill_ids = [
        skill_id
        for skill_id in normalized_skill_ids
        if skill_id not in skills_by_id
    ]

    if missing_skill_ids:
        raise ValueError(
            "Unknown skill IDs: "
            + ", ".join(
                str(skill_id)
                for skill_id
                in missing_skill_ids
            )
        )

    results = []

    # Ascending skill_id order is intentional.
    # It gives every transaction the same lock order.
    for skill_id in normalized_skill_ids:
        result = (
            _recalculate_skill_mastery_for_locked_user(
                user=locked_user,
                skill=skills_by_id[skill_id],
                as_of=as_of,
            )
        )

        results.append(
            result
        )

    return results


def recalculate_skill_mastery(
    *,
    user,
    skill_id,
    as_of=None,
):
    """
    Convenience wrapper for recalculating one Skill.
    """

    results = recalculate_skill_masteries(
        user=user,
        skill_ids=[
            skill_id,
        ],
        as_of=as_of,
    )

    return results[0]

def _skill_mastery_needs_refresh(
    *,
    state,
    as_of,
):
    """
    Return True when a persisted SkillMastery snapshot
    must be rebuilt before it is used.

    Refresh conditions:
    - no snapshot exists;
    - snapshot has never been calculated;
    - snapshot uses an obsolete algorithm version;
    - snapshot is at least refresh_after_hours old.

    A caller must not request a point in time earlier
    than an already persisted calculation timestamp.
    """

    config = settings.MASTERY_RULE_V1

    current_algorithm_version = str(
        config["algorithm_version"]
    ).strip()

    if not current_algorithm_version:
        raise ValueError(
            "Mastery algorithm_version must not "
            "be blank."
        )

    refresh_after_hours = int(
        config["refresh_after_hours"]
    )

    if refresh_after_hours < 1:
        raise ValueError(
            "Mastery refresh_after_hours must "
            "be at least 1."
        )

    if state is None:
        return True

    if (
        state.calculated_at is not None
        and state.calculated_at > as_of
    ):
        raise ValueError(
            "Mastery as_of must not be earlier "
            "than calculated_at."
        )

    if state.calculated_at is None:
        return True

    if (
        state.algorithm_version
        != current_algorithm_version
    ):
        return True

    refresh_interval = timedelta(
        hours=refresh_after_hours
    )

    snapshot_age = (
        as_of
        - state.calculated_at
    )

    return snapshot_age >= refresh_interval


def ensure_skill_mastery_current(
    *,
    user,
    skill_id,
    as_of=None,
):
    """
    Return a current persisted SkillMastery snapshot.

    This is the read gateway that consumers such as
    Skill Priority and Recommendation should use
    instead of directly trusting an arbitrary
    SkillMastery row.

    A stale, missing, uncalculated, or obsolete-version
    snapshot is rebuilt from authoritative assessment
    history before being returned.
    """

    if user.pk is None:
        raise ValueError(
            "User must be saved before mastery "
            "can be loaded."
        )

    if as_of is None:
        as_of = timezone.now()

    state = (
        SkillMastery.objects
        .filter(
            user=user,
            skill_id=skill_id,
        )
        .first()
    )

    if _skill_mastery_needs_refresh(
        state=state,
        as_of=as_of,
    ):
        recalculate_skill_mastery(
            user=user,
            skill_id=skill_id,
            as_of=as_of,
        )

        state = (
            SkillMastery.objects
            .get(
                user=user,
                skill_id=skill_id,
            )
        )

    return state