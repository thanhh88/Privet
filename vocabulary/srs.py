
"""Pure, deterministic Leitner policy."""

from datetime import timedelta


def validate_policy(policy):
    expected_keys = {
        "version",
        "intervals",
        "max_new_per_day",
        "max_due_per_request",
    }

    if not isinstance(policy, dict):
        raise ValueError("Invalid VOCABULARY_SRS")

    if set(policy) != expected_keys:
        raise ValueError("Invalid VOCABULARY_SRS keys")

    version = policy["version"]
    intervals = policy["intervals"]

    if not isinstance(version, str) or not version.strip():
        raise ValueError("Invalid SRS version")

    if (
        not isinstance(intervals, dict)
        or set(intervals) != set(range(1, 6))
    ):
        raise ValueError("Intervals must define boxes 1..5")

    if any(
        type(days) is not int or days < 1
        for days in intervals.values()
    ):
        raise ValueError(
            "Intervals must be positive integer days"
        )

    for key in (
        "max_new_per_day",
        "max_due_per_request",
    ):
        value = policy[key]

        if type(value) is not int or value < 1:
            raise ValueError(
                f"{key} must be a positive integer"
            )

    return policy


def advance(box, rating, now, policy):
    validate_policy(policy)

    if type(box) is not int or box not in range(1, 6):
        raise ValueError("Invalid Leitner box")

    if rating == "remembered":
        new_box = min(5, box + 1)

    elif rating == "forgot":
        new_box = 1

    else:
        raise ValueError("Invalid rating")

    days = policy["intervals"][new_box]

    return (
        new_box,
        days,
        now + timedelta(days=days),
    )
