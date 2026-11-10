
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


LEVELS = [
    ("A1", "A1"),
    ("A2", "A2"),
]


# ============================================================
# 1. VOCABULARY TOPIC
# ============================================================

class VocabularyTopic(models.Model):

    code = models.SlugField(
        max_length=80,
        unique=True,
    )

    title_vi = models.CharField(
        max_length=160,
    )

    title_ru = models.CharField(
        max_length=160,
        blank=True,
    )

    cefr_level = models.CharField(
        max_length=2,
        choices=LEVELS,
    )

    description = models.TextField(
        blank=True,
    )

    display_order = models.PositiveIntegerField(
        default=0,
    )

    is_active = models.BooleanField(
        default=True,
    )

    # Assessment is read-only for SRS.
    assessment_skill = models.ForeignKey(
        "curriculum.Skill",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="vocabulary_topics",
    )

    class Meta:
        db_table = "vocabulary_topic"
        ordering = ["display_order", "code"]

    def __str__(self):
        return f"{self.cefr_level}: {self.title_vi}"


# ============================================================
# 2. VOCABULARY ITEM
# ============================================================

class VocabularyItem(models.Model):

    source_id = models.SlugField(
        max_length=100,
        unique=True,
    )

    # Original Russian entry.
    # May contain grammatical annotations.
    lemma = models.CharField(
        max_length=200,
    )

    stress_marked = models.CharField(
        max_length=200,
        blank=True,
    )

    # Vietnamese meaning, to be filled later.
    meaning_vi = models.CharField(
        max_length=300,
        blank=True,
        default="",
    )

    # Original English meaning from source workbook.
    meaning_en = models.CharField(
        max_length=500,
        blank=True,
        default="",
    )

    part_of_speech = models.CharField(
        max_length=50,
        blank=True,
    )

    cefr_level = models.CharField(
        max_length=2,
        choices=LEVELS,
    )

    example_ru = models.TextField(
        blank=True,
    )

    example_vi = models.TextField(
        blank=True,
    )

    notes_vi = models.TextField(
        blank=True,
    )

    audio_url = models.CharField(
        max_length=500,
        blank=True,
    )

    # Original per-level source records.
    source_entries = models.JSONField(
        default=list,
        blank=True,
    )

    is_active = models.BooleanField(
        default=True,
    )

    created_at = models.DateTimeField(
        default=timezone.now,
        editable=False,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    topics = models.ManyToManyField(
        VocabularyTopic,
        through="VocabularyItemTopic",
        related_name="items",
    )

    class Meta:
        db_table = "vocabulary_item"
        ordering = ["source_id"]

    def __str__(self):
        meaning = self.meaning_vi or self.meaning_en
        return f"{self.lemma} - {meaning}"


# ============================================================
# 3. ITEM - TOPIC RELATION
# ============================================================

class VocabularyItemTopic(models.Model):

    item = models.ForeignKey(
        VocabularyItem,
        on_delete=models.CASCADE,
        related_name="topic_links",
    )

    topic = models.ForeignKey(
        VocabularyTopic,
        on_delete=models.PROTECT,
        related_name="item_links",
    )

    display_order = models.PositiveIntegerField(
        default=0,
    )

    class Meta:
        db_table = "vocabulary_item_topic"

        ordering = [
            "topic_id",
            "display_order",
            "item_id",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=["item", "topic"],
                name="uniq_vocab_item_topic",
            ),
        ]


# ============================================================
# 4. USER VOCABULARY SRS STATE
# ============================================================

class VocabularySRSState(models.Model):

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="vocabulary_srs_states",
    )

    item = models.ForeignKey(
        VocabularyItem,
        on_delete=models.PROTECT,
        related_name="srs_states",
    )

    box = models.PositiveSmallIntegerField(
        default=1,
    )

    version = models.PositiveIntegerField(
        default=1,
    )

    lapses = models.PositiveIntegerField(
        default=0,
    )

    learned_at = models.DateTimeField(
        default=timezone.now,
        editable=False,
    )

    next_review_at = models.DateTimeField(
        db_index=True,
    )

    last_reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        db_table = "vocabulary_srs_state"

        constraints = [
            models.UniqueConstraint(
                fields=["user", "item"],
                name="uniq_vocab_user_item_state",
            ),

            models.CheckConstraint(
                condition=Q(
                    box__gte=1,
                    box__lte=5,
                ),
                name="vocab_state_box_1_5",
            ),

            models.CheckConstraint(
                condition=Q(
                    version__gte=1,
                ),
                name="vocab_state_version_positive",
            ),
        ]

        indexes = [
            models.Index(
                fields=["user", "next_review_at"],
                name="vocab_user_due_idx",
            ),
        ]


# ============================================================
# 5. VOCABULARY REVIEW LOG
# ============================================================

class VocabularyReviewLog(models.Model):

    class Rating(models.TextChoices):
        REMEMBERED = "remembered", "Remembered"
        FORGOT = "forgot", "Forgot"

    request_id = models.UUIDField(
        unique=True,
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="vocabulary_review_logs",
    )

    item = models.ForeignKey(
        VocabularyItem,
        on_delete=models.PROTECT,
        related_name="review_logs",
    )

    state = models.ForeignKey(
        VocabularySRSState,
        on_delete=models.CASCADE,
        related_name="review_logs",
    )

    rating = models.CharField(
        max_length=12,
        choices=Rating.choices,
    )

    box_before = models.PositiveSmallIntegerField()
    box_after = models.PositiveSmallIntegerField()

    interval_days_applied = (
        models.PositiveSmallIntegerField()
    )

    state_version_before = (
        models.PositiveIntegerField()
    )

    state_version_after = (
        models.PositiveIntegerField()
    )

    lapses_after = models.PositiveIntegerField()

    reviewed_at = models.DateTimeField(
        default=timezone.now,
        editable=False,
    )

    next_review_at = models.DateTimeField()

    srs_version = models.CharField(
        max_length=40,
    )

    response_time_ms = models.PositiveIntegerField(
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "vocabulary_review_log"

        ordering = [
            "-reviewed_at",
            "-pk",
        ]

        constraints = [
            models.CheckConstraint(
                condition=Q(
                    box_before__gte=1,
                    box_before__lte=5,
                ),
                name="vocab_log_box_before_1_5",
            ),

            models.CheckConstraint(
                condition=Q(
                    box_after__gte=1,
                    box_after__lte=5,
                ),
                name="vocab_log_box_after_1_5",
            ),

            models.CheckConstraint(
                condition=Q(
                    interval_days_applied__gte=1,
                ),
                name="vocab_log_interval_positive",
            ),
        ]

        indexes = [
            models.Index(
                fields=["user", "reviewed_at"],
                name="vocab_user_review_idx",
            ),
        ]


class VocabularySavedWord(models.Model):
    """
    Bookmark của người dùng.

    Save từ không tạo SRS state,
    không thay đổi SkillMastery.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="saved_vocabulary_words",
    )

    item = models.ForeignKey(
        "vocabulary.VocabularyItem",
        on_delete=models.PROTECT,
        related_name="saved_by_users",
    )

    saved_at = models.DateTimeField(
        default=timezone.now,
        editable=False,
    )

    class Meta:
        db_table = "vocabulary_saved_word"

        constraints = [
            models.UniqueConstraint(
                fields=["user", "item"],
                name="uniq_vocab_saved_user_item",
            ),
        ]

        indexes = [
            models.Index(
                fields=["user", "saved_at"],
                name="vocab_saved_user_idx",
            ),
        ]

    def __str__(self):
        return f"{self.user_id}: {self.item.source_id}"
