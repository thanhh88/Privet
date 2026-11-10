"""Public UI views."""

import calendar
import json

from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models.functions import TruncDate
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
)
from django.shortcuts import (
    render,
    resolve_url,
)
from django.utils import timezone

from tracking.models import StudySession
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie

# ============================================================
# LESSON JSON LOCATION
# ============================================================

LESSON_DATA_DIR = (
    Path(settings.BASE_DIR)
    / "curriculum"
    / "data"
    / "lessons"
)


# ============================================================
# HELPERS
# ============================================================

def _get_display_name(user):
    """
    Registration UI hiện tại đang lưu:

    first_name = Họ
    last_name = Tên

    Vì vậy dùng last_name để chào người dùng bằng Tên.
    """

    return (
        user.last_name
        or user.first_name
        or "Bạn"
    )


def _load_lesson_json(lesson_slug):
    """
    Đọc một lesson từ:

    curriculum/data/lessons/<slug>.json

    Ví dụ:

    a1-noun-gender
        ->
    curriculum/data/lessons/a1-noun-gender.json
    """

    file_path = (
        LESSON_DATA_DIR
        / f"{lesson_slug}.json"
    )

    if not file_path.is_file():
        raise Http404(
            "Không tìm thấy bài học."
        )

    try:
        lesson = json.loads(
            file_path.read_text(
                encoding="utf-8"
            )
        )

    except (
        OSError,
        json.JSONDecodeError,
    ) as exc:
        raise Http404(
            "Không thể đọc dữ liệu bài học."
        ) from exc


    # Slug trong JSON phải khớp tên file / URL.
    if lesson.get("slug") != lesson_slug:
        raise Http404(
            "Lesson slug không hợp lệ."
        )


    # Cho phép ẩn lesson khỏi giao diện.
    if not lesson.get(
        "is_active",
        True,
    ):
        raise Http404(
            "Bài học hiện không khả dụng."
        )


    return lesson


# ============================================================
# LANDING
# ============================================================

def landing(
    request: HttpRequest,
) -> HttpResponse:
    """
    Landing page.

    Auth URL hiện được lấy từ settings.
    """

    context = {
        "login_url": resolve_url(
            getattr(
                settings,
                "PRIVET_LOGIN_URL",
                "/accounts/login/",
            )
        ),
        "register_url": resolve_url(
            getattr(
                settings,
                "PRIVET_REGISTER_URL",
                "/accounts/register/",
            )
        ),
    }

    return render(
        request,
        "UI/landing.html",
        context,
    )


# ============================================================
# DASHBOARD
# ============================================================

@login_required
def dashboard(request):
    today = timezone.localdate()

    display_name = _get_display_name(
        request.user
    )


    # ========================================================
    # CURRENT MONTH
    # ========================================================

    days_in_month = calendar.monthrange(
        today.year,
        today.month,
    )[1]

    first_day = today.replace(
        day=1
    )

    last_day = today.replace(
        day=days_in_month
    )


    # Ngày nào có active StudySession
    # thì coi là ngày đã học.

    month_study_dates = set(
        StudySession.objects.filter(
            user=request.user,
            active_seconds__gt=0,
            started_at__date__range=(
                first_day,
                last_day,
            ),
        )
        .annotate(
            study_date=TruncDate(
                "started_at"
            )
        )
        .values_list(
            "study_date",
            flat=True,
        )
    )


    streak_days = []

    for day_number in range(
        1,
        days_in_month + 1,
    ):
        current_date = today.replace(
            day=day_number
        )

        streak_days.append(
            {
                "day": day_number,

                "learned": (
                    current_date
                    in month_study_dates
                ),

                "is_today": (
                    current_date
                    == today
                ),
            }
        )


    # ========================================================
    # TEMP UI DEMO
    #
    # Chỉ để nhìn thử các logo gấu trong streak.
    # Sau này xóa toàn bộ block này.
    # ========================================================

    demo_study_days = {
        1,
        2,
        5,
        7,
        8,
        11,
        14,
        15,
        16,
        18,
    }

    for item in streak_days:
        if item["day"] in demo_study_days:
            item["learned"] = True


    # ========================================================
    # CURRENT STREAK
    # ========================================================

    recent_start = (
        today
        - timedelta(days=90)
    )

    recent_study_dates = set(
        StudySession.objects.filter(
            user=request.user,
            active_seconds__gt=0,
            started_at__date__gte=(
                recent_start
            ),
            started_at__date__lte=today,
        )
        .annotate(
            study_date=TruncDate(
                "started_at"
            )
        )
        .values_list(
            "study_date",
            flat=True,
        )
    )


    # Nếu hôm nay chưa học,
    # nhưng hôm qua có học,
    # streak vẫn được giữ.

    if today in recent_study_dates:
        streak_cursor = today

    elif (
        today - timedelta(days=1)
        in recent_study_dates
    ):
        streak_cursor = (
            today
            - timedelta(days=1)
        )

    else:
        streak_cursor = None


    streak_count = 0

    while (
        streak_cursor
        and streak_cursor
        in recent_study_dates
    ):
        streak_count += 1

        streak_cursor -= timedelta(
            days=1
        )


    context = {
        "display_name": display_name,

        "streak_days": streak_days,

        "streak_count": streak_count,

        "month_label": (
            f"Tháng "
            f"{today.month}/"
            f"{today.year}"
        ),
    }


    return render(
        request,
        "UI/dashboard.html",
        context,
    )


