from curriculum.models import Skill
from questionbank.models import QuestionSkill

skills = (
    Skill.objects
    .filter(
        is_active=True,
        is_extension=False,
    )
    .select_related("topic")
    .order_by(
        "cefr_level",
        "skill_code",
    )
)

for skill in skills:
    qs = QuestionSkill.objects.filter(
        skill=skill,
        question__is_active=True,
    )

    count = (
        qs.values("question_id")
        .distinct()
        .count()
    )

    primary_count = (
        qs.filter(is_primary=True)
        .values("question_id")
        .distinct()
        .count()
    )

    difficulties = sorted({
        str(value)
        for value in qs.values_list(
            "question__difficulty_score",
            flat=True,
        )
    })

    print(
        f"{skill.skill_code}\t"
        f"{skill.cefr_level}\t"
        f"{skill.topic.category}\t"
        f"count={count}\t"
        f"difficulties={difficulties}\t"
        f"primary={primary_count}"
    )
