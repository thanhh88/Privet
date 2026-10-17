from dataclasses import dataclass
from decimal import Decimal


DIAGNOSTIC_RULE_VERSION = "diagnostic_v1"

DIAGNOSTIC_TARGET_QUESTION_COUNT = 15

DIAGNOSTIC_ANCHOR_COUNT = 5

DIAGNOSTIC_OBSERVATIONS_PER_ANCHOR = 3


@dataclass(frozen=True)
class DiagnosticBlueprintSlot:
    """
    One deterministic slot in the initial
    diagnostic assessment.

    target_difficulty is a Question-selection
    target, not an inferred learner mastery.
    """

    slot_number: int

    round_number: int

    anchor_order: int

    skill_code: str

    cefr_level: str

    target_difficulty: Decimal


DIAGNOSTIC_BLUEPRINT_V1 = (
    # =========================================
    # Round 1
    # =========================================

    DiagnosticBlueprintSlot(
        slot_number=1,
        round_number=1,
        anchor_order=1,
        skill_code="ACC-02",
        cefr_level="A1",
        target_difficulty=Decimal("0.2500"),
    ),

    DiagnosticBlueprintSlot(
        slot_number=2,
        round_number=1,
        anchor_order=2,
        skill_code="MOVE-01",
        cefr_level="A1",
        target_difficulty=Decimal("0.2500"),
    ),

    DiagnosticBlueprintSlot(
        slot_number=3,
        round_number=1,
        anchor_order=3,
        skill_code="VOC-01",
        cefr_level="A1",
        target_difficulty=Decimal("0.2500"),
    ),

    DiagnosticBlueprintSlot(
        slot_number=4,
        round_number=1,
        anchor_order=4,
        skill_code="VERB-05",
        cefr_level="A2",
        target_difficulty=Decimal("0.2500"),
    ),

    DiagnosticBlueprintSlot(
        slot_number=5,
        round_number=1,
        anchor_order=5,
        skill_code="VOC-10",
        cefr_level="A2",
        target_difficulty=Decimal("0.5000"),
    ),

    # =========================================
    # Round 2
    # =========================================

    DiagnosticBlueprintSlot(
        slot_number=6,
        round_number=2,
        anchor_order=1,
        skill_code="ACC-02",
        cefr_level="A1",
        target_difficulty=Decimal("0.5000"),
    ),

    DiagnosticBlueprintSlot(
        slot_number=7,
        round_number=2,
        anchor_order=2,
        skill_code="MOVE-01",
        cefr_level="A1",
        target_difficulty=Decimal("0.5000"),
    ),

    DiagnosticBlueprintSlot(
        slot_number=8,
        round_number=2,
        anchor_order=3,
        skill_code="VOC-01",
        cefr_level="A1",
        target_difficulty=Decimal("0.2500"),
    ),

    DiagnosticBlueprintSlot(
        slot_number=9,
        round_number=2,
        anchor_order=4,
        skill_code="VERB-05",
        cefr_level="A2",
        target_difficulty=Decimal("0.5000"),
    ),

    DiagnosticBlueprintSlot(
        slot_number=10,
        round_number=2,
        anchor_order=5,
        skill_code="VOC-10",
        cefr_level="A2",
        target_difficulty=Decimal("0.5000"),
    ),

    # =========================================
    # Round 3
    # =========================================

    DiagnosticBlueprintSlot(
        slot_number=11,
        round_number=3,
        anchor_order=1,
        skill_code="ACC-02",
        cefr_level="A1",
        target_difficulty=Decimal("0.7500"),
    ),

    DiagnosticBlueprintSlot(
        slot_number=12,
        round_number=3,
        anchor_order=2,
        skill_code="MOVE-01",
        cefr_level="A1",
        target_difficulty=Decimal("0.7500"),
    ),

    DiagnosticBlueprintSlot(
        slot_number=13,
        round_number=3,
        anchor_order=3,
        skill_code="VOC-01",
        cefr_level="A1",
        target_difficulty=Decimal("0.2500"),
    ),

    DiagnosticBlueprintSlot(
        slot_number=14,
        round_number=3,
        anchor_order=4,
        skill_code="VERB-05",
        cefr_level="A2",
        target_difficulty=Decimal("0.7500"),
    ),

    DiagnosticBlueprintSlot(
        slot_number=15,
        round_number=3,
        anchor_order=5,
        skill_code="VOC-10",
        cefr_level="A2",
        target_difficulty=Decimal("0.5000"),
    ),
)


