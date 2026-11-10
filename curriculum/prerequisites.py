from collections import defaultdict, deque
from decimal import Decimal

from django.db import transaction

from .models import (
    DEFAULT_PREREQUISITE_MASTERY,
    Skill,
    SkillPrerequisite,
)


DEFAULT_REQUIRED_MASTERY = DEFAULT_PREREQUISITE_MASTERY


def _coerce_skill_id(skill_or_id):
    if isinstance(skill_or_id, Skill):
        return skill_or_id.pk
    return int(skill_or_id)


def would_create_prerequisite_cycle(
    *,
    skill_id,
    prerequisite_skill_id,
    exclude_edge_id=None,
):
    """Return True when adding an active edge would create a graph cycle.

    Edges are directed as ``skill -> prerequisite_skill``. Adding
    ``skill -> prerequisite`` creates a cycle when ``prerequisite`` already
    reaches ``skill`` through active prerequisite edges.
    """

    skill_id = int(skill_id)
    prerequisite_skill_id = int(prerequisite_skill_id)

    if skill_id == prerequisite_skill_id:
        return True

    queryset = SkillPrerequisite.objects.filter(is_active=True)
    if exclude_edge_id is not None:
        queryset = queryset.exclude(pk=exclude_edge_id)

    adjacency = defaultdict(list)
    for dependent_id, required_id in queryset.values_list(
        "skill_id",
        "prerequisite_skill_id",
    ):
        adjacency[dependent_id].append(required_id)

    frontier = [prerequisite_skill_id]
    visited = set()

    while frontier:
        current_id = frontier.pop()
        if current_id == skill_id:
            return True
        if current_id in visited:
            continue
        visited.add(current_id)
        frontier.extend(adjacency.get(current_id, ()))

    return False


def get_direct_prerequisite_links(skill, *, active_only=True):
    """Return deterministic direct prerequisite links for ``skill``."""

    skill_id = _coerce_skill_id(skill)
    queryset = (
        SkillPrerequisite.objects.filter(skill_id=skill_id)
        .select_related("prerequisite_skill", "prerequisite_skill__topic")
    )
    if active_only:
        queryset = queryset.filter(is_active=True)

    return queryset.order_by(
        "prerequisite_skill__topic_id",
        "prerequisite_skill__display_order",
        "prerequisite_skill_id",
    )


def get_prerequisite_closure(skill, *, active_only=True):
    """Return all transitive prerequisites in deterministic breadth-first order.

    The function is cycle-safe even if legacy/imported data bypassed model
    validation. It returns each prerequisite skill at most once.
    """

    root_id = _coerce_skill_id(skill)
    queryset = SkillPrerequisite.objects.all()
    if active_only:
        queryset = queryset.filter(is_active=True)

    adjacency = defaultdict(list)
    for dependent_id, required_id in queryset.values_list(
        "skill_id",
        "prerequisite_skill_id",
    ):
        adjacency[dependent_id].append(required_id)

    for required_ids in adjacency.values():
        required_ids.sort()

    queue = deque(adjacency.get(root_id, ()))
    seen = {root_id}
    ordered_ids = []

    while queue:
        current_id = queue.popleft()
        if current_id in seen:
            continue
        seen.add(current_id)
        ordered_ids.append(current_id)
        queue.extend(adjacency.get(current_id, ()))

    skills_by_id = Skill.objects.in_bulk(ordered_ids)
    return [
        skills_by_id[skill_id]
        for skill_id in ordered_ids
        if skill_id in skills_by_id
    ]


@transaction.atomic
def set_skill_prerequisite(
    *,
    skill,
    prerequisite_skill,
    required_mastery=DEFAULT_REQUIRED_MASTERY,
    is_active=True,
):
    """Create or update one prerequisite edge after full validation.

    This service is the preferred write path outside Django admin/import code.
    Database constraints still protect pair uniqueness, self-links and mastery
    range if callers bypass this service.
    """

    edge = (
        SkillPrerequisite.objects.select_for_update()
        .filter(
            skill=skill,
            prerequisite_skill=prerequisite_skill,
        )
        .first()
    )

    if edge is None:
        edge = SkillPrerequisite(
            skill=skill,
            prerequisite_skill=prerequisite_skill,
        )

    edge.required_mastery = Decimal(str(required_mastery))
    edge.is_active = bool(is_active)
    edge.full_clean()
    edge.save()
    return edge
