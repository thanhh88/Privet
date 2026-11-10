"""Human-readable recommendation explanations.

R6 owns explanation rendering only.

It consumes already-ranked R5 recommendations and turns their score
components plus candidate metadata into short Vietnamese UI copy and
structured, auditable reasons.  R6 never changes eligibility, features,
scores, ranks, or learner state.
"""

from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings

from recommendation.candidates import RecommendationCandidateType
from recommendation.ranking import RankedRecommendation, rank_recommendations


_ZERO = Decimal("0")


@dataclass(frozen=True)
class RecommendationExplanationReason:
    """One user-facing reason traceable to an R5 score component."""

    code: str
    message: str
    feature_name: str | None
    value: Decimal | None
    contribution: Decimal | None


@dataclass(frozen=True)
class ExplainedRecommendation:
    """One ranked recommendation plus versioned UI explanation text."""

    ranked: RankedRecommendation
    explanation_version: str
    headline: str
    reasons: tuple[RecommendationExplanationReason, ...]

    @property
    def rank(self):
        return self.ranked.rank

    @property
    def candidate(self):
        return self.ranked.candidate

    @property
    def score(self):
        return self.ranked.score

    @property
    def scoring_version(self):
        return self.ranked.scoring_version


_REASON_CODE_BY_FEATURE = {
    "weakness": "target_skill_needs_strengthening",
    "recurring_errors": "recurring_errors_detected",
    "review_urgency": "review_is_due",
    "uncertainty": "mastery_is_uncertain",
    "recent_learning": "recent_learning_relevance",
    "prerequisite_readiness": "prerequisites_ready",
    "continuity": "continue_current_work",
    "novelty": "new_learning_opportunity",
    "difficulty_fit": "difficulty_fit",
}

# Stable order only breaks exact contribution ties.  R5 contribution remains
# the primary signal used to choose which reasons are surfaced.
_REASON_TIE_ORDER = {
    feature_name: index
    for index, feature_name in enumerate(
        (
            "weakness",
            "recurring_errors",
            "review_urgency",
            "uncertainty",
            "recent_learning",
            "prerequisite_readiness",
            "continuity",
            "novelty",
            "difficulty_fit",
        )
    )
}


def _load_policy():
    raw = getattr(settings, "RECOMMENDATION_EXPLANATION", None)
    if not isinstance(raw, dict):
        raise ValueError("RECOMMENDATION_EXPLANATION must be a dictionary.")

    version = raw.get("version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError(
            "RECOMMENDATION_EXPLANATION.version must be a non-empty string."
        )

    default_max_reasons = raw.get("default_max_reasons")
    if (
        isinstance(default_max_reasons, bool)
        or not isinstance(default_max_reasons, int)
        or default_max_reasons <= 0
    ):
        raise ValueError(
            "RECOMMENDATION_EXPLANATION.default_max_reasons must be a "
            "positive integer."
        )

    return version.strip(), default_max_reasons


def _validate_max_reasons(max_reasons, *, default_max_reasons):
    if max_reasons is None:
        return default_max_reasons
    if (
        isinstance(max_reasons, bool)
        or not isinstance(max_reasons, int)
        or max_reasons <= 0
    ):
        raise ValueError("max_reasons must be a positive integer.")
    return max_reasons


def _validated_ranked_rows(ranked_recommendations):
    rows = tuple(ranked_recommendations)
    candidate_keys = set()
    ranks = set()

    for row in rows:
        if not isinstance(row, RankedRecommendation):
            raise TypeError(
                "ranked_recommendations must contain RankedRecommendation "
                "instances only."
            )

        if isinstance(row.rank, bool) or not isinstance(row.rank, int) or row.rank <= 0:
            raise ValueError("Recommendation ranks must be positive integers.")

        key = row.candidate.candidate_key
        if key in candidate_keys:
            raise ValueError("Ranked recommendation candidate keys must be unique.")
        candidate_keys.add(key)

        if row.rank in ranks:
            raise ValueError("Ranked recommendation ranks must be unique.")
        ranks.add(row.rank)

    return tuple(sorted(rows, key=lambda row: row.rank))


def _headline(candidate):
    title = (candidate.lesson_title or "").strip()

    if candidate.candidate_type == RecommendationCandidateType.RESUME_ASSESSMENT:
        if candidate.assessment_mode == "diagnostic":
            return "Tiếp tục bài đánh giá đầu vào"
        return "Tiếp tục bài đánh giá đang làm"

    if candidate.candidate_type == RecommendationCandidateType.CONTINUE_LESSON:
        if title:
            return f"Tiếp tục bài học: {title}"
        return "Tiếp tục bài học đang học"

    if candidate.candidate_type == RecommendationCandidateType.START_LESSON:
        if title:
            return f"Học bài: {title}"
        return "Bắt đầu bài học phù hợp"

    if candidate.candidate_type == RecommendationCandidateType.VOCABULARY_REVIEW:
        if candidate.vocabulary_due_count > 0:
            return f"Ôn {candidate.vocabulary_due_count} từ vựng đến hạn"
        return "Ôn từ vựng đến hạn"

    if (
        candidate.candidate_type
        == RecommendationCandidateType.START_ADAPTIVE_ASSESSMENT
    ):
        return "Bắt đầu bài đánh giá thích ứng"

    raise ValueError(
        f"Unsupported recommendation candidate type: {candidate.candidate_type!r}."
    )