# ============================================================
# LESSON CATALOG
# ============================================================

@login_required
def lessons(request):
    """
    Danh sách lesson.

    Mỗi lesson được đọc từ một JSON riêng.
    """

    selected_category = (
        request.GET.get(
            "category",
            "grammar",
        )
    )

    selected_level = (
        request.GET.get(
            "level",
            "A1",
        )
    )

    if selected_category == "vocabulary":
        level = (
            selected_level
            if selected_level in {"A1", "A2", "B1"}
            else "A1"
        )

        return HttpResponseRedirect(
            f"{reverse('UI:vocabulary')}?level={level}"
        )

    lesson_list = []

    # ========================================================
    # READ LESSON JSON FILES
    # ========================================================

    if LESSON_DATA_DIR.exists():

        for file_path in (
            LESSON_DATA_DIR.glob(
                "*.json"
            )
        ):

            try:
                lesson = json.loads(
                    file_path.read_text(
                        encoding="utf-8"
                    )
                )

            except (
                OSError,
                json.JSONDecodeError,
            ):
                # Một JSON lỗi không nên làm crash
                # toàn bộ catalog.
                continue


            # Hidden lesson
            if not lesson.get(
                "is_active",
                True,
            ):
                continue


            # Category filter
            if (
                lesson.get(
                    "category_key"
                )
                != selected_category
            ):
                continue


            # CEFR filter
            if (
                lesson.get(
                    "cefr_level"
                )
                != selected_level
            ):
                continue


            # Lesson phải có slug.
            if not lesson.get("slug"):
                continue


            lesson_list.append(
                lesson
            )


    # Thứ tự xuất hiện trong catalog.
    lesson_list.sort(
        key=lambda item: item.get(
            "order",
            9999,
        )
    )


    display_name = _get_display_name(
        request.user
    )


    context = {
        "lessons": lesson_list,

        "selected_category": (
            selected_category
        ),

        "selected_level": (
            selected_level
        ),

        "display_name": (
            display_name
        ),
    }


    return render(
        request,
        "UI/lessons.html",
        context,
    )


# ============================================================
# LESSON DETAIL
# ============================================================

@login_required
def lesson_detail(
    request,
    lesson_slug,
):
    """
    Một template chung cho tất cả lesson.

    URL:
        /lessons/<slug>/

    JSON:
        curriculum/data/lessons/<slug>.json

    Template:
        UI/lesson_detail.html
    """

    lesson = _load_lesson_json(
        lesson_slug
    )


    display_name = _get_display_name(
        request.user
    )


    context = {
        "lesson": lesson,

        "display_name": (
            display_name
        ),

        # Demo UI.
        # Sau này lấy từ LessonProgress.
        "lesson_progress": 0,
    }


    return render(
        request,
        "UI/lesson_detail.html",
        context,
    )