def validate_diagnostic_blueprint(
    blueprint=DIAGNOSTIC_BLUEPRINT_V1,
):
    """
    Validate the canonical structural invariants of
    one diagnostic blueprint.

    Pure function: no database access.
    """

    blueprint = tuple(
        blueprint
    )

    if len(
        blueprint
    ) != DIAGNOSTIC_TARGET_QUESTION_COUNT:
        raise ValueError(
            "Diagnostic blueprint must contain "
            "exactly 15 slots."
        )

    expected_slot_numbers = tuple(
        range(
            1,
            DIAGNOSTIC_TARGET_QUESTION_COUNT + 1,
        )
    )

    actual_slot_numbers = tuple(
        slot.slot_number
        for slot in blueprint
    )

    if (
        actual_slot_numbers
        != expected_slot_numbers
    ):
        raise ValueError(
            "Diagnostic slot numbers must be "
            "continuous from 1 to 15."
        )

    anchor_skill_codes = []

    observations_by_skill = {}

    for slot in blueprint:
        if not isinstance(
            slot,
            DiagnosticBlueprintSlot,
        ):
            raise ValueError(
                "Every diagnostic slot must be a "
                "DiagnosticBlueprintSlot."
            )

        if slot.round_number not in {
            1,
            2,
            3,
        }:
            raise ValueError(
                "Diagnostic round_number must be "
                "1, 2, or 3."
            )

        if not (
            1
            <= slot.anchor_order
            <= DIAGNOSTIC_ANCHOR_COUNT
        ):
            raise ValueError(
                "Diagnostic anchor_order must be "
                "between 1 and 5."
            )

        if (
            not isinstance(
                slot.skill_code,
                str,
            )
            or not slot.skill_code.strip()
        ):
            raise ValueError(
                "Diagnostic skill_code must be "
                "non-blank."
            )

        if slot.cefr_level not in {
            "A1",
            "A2",
        }:
            raise ValueError(
                "Diagnostic CEFR level must be "
                "A1 or A2."
            )

        difficulty = Decimal(
            str(
                slot.target_difficulty
            )
        )

        if not (
            Decimal("0")
            <= difficulty
            <= Decimal("1")
        ):
            raise ValueError(
                "Diagnostic target difficulty must "
                "be between 0 and 1."
            )

        if (
            slot.skill_code
            not in anchor_skill_codes
        ):
            anchor_skill_codes.append(
                slot.skill_code
            )

        observations_by_skill[
            slot.skill_code
        ] = (
            observations_by_skill.get(
                slot.skill_code,
                0,
            )
            + 1
        )

    if (
        len(anchor_skill_codes)
        != DIAGNOSTIC_ANCHOR_COUNT
    ):
        raise ValueError(
            "Diagnostic blueprint must contain "
            "exactly five anchor Skills."
        )

    for (
        skill_code,
        observation_count,
    ) in observations_by_skill.items():
        if (
            observation_count
            != DIAGNOSTIC_OBSERVATIONS_PER_ANCHOR
        ):
            raise ValueError(
                f"Diagnostic anchor {skill_code} "
                f"must appear exactly three times."
            )

    # -----------------------------------------
    # CEFR consistency across anchors
    # -----------------------------------------

    cefr_by_skill = {}

    for slot in blueprint:
        existing_cefr = cefr_by_skill.get(
            slot.skill_code
        )

        if existing_cefr is None:
            cefr_by_skill[
                slot.skill_code
            ] = slot.cefr_level

        elif existing_cefr != slot.cefr_level:
            raise ValueError(
                f"Diagnostic anchor "
                f"{slot.skill_code} must keep "
                f"the same CEFR level across "
                f"all rounds."
            )

    a1_anchor_count = sum(
        1
        for cefr_level in cefr_by_skill.values()
        if cefr_level == "A1"
    )

    a2_anchor_count = sum(
        1
        for cefr_level in cefr_by_skill.values()
        if cefr_level == "A2"
    )

    if (
        a1_anchor_count != 3
        or a2_anchor_count != 2
    ):
        raise ValueError(
            "Diagnostic blueprint must contain "
            "exactly three A1 anchors and "
            "two A2 anchors."
        )

    # Each round must contain all five anchors
    # exactly once and in the same pedagogical order.
    expected_anchor_order = tuple(
        anchor_skill_codes
    )

    for round_number in (
        1,
        2,
        3,
    ):
        round_codes = tuple(
            slot.skill_code
            for slot in blueprint
            if (
                slot.round_number
                == round_number
            )
        )

        if (
            round_codes
            != expected_anchor_order
        ):
            raise ValueError(
                "Every diagnostic round must "
                "contain the same five anchors "
                "in the same order."
            )

    return blueprint