def _message_for_component(*, candidate, feature_name):
    if feature_name == "weakness":
        return "Một hoặc nhiều kỹ năng liên quan vẫn cần được củng cố."

    if feature_name == "recurring_errors":
        return "Lịch sử học cho thấy lỗi lặp lại ở kỹ năng liên quan."

    if feature_name == "review_urgency":
        if (
            candidate.candidate_type
            == RecommendationCandidateType.VOCABULARY_REVIEW
            and candidate.vocabulary_due_count > 0
        ):
            return (
                f"Có {candidate.vocabulary_due_count} từ vựng đã đến hạn "
                "ôn trong hệ thống SRS."
            )
        return "Kỹ năng liên quan đang có nhu cầu ôn tập cao."

    if feature_name == "uncertainty":
        return (
            "Ước lượng mức độ thành thạo của kỹ năng liên quan còn chưa "
            "chắc chắn."
        )

    if feature_name == "recent_learning":
        return "Nội dung này liên quan đến kiến thức bạn vừa học gần đây."

    if feature_name == "prerequisite_readiness":
        return "Bài học hiện sẵn sàng về các điều kiện tiên quyết."

    if feature_name == "continuity":
        if candidate.candidate_type == RecommendationCandidateType.RESUME_ASSESSMENT:
            return "Bạn đang có một bài đánh giá chưa hoàn thành."
        if candidate.candidate_type == RecommendationCandidateType.CONTINUE_LESSON:
            return "Bạn đang học dở bài này."
        return "Hoạt động này tiếp nối công việc học hiện tại."

    if feature_name == "novelty":
        if (
            candidate.candidate_type
            == RecommendationCandidateType.START_ADAPTIVE_ASSESSMENT
        ):
            return "Hoạt động này cung cấp một lần đánh giá thích ứng mới."
        if candidate.candidate_type == RecommendationCandidateType.START_LESSON:
            return "Bài học này bổ sung nội dung mới vào lộ trình hiện tại."
        return "Hoạt động này bổ sung nội dung mới cho lộ trình hiện tại."

    if feature_name == "difficulty_fit":
        return "Độ khó của hoạt động phù hợp với trạng thái hiện tại."

    raise ValueError(f"Unsupported explanation feature: {feature_name!r}.")


def _fallback_reason(candidate):
    if candidate.candidate_type == RecommendationCandidateType.RESUME_ASSESSMENT:
        message = "Bài đánh giá này đang hoạt động và có thể tiếp tục ngay."
    elif candidate.candidate_type == RecommendationCandidateType.CONTINUE_LESSON:
        message = "Bài học này đang học dở và có thể tiếp tục ngay."
    elif candidate.candidate_type == RecommendationCandidateType.VOCABULARY_REVIEW:
        if candidate.vocabulary_due_count > 0:
            message = (
                f"Có {candidate.vocabulary_due_count} từ vựng đang đến hạn "
                "ôn tập."
            )
        else:
            message = "Hoạt động ôn từ vựng hiện đang khả dụng."
    elif candidate.candidate_type == RecommendationCandidateType.START_LESSON:
        message = "Bài học này hiện đủ điều kiện để bắt đầu."
    elif (
        candidate.candidate_type
        == RecommendationCandidateType.START_ADAPTIVE_ASSESSMENT
    ):
        message = "Bạn hiện đủ điều kiện để bắt đầu bài đánh giá thích ứng."
    else:
        raise ValueError(
            f"Unsupported recommendation candidate type: {candidate.candidate_type!r}."
        )

    return RecommendationExplanationReason(
        code="available_now",
        message=message,
        feature_name=None,
        value=None,
        contribution=None,
    )


def _reasons_for_ranked(*, ranked, max_reasons):
    for component in ranked.components:
        if component.feature_name not in _REASON_TIE_ORDER:
            raise ValueError(
                "Unsupported R5 score component for explanation: "
                f"{component.feature_name!r}."
            )

    positive_components = [
        component
        for component in ranked.components
        if component.contribution > _ZERO
    ]

    positive_components.sort(
        key=lambda component: (
            -component.contribution,
            _REASON_TIE_ORDER[component.feature_name],
        )
    )

    reasons = []
    for component in positive_components[:max_reasons]:
        reasons.append(
            RecommendationExplanationReason(
                code=_REASON_CODE_BY_FEATURE[component.feature_name],
                message=_message_for_component(
                    candidate=ranked.candidate,
                    feature_name=component.feature_name,
                ),
                feature_name=component.feature_name,
                value=component.value,
                contribution=component.contribution,
            )
        )

    if not reasons:
        reasons.append(_fallback_reason(ranked.candidate))

    return tuple(reasons)


def explain_ranked_recommendations(*, ranked_recommendations, max_reasons=None):
    """Create deterministic, traceable Vietnamese explanations for R5 rows."""

    version, default_max_reasons = _load_policy()
    max_reasons = _validate_max_reasons(
        max_reasons,
        default_max_reasons=default_max_reasons,
    )
    rows = _validated_ranked_rows(ranked_recommendations)

    return tuple(
        ExplainedRecommendation(
            ranked=row,
            explanation_version=version,
            headline=_headline(row.candidate),
            reasons=_reasons_for_ranked(
                ranked=row,
                max_reasons=max_reasons,
            ),
        )
        for row in rows
    )


def explain_recommendations(
    *,
    user,
    candidates=None,
    as_of=None,
    limit=None,
    max_reasons=None,
):
    """Run R4 -> R5 -> R6 and return explained Top-N recommendations."""

    ranked = rank_recommendations(
        user=user,
        candidates=candidates,
        as_of=as_of,
        limit=limit,
    )
    return explain_ranked_recommendations(
        ranked_recommendations=ranked,
        max_reasons=max_reasons,
    )