@login_required
def progress(request):
    """
    Progress page.

    Hiện tại dùng demo data để dựng UI.
    Sau này sẽ thay bằng dữ liệu thật từ:
    - LessonProgress
    - Skill mastery
    - StudySession
    - assessment/question history
    """

    display_name = _get_display_name(
        request.user
    )


    # ========================================================
    # TEMP UI DEMO DATA
    # Sau này thay bằng dữ liệu thật.
    # ========================================================

    progress_data = {
        "overall_percent": 38,

        "current_level": "A1",

        "completed_lessons": 7,
        "total_lessons": 18,

        "streak_count": 4,

        "questions_total": 126,

        "skills_mastered": 3,
        "skills_total": 9,
    }


    level_progress = [
        {
            "code": "A1",
            "name": "Cơ bản",
            "percent": 58,
            "status": "Đang học",
            "active": True,
        },
        {
            "code": "A2",
            "name": "Sơ cấp",
            "percent": 0,
            "status": "Chưa bắt đầu",
            "active": False,
        },
        {
            "code": "B1",
            "name": "Mở rộng",
            "percent": 0,
            "status": "Chưa bắt đầu",
            "active": False,
        },
    ]


    skill_progress = [
        {
            "name": "Giống danh từ",
            "percent": 82,
        },
        {
            "name": "Đại từ nhân xưng",
            "percent": 71,
        },
        {
            "name": "Động từ hiện tại",
            "percent": 64,
        },
        {
            "name": "Cách 4",
            "percent": 46,
        },
        {
            "name": "Động từ chuyển động",
            "percent": 28,
        },
    ]


    weekly_activity = [
        {
            "day": "T2",
            "questions": 8,
            "height": 40,
        },
        {
            "day": "T3",
            "questions": 14,
            "height": 70,
        },
        {
            "day": "T4",
            "questions": 5,
            "height": 25,
        },
        {
            "day": "T5",
            "questions": 11,
            "height": 55,
        },
        {
            "day": "T6",
            "questions": 18,
            "height": 90,
        },
        {
            "day": "T7",
            "questions": 10,
            "height": 50,
        },
        {
            "day": "CN",
            "questions": 12,
            "height": 60,
        },
    ]


    context = {
        "display_name": display_name,

        "progress": progress_data,

        "level_progress": level_progress,

        "skill_progress": skill_progress,

        "weekly_activity": weekly_activity,
    }


    return render(
        request,
        "UI/progress.html",
        context,
    )

# ============================================================
# TEST HUB
# ============================================================

@login_required
def test_hub(request):
    """
    Test hub UI demo.
    """

    display_name = _get_display_name(
        request.user
    )

    test_history = (
        _demo_test_history_data()
    )


    context = {
        "display_name": display_name,

        "completed_test_count": len(
            test_history
        ),

        "recent_tests": (
            test_history[:3]
        ),

        "submitted_demo": (
            request.GET.get(
                "submitted"
            )
            == "1"
        ),
    }


    return render(
        request,
        "UI/test_hub.html",
        context,
    )

@login_required
def test_history(request):
    """
    Lịch sử bài test + review bài đã chọn.

    Query parameters:

        ?test=101
        ?test=101&filter=wrong
        ?test=101&filter=blank
    """

    display_name = _get_display_name(
        request.user
    )

    test_history_data = (
        _demo_test_history_data()
    )


    if not test_history_data:
        raise Http404(
            "Chưa có lịch sử bài test."
        )


    requested_test_id = request.GET.get(
        "test"
    )


    if requested_test_id is None:
        selected_test = (
            test_history_data[0]
        )

    else:
        try:
            requested_test_id = int(
                requested_test_id
            )

        except (
            TypeError,
            ValueError,
        ) as exc:
            raise Http404(
                "Mã bài test không hợp lệ."
            ) from exc


        selected_test = next(
            (
                item
                for item
                in test_history_data
                if item["id"]
                == requested_test_id
            ),
            None,
        )


        if selected_test is None:
            raise Http404(
                "Không tìm thấy bài test."
            )


    review_filter = request.GET.get(
        "filter",
        "all",
    )


    if review_filter not in {
        "all",
        "wrong",
        "blank",
    }:
        review_filter = "all"


    if review_filter == "wrong":
        review_questions = [
            question
            for question
            in selected_test["questions"]
            if question["status"]
            == "wrong"
        ]

    elif review_filter == "blank":
        review_questions = [
            question
            for question
            in selected_test["questions"]
            if question["status"]
            == "blank"
        ]

    else:
        review_questions = (
            selected_test["questions"]
        )


    context = {
        "display_name": display_name,

        "test_history": (
            test_history_data
        ),

        "selected_test": (
            selected_test
        ),

        "review_filter": (
            review_filter
        ),

        "review_questions": (
            review_questions
        ),
    }


    return render(
        request,
        "UI/test_history.html",
        context,
    )

# ============================================================
# TEST DOING — DEMO
# ============================================================

@login_required
def test_doing(request):
    """
    Màn hình làm bài demo.

    Mỗi câu được mở bằng query parameter:

        /tests/demo/?q=1
        /tests/demo/?q=2
        ...

    Đây mới là UI demo, chưa ghi đáp án vào database.
    """

    display_name = _get_display_name(
        request.user
    )

    demo_questions = [
        {
            "number": 1,
            "instruction": "Chọn đáp án đúng",
            "question": "Я живу в ______.",
            "translation": "Tôi sống ở Moskva.",
            "options": [
                {
                    "key": "A",
                    "text": "Москва",
                },
                {
                    "key": "Б",
                    "text": "Москве",
                },
                {
                    "key": "В",
                    "text": "Москву",
                },
                {
                    "key": "Г",
                    "text": "Москвой",
                },
            ],
        },
        {
            "number": 2,
            "instruction": "Chọn đáp án đúng",
            "question": "Это ______ книга.",
            "translation": "Đây là một quyển sách mới.",
            "options": [
                {
                    "key": "A",
                    "text": "новый",
                },
                {
                    "key": "Б",
                    "text": "новая",
                },
                {
                    "key": "В",
                    "text": "новое",
                },
                {
                    "key": "Г",
                    "text": "новые",
                },
            ],
        },
        {
            "number": 3,
            "instruction": "Chọn đáp án đúng",
            "question": "Мы ______ русский язык.",
            "translation": "Chúng tôi học tiếng Nga.",
            "options": [
                {
                    "key": "A",
                    "text": "учишь",
                },
                {
                    "key": "Б",
                    "text": "учу",
                },
                {
                    "key": "В",
                    "text": "учим",
                },
                {
                    "key": "Г",
                    "text": "учит",
                },
            ],
        },
        {
            "number": 4,
            "instruction": "Chọn đáp án đúng",
            "question": "Она идёт ______ университет.",
            "translation": "Cô ấy đang đi đến trường đại học.",
            "options": [
                {
                    "key": "A",
                    "text": "в",
                },
                {
                    "key": "Б",
                    "text": "на",
                },
                {
                    "key": "В",
                    "text": "из",
                },
                {
                    "key": "Г",
                    "text": "с",
                },
            ],
        },
        {
            "number": 5,
            "instruction": "Chọn đáp án đúng",
            "question": "Я не вижу ______ в этом магазине.",
            "translation": "Tôi không thấy anh trai mình trong cửa hàng này.",
            "options": [
                {
                    "key": "A",
                    "text": "мой брат",
                },
                {
                    "key": "Б",
                    "text": "моего брата",
                },
                {
                    "key": "В",
                    "text": "моему брату",
                },
                {
                    "key": "Г",
                    "text": "моим братом",
                },
            ],
        },
        {
            "number": 6,
            "instruction": "Chọn đáp án đúng",
            "question": "У меня нет ______.",
            "translation": "Tôi không có vé.",
            "options": [
                {
                    "key": "A",
                    "text": "билет",
                },
                {
                    "key": "Б",
                    "text": "билета",
                },
                {
                    "key": "В",
                    "text": "билету",
                },
                {
                    "key": "Г",
                    "text": "билетом",
                },
            ],
        },
        {
            "number": 7,
            "instruction": "Chọn đáp án đúng",
            "question": "Каждый день я ______ в университет.",
            "translation": "Mỗi ngày tôi đi đến trường đại học.",
            "options": [
                {
                    "key": "A",
                    "text": "иду",
                },
                {
                    "key": "Б",
                    "text": "хожу",
                },
                {
                    "key": "В",
                    "text": "идёт",
                },
                {
                    "key": "Г",
                    "text": "ходит",
                },
            ],
        },
        {
            "number": 8,
            "instruction": "Chọn đáp án đúng",
            "question": "Вчера я ______ письмо.",
            "translation": "Hôm qua tôi đã viết một lá thư.",
            "options": [
                {
                    "key": "A",
                    "text": "пишу",
                },
                {
                    "key": "Б",
                    "text": "писал",
                },
                {
                    "key": "В",
                    "text": "напишу",
                },
                {
                    "key": "Г",
                    "text": "пишет",
                },
            ],
        },
        {
            "number": 9,
            "instruction": "Chọn đáp án đúng",
            "question": "Мы встречаемся ______ вокзале.",
            "translation": "Chúng tôi gặp nhau ở nhà ga.",
            "options": [
                {
                    "key": "A",
                    "text": "в",
                },
                {
                    "key": "Б",
                    "text": "на",
                },
                {
                    "key": "В",
                    "text": "к",
                },
                {
                    "key": "Г",
                    "text": "из",
                },
            ],
        },
        {
            "number": 10,
            "instruction": "Chọn đáp án đúng",
            "question": "Я купил билет ______ поезд.",
            "translation": "Tôi đã mua vé tàu.",
            "options": [
                {
                    "key": "A",
                    "text": "в",
                },
                {
                    "key": "Б",
                    "text": "из",
                },
                {
                    "key": "В",
                    "text": "на",
                },
                {
                    "key": "Г",
                    "text": "к",
                },
            ],
        },
    ]

    try:
        question_number = int(
            request.GET.get(
                "q",
                "1",
            )
        )

    except (
        TypeError,
        ValueError,
    ):
        question_number = 1

    if not (
        1
        <= question_number
        <= len(demo_questions)
    ):
        raise Http404(
            "Không tìm thấy câu hỏi."
        )

    current_question = demo_questions[
        question_number - 1
    ]

    # Demo trạng thái các câu đã làm.
    answered_numbers = {
        1,
        2,
        3,
        4,
        6,
        7,
        9,
    }

    question_grid = []

    for number in range(
        1,
        11,
    ):
        question_grid.append(
            {
                "number": number,
                "answered": (
                    number
                    in answered_numbers
                ),
                "current": (
                    number
                    == question_number
                ),
            }
        )

    unanswered_numbers = [
        number
        for number in range(1, 11)
        if number not in answered_numbers
    ]

    context = {
        "display_name": display_name,

        "question": current_question,

        "question_number": question_number,

        "question_total": len(
            demo_questions
        ),

        "previous_number": (
            question_number - 1
            if question_number > 1
            else None
        ),

        "next_number": (
            question_number + 1
            if question_number < 10
            else None
        ),

        "question_grid": question_grid,

        "answered_count": len(
            answered_numbers
        ),

        "unanswered_numbers": (
            unanswered_numbers
        ),
    }

    return render(
        request,
        "UI/test_doing.html",
        context,
    )

# ============================================================
# TEST HISTORY — DEMO DATA
# ============================================================

def _demo_test_history_data():
    """
    Dữ liệu demo cho UI lịch sử bài test.

    Sau này thay bằng AssessmentSession,
    SessionQuestion và QuestionAttempt thật.
    """

    question_bank = [
        {
            "number": 1,
            "prompt": "Я живу в ______.",
            "translation": "Tôi sống ở Moskva.",
            "correct_answer": "Москве",
            "wrong_answer": "Москва",
            "skill": "Предложный падеж",
            "explanation": (
                "Sau giới từ в khi nói về địa điểm, "
                "Москва chuyển thành Москве."
            ),
        },
        {
            "number": 2,
            "prompt": "Это ______ книга.",
            "translation": "Đây là một quyển sách mới.",
            "correct_answer": "новая",
            "wrong_answer": "новый",
            "skill": "Род прилагательных",
            "explanation": (
                "книга là danh từ giống cái nên "
                "tính từ phải dùng dạng новая."
            ),
        },
        {
            "number": 3,
            "prompt": "Мы ______ русский язык.",
            "translation": "Chúng tôi học tiếng Nga.",
            "correct_answer": "учим",
            "wrong_answer": "учу",
            "skill": "Настоящее время",
            "explanation": (
                "Chủ ngữ мы dùng động từ chia "
                "ở ngôi thứ nhất số nhiều: учим."
            ),
        },
        {
            "number": 4,
            "prompt": "Она идёт ______ университет.",
            "translation": "Cô ấy đang đi đến trường đại học.",
            "correct_answer": "в",
            "wrong_answer": "на",
            "skill": "Предлоги движения",
            "explanation": (
                "Với университет, dùng в khi diễn tả "
                "hướng chuyển động đến một địa điểm."
            ),
        },
        {
            "number": 5,
            "prompt": "Я не вижу ______ в этом магазине.",
            "translation": (
                "Tôi không thấy anh trai mình "
                "trong cửa hàng này."
            ),
            "correct_answer": "моего брата",
            "wrong_answer": "мой брат",
            "skill": "Винительный падеж",
            "explanation": (
                "Đối tượng chỉ người sau вижу "
                "dùng dạng đối cách: моего брата."
            ),
        },
        {
            "number": 6,
            "prompt": "У меня нет ______.",
            "translation": "Tôi không có vé.",
            "correct_answer": "билета",
            "wrong_answer": "билет",
            "skill": "Родительный падеж",
            "explanation": (
                "Sau нет, danh từ chuyển sang "
                "sinh cách: билета."
            ),
        },
        {
            "number": 7,
            "prompt": "Каждый день я ______ в университет.",
            "translation": (
                "Mỗi ngày tôi đi đến trường đại học."
            ),
            "correct_answer": "хожу",
            "wrong_answer": "иду",
            "skill": "Глаголы движения",
            "explanation": (
                "Hành động lặp lại thường xuyên "
                "dùng ходить: каждый день я хожу."
            ),
        },
        {
            "number": 8,
            "prompt": "Вчера я ______ письмо.",
            "translation": (
                "Hôm qua tôi đã viết một lá thư."
            ),
            "correct_answer": "писал",
            "wrong_answer": "пишу",
            "skill": "Прошедшее время",
            "explanation": (
                "Вчера chỉ thời gian quá khứ nên "
                "dùng писал."
            ),
        },
        {
            "number": 9,
            "prompt": "Мы встречаемся ______ вокзале.",
            "translation": "Chúng tôi gặp nhau ở nhà ga.",
            "correct_answer": "на",
            "wrong_answer": "в",
            "skill": "Предложный падеж",
            "explanation": (
                "Cách dùng thông thường là "
                "на вокзале."
            ),
        },
        {
            "number": 10,
            "prompt": "Я купил билет ______ поезд.",
            "translation": "Tôi đã mua vé tàu.",
            "correct_answer": "на",
            "wrong_answer": "в",
            "skill": "Предлоги",
            "explanation": (
                "Cấu trúc đúng là билет на поезд."
            ),
        },
    ]


    test_specs = [
        {
            "id": 101,
            "date": "01/10/2026",
            "duration": "6′42″",
            "wrong_numbers": {
                2,
                5,
            },
            "blank_numbers": {
                8,
            },
        },
        {
            "id": 102,
            "date": "28/09/2026",
            "duration": "5′58″",
            "wrong_numbers": {
                4,
                8,
            },
            "blank_numbers": set(),
        },
        {
            "id": 103,
            "date": "25/09/2026",
            "duration": "7′10″",
            "wrong_numbers": {
                1,
                3,
                6,
            },
            "blank_numbers": {
                9,
            },
        },
        {
            "id": 104,
            "date": "20/09/2026",
            "duration": "5′41″",
            "wrong_numbers": {
                7,
            },
            "blank_numbers": set(),
        },
    ]


    test_history = []


    for spec in test_specs:
        review_questions = []


        for source_question in question_bank:
            question = dict(
                source_question
            )

            number = question["number"]


            if number in spec["blank_numbers"]:
                status = "blank"
                status_label = "Bỏ trống"
                user_answer = (
                    "(chưa trả lời)"
                )

            elif number in spec["wrong_numbers"]:
                status = "wrong"
                status_label = "Sai"
                user_answer = question[
                    "wrong_answer"
                ]

            else:
                status = "correct"
                status_label = "Đúng"
                user_answer = question[
                    "correct_answer"
                ]


            question.update(
                {
                    "status": status,
                    "status_label": (
                        status_label
                    ),
                    "user_answer": (
                        user_answer
                    ),
                }
            )


            review_questions.append(
                question
            )


        correct_count = sum(
            1
            for question in review_questions
            if question["status"] == "correct"
        )

        wrong_count = sum(
            1
            for question in review_questions
            if question["status"] == "wrong"
        )

        blank_count = sum(
            1
            for question in review_questions
            if question["status"] == "blank"
        )


        test_history.append(
            {
                "id": spec["id"],
                "date": spec["date"],
                "duration": spec["duration"],

                "score": correct_count,
                "total": len(
                    review_questions
                ),

                "percent": round(
                    correct_count
                    / len(review_questions)
                    * 100
                ),

                "correct": correct_count,
                "wrong": wrong_count,
                "blank": blank_count,

                "questions": (
                    review_questions
                ),
            }
        )


    return test_history


# ALPHABET — STATIC LEARNING PAGE

@login_required
def alphabet(request):
    return render(
        request,
        "UI/alphabet.html",
        {
            "display_name": _get_display_name(request.user),
        },
    )


# ============================================================
# VOCABULARY UI
# ============================================================

@login_required
@ensure_csrf_cookie
def vocabulary(request):
    level = (
        request.GET.get("level", "A1")
        .strip()
        .upper()
    )

    if level not in ("A1", "A2", "B1"):
        level = "A1"

    return render(
        request,
        "UI/vocabulary.html",
        {
            "display_name": _get_display_name(request.user),
            "selected_level": level,
        },
    )
