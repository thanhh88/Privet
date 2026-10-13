"""
Canonical curriculum taxonomy for Privet v1.

This module is the source of truth for seedable Topic and Skill metadata.

Important:
- Database PKs are surrogate identifiers.
- Skill.skill_code is the stable semantic identifier.
- Existing skill codes must never be reused for a different meaning.
- Skill.cefr_level is a pedagogical anchor, not a hard constraint on
  Question.cefr_level.
- B1 extension questions do not change the anchor of a skill that also
  belongs to the A1-A2 core.
"""


TOPICS = [
    # Grammar
    {
        "topic_name": "Именительный падеж и согласование",
        "category": "grammar",
        "display_order": 1,
    },
    {
        "topic_name": "Родительный падеж",
        "category": "grammar",
        "display_order": 2,
    },
    {
        "topic_name": "Дательный падеж",
        "category": "grammar",
        "display_order": 3,
    },
    {
        "topic_name": "Винительный падеж",
        "category": "grammar",
        "display_order": 4,
    },
    {
        "topic_name": "Творительный падеж",
        "category": "grammar",
        "display_order": 5,
    },
    {
        "topic_name": "Предложный падеж",
        "category": "grammar",
        "display_order": 6,
    },
    {
        "topic_name": "Глагольная система и НСВ/СВ",
        "category": "grammar",
        "display_order": 7,
    },
    {
        "topic_name": "Глаголы движения",
        "category": "grammar",
        "display_order": 8,
    },
    {
        "topic_name": "Сложное предложение",
        "category": "grammar",
        "display_order": 9,
    },

    # Vocabulary
    {
        "topic_name": "Личная информация и семья",
        "category": "vocabulary",
        "display_order": 10,
    },
    {
        "topic_name": "Учёба и работа: основы",
        "category": "vocabulary",
        "display_order": 11,
    },
    {
        "topic_name": "Дом и город: основы",
        "category": "vocabulary",
        "display_order": 12,
    },
    {
        "topic_name": "Еда и покупки: основы",
        "category": "vocabulary",
        "display_order": 13,
    },
    {
        "topic_name": "Транспорт, путешествия и природа",
        "category": "vocabulary",
        "display_order": 14,
    },
    {
        "topic_name": "Время, тело и повседневность",
        "category": "vocabulary",
        "display_order": 15,
    },
    {
        "topic_name": "Досуг, общение и технологии",
        "category": "vocabulary",
        "display_order": 16,
    },
    {
        "topic_name": "Личная жизнь и отношения",
        "category": "vocabulary",
        "display_order": 17,
    },
    {
        "topic_name": "Дом и еда: расширение",
        "category": "vocabulary",
        "display_order": 18,
    },
    {
        "topic_name": "Город, услуги и поездки",
        "category": "vocabulary",
        "display_order": 19,
    },
    {
        "topic_name": "Учёба и профессии: расширение",
        "category": "vocabulary",
        "display_order": 20,
    },
    {
        "topic_name": "Здоровье, одежда и покупки",
        "category": "vocabulary",
        "display_order": 21,
    },
    {
        "topic_name": "Культура, природа и общение",
        "category": "vocabulary",
        "display_order": 22,
    },
    {
        "topic_name": "Учёба, работа и развитие",
        "category": "vocabulary",
        "display_order": 23,
    },
    {
        "topic_name": "Общество, мнение и абстрактные понятия",
        "category": "vocabulary",
        "display_order": 24,
    },
]


SKILLS = [
    # T1 — Именительный падеж и согласование
    {
        "skill_code": "NOM-01",
        "topic_name": "Именительный падеж и согласование",
        "skill_name": "Согласование прилагательных с существительными",
        "cefr_level": "A1",
        "is_extension": False,
        "display_order": 1,
    },
    {
        "skill_code": "NOM-02",
        "topic_name": "Именительный падеж и согласование",
        "skill_name": (
            "Согласование притяжательных и указательных местоимений"
        ),
        "cefr_level": "A1",
        "is_extension": False,
        "display_order": 2,
    },
    {
        "skill_code": "NOM-03",
        "topic_name": "Именительный падеж и согласование",
        "skill_name": "Профессии и роли в именительном падеже",
        "cefr_level": "A1",
        "is_extension": False,
        "display_order": 3,
    },
    {
        "skill_code": "NOM-04",
        "topic_name": "Именительный падеж и согласование",
        "skill_name": "Согласование подлежащего и сказуемого",
        "cefr_level": "A1",
        "is_extension": False,
        "display_order": 4,
    },

    # T2 — Родительный падеж
    {
        "skill_code": "GEN-01",
        "topic_name": "Родительный падеж",
        "skill_name": (
            "Отсутствие: нет / не было / не будет + родительный"
        ),
        "cefr_level": "A1",
        "is_extension": False,
        "display_order": 1,
    },
    {
        "skill_code": "GEN-02",
        "topic_name": "Родительный падеж",
        "skill_name": "Принадлежность и наличие: у + родительный",
        "cefr_level": "A1",
        "is_extension": False,
        "display_order": 2,
    },
    {
        "skill_code": "GEN-03",
        "topic_name": "Родительный падеж",
        "skill_name": (
            "Направление «откуда?»: из / с / от + родительный"
        ),
        "cefr_level": "A2",
        "is_extension": False,
        "display_order": 3,
    },
    {
        "skill_code": "GEN-04",
        "topic_name": "Родительный падеж",
        "skill_name": (
            "Количество: много / мало / несколько / сколько"
        ),
        "cefr_level": "A1",
        "is_extension": False,
        "display_order": 4,
    },
    {
        "skill_code": "GEN-05",
        "topic_name": "Родительный падеж",
        "skill_name": "Числительные и родительный падеж",
        "cefr_level": "A1",
        "is_extension": False,
        "display_order": 5,
    },
    {
        "skill_code": "GEN-06",
        "topic_name": "Родительный падеж",
        "skill_name": "Принадлежность и даты в родительном падеже",
        "cefr_level": "A2",
        "is_extension": False,
        "display_order": 6,
    },

    # T3 — Дательный падеж
    {
        "skill_code": "DAT-01",
        "topic_name": "Дательный падеж",
        "skill_name": "Адресат и получатель действия",
        "cefr_level": "A1",
        "is_extension": False,
        "display_order": 1,
    },
    {
        "skill_code": "DAT-02",
        "topic_name": "Дательный падеж",
        "skill_name": "Безличные конструкции и выражение возраста",
        "cefr_level": "A1",
        "is_extension": False,
        "display_order": 2,
    },
    {
        "skill_code": "DAT-03",
        "topic_name": "Дательный падеж",
        "skill_name": "Направление к кому / чему: к + дательный",
        "cefr_level": "A1",
        "is_extension": False,
        "display_order": 3,
    },
    {
        "skill_code": "DAT-04",
        "topic_name": "Дательный падеж",
        "skill_name": "Глагольное управление с дательным падежом",
        "cefr_level": "A2",
        "is_extension": False,
        "display_order": 4,
    },

    # T4 — Винительный падеж
    {
        "skill_code": "ACC-01",
        "topic_name": "Винительный падеж",
        "skill_name": (
            "Прямой объект: неодушевлённые существительные"
        ),
        "cefr_level": "A1",
        "is_extension": False,
        "display_order": 1,
    },
    {
        "skill_code": "ACC-02",
        "topic_name": "Винительный падеж",
        "skill_name": "Прямой объект: одушевлённые существительные",
        "cefr_level": "A1",
        "is_extension": False,
        "display_order": 2,
    },
    {
        "skill_code": "ACC-03",
        "topic_name": "Винительный падеж",
        "skill_name": (
            "Согласование определений в винительном падеже"
        ),
        "cefr_level": "A2",
        "is_extension": False,
        "display_order": 3,
    },
    {
        "skill_code": "ACC-04",
        "topic_name": "Винительный падеж",
        "skill_name": (
            "Направление «куда?»: в / на + винительный"
        ),
        "cefr_level": "A1",
        "is_extension": False,
        "display_order": 4,
    },
    {
        "skill_code": "ACC-05",
        "topic_name": "Винительный падеж",
        "skill_name": "Время и длительность в винительном падеже",
        "cefr_level": "A2",
        "is_extension": False,
        "display_order": 5,
    },

    # T5 — Творительный падеж
    {
        "skill_code": "INS-01",
        "topic_name": "Творительный падеж",
        "skill_name": "Совместность: с / со + творительный",
        "cefr_level": "A2",
        "is_extension": False,
        "display_order": 1,
    },
    {
        "skill_code": "INS-02",
        "topic_name": "Творительный падеж",
        "skill_name": (
            "Профессия и роль после работать / стать / быть"
        ),
        "cefr_level": "A2",
        "is_extension": False,
        "display_order": 2,
    },
    {
        "skill_code": "INS-03",
        "topic_name": "Творительный падеж",
        "skill_name": (
            "Глагольное и именное управление с творительным"
        ),
        "cefr_level": "A2",
        "is_extension": False,
        "display_order": 3,
    },
    {
        "skill_code": "INS-04",
        "topic_name": "Творительный падеж",
        "skill_name": "Пространственные предлоги с творительным",
        "cefr_level": "A2",
        "is_extension": False,
        "display_order": 4,
    },

    # T6 — Предложный падеж
    {
        "skill_code": "PREP-01",
        "topic_name": "Предложный падеж",
        "skill_name": (
            "Местонахождение «где?»: в / на + предложный"
        ),
        "cefr_level": "A1",
        "is_extension": False,
        "display_order": 1,
    },
    {
        "skill_code": "PREP-02",
        "topic_name": "Предложный падеж",
        "skill_name": (
            "Объект речи и мысли: о / об + предложный"
        ),
        "cefr_level": "A1",
        "is_extension": False,
        "display_order": 2,
    },
    {
        "skill_code": "PREP-03",
        "topic_name": "Предложный падеж",
        "skill_name": (
            "Географические названия в предложном падеже"
        ),
        "cefr_level": "A1",
        "is_extension": False,
        "display_order": 3,
    },
    {
        "skill_code": "PREP-04",
        "topic_name": "Предложный падеж",
        "skill_name": (
            "Время: месяц, год и неделя в предложном падеже"
        ),
        "cefr_level": "A1",
        "is_extension": False,
        "display_order": 4,
    },
    {
        "skill_code": "PREP-05",
        "topic_name": "Предложный падеж",
        "skill_name": (
            "Согласование прилагательных в предложном падеже"
        ),
        "cefr_level": "A2",
        "is_extension": False,
        "display_order": 5,
    },

    # T7 — Глагольная система и НСВ/СВ
    {
        "skill_code": "VERB-01",
        "topic_name": "Глагольная система и НСВ/СВ",
        "skill_name": "Настоящее время: лицо и число",
        "cefr_level": "A1",
        "is_extension": False,
        "display_order": 1,
    },
    {
        "skill_code": "VERB-02",
        "topic_name": "Глагольная система и НСВ/СВ",
        "skill_name": "Прошедшее время: род и число",
        "cefr_level": "A1",
        "is_extension": False,
        "display_order": 2,
    },
    {
        "skill_code": "VERB-03",
        "topic_name": "Глагольная система и НСВ/СВ",
        "skill_name": "Будущее время НСВ и СВ",
        "cefr_level": "A2",
        "is_extension": False,
        "display_order": 3,
    },
    {
        "skill_code": "VERB-04",
        "topic_name": "Глагольная система и НСВ/СВ",
        "skill_name": "Инфинитив и повелительное наклонение",
        "cefr_level": "A1",
        "is_extension": False,
        "display_order": 4,
    },
    {
        "skill_code": "VERB-05",
        "topic_name": "Глагольная система и НСВ/СВ",
        "skill_name": (
            "Вид глагола: процесс, повторяемость и результат"
        ),
        "cefr_level": "A2",
        "is_extension": False,
        "display_order": 5,
    },
    {
        "skill_code": "VERB-06",
        "topic_name": "Глагольная система и НСВ/СВ",
        "skill_name": (
            "Вид глагола в контексте и возвратные формы"
        ),
        "cefr_level": "A2",
        "is_extension": False,
        "display_order": 6,
    },

    # T8 — Глаголы движения
    {
        "skill_code": "MOVE-01",
        "topic_name": "Глаголы движения",
        "skill_name": "идти / ходить",
        "cefr_level": "A1",
        "is_extension": False,
        "display_order": 1,
    },
    {
        "skill_code": "MOVE-02",
        "topic_name": "Глаголы движения",
        "skill_name": "ехать / ездить",
        "cefr_level": "A1",
        "is_extension": False,
        "display_order": 2,
    },
    {
        "skill_code": "MOVE-03",
        "topic_name": "Глаголы движения",
        "skill_name": "пойти / поехать",
        "cefr_level": "A2",
        "is_extension": False,
        "display_order": 3,
    },
    {
        "skill_code": "MOVE-04",
        "topic_name": "Глаголы движения",
        "skill_name": "Приставочные глаголы движения",
        "cefr_level": "A2",
        "is_extension": False,
        "display_order": 4,
    },
    {
        "skill_code": "MOVE-05",
        "topic_name": "Глаголы движения",
        "skill_name": (
            "нести / носить, вести / водить, везти / возить"
        ),
        "cefr_level": "B1",
        "is_extension": True,
        "display_order": 5,
    },

    # T9 — Сложное предложение
    {
        "skill_code": "SENT-01",
        "topic_name": "Сложное предложение",
        "skill_name": "Относительные предложения с который",
        "cefr_level": "A2",
        "is_extension": False,
        "display_order": 1,
    },
    {
        "skill_code": "SENT-02",
        "topic_name": "Сложное предложение",
        "skill_name": "Придаточные с что и чтобы",
        "cefr_level": "A2",
        "is_extension": False,
        "display_order": 2,
    },
    {
        "skill_code": "SENT-03",
        "topic_name": "Сложное предложение",
        "skill_name": (
            "Причина и следствие: потому что / так как / поэтому"
        ),
        "cefr_level": "A2",
        "is_extension": False,
        "display_order": 3,
    },
    {
        "skill_code": "SENT-04",
        "topic_name": "Сложное предложение",
        "skill_name": "Условие, время и уступка",
        "cefr_level": "A2",
        "is_extension": False,
        "display_order": 4,
    },
    {
        "skill_code": "SENT-05",
        "topic_name": "Сложное предложение",
        "skill_name": "Косвенный вопрос и частица ли",
        "cefr_level": "B1",
        "is_extension": True,
        "display_order": 5,
    },

    # Vocabulary
    {
        "skill_code": "VOC-01",
        "topic_name": "Личная информация и семья",
        "skill_name": "Лексика: личная информация и семья",
        "cefr_level": "A1",
        "is_extension": False,
        "display_order": 1,
    },
    {
        "skill_code": "VOC-02",
        "topic_name": "Учёба и работа: основы",
        "skill_name": "Лексика: учёба и работа — основы",
        "cefr_level": "A1",
        "is_extension": False,
        "display_order": 1,
    },
    {
        "skill_code": "VOC-03",
        "topic_name": "Дом и город: основы",
        "skill_name": "Лексика: дом и город — основы",
        "cefr_level": "A1",
        "is_extension": False,
        "display_order": 1,
    },
    {
        "skill_code": "VOC-04",
        "topic_name": "Еда и покупки: основы",
        "skill_name": "Лексика: еда и покупки — основы",
        "cefr_level": "A1",
        "is_extension": False,
        "display_order": 1,
    },
    {
        "skill_code": "VOC-05",
        "topic_name": "Транспорт, путешествия и природа",
        "skill_name": (
            "Лексика: транспорт, путешествия и природа"
        ),
        "cefr_level": "A1",
        "is_extension": False,
        "display_order": 1,
    },
    {
        "skill_code": "VOC-06",
        "topic_name": "Время, тело и повседневность",
        "skill_name": (
            "Лексика: время, тело и повседневность"
        ),
        "cefr_level": "A1",
        "is_extension": False,
        "display_order": 1,
    },
    {
        "skill_code": "VOC-07",
        "topic_name": "Досуг, общение и технологии",
        "skill_name": (
            "Лексика: досуг, общение и технологии"
        ),
        "cefr_level": "A1",
        "is_extension": False,
        "display_order": 1,
    },
    {
        "skill_code": "VOC-08",
        "topic_name": "Личная жизнь и отношения",
        "skill_name": "Лексика: личная жизнь и отношения",
        "cefr_level": "A2",
        "is_extension": False,
        "display_order": 1,
    },
    {
        "skill_code": "VOC-09",
        "topic_name": "Дом и еда: расширение",
        "skill_name": "Лексика: дом и еда — расширение",
        "cefr_level": "A2",
        "is_extension": False,
        "display_order": 1,
    },
    {
        "skill_code": "VOC-10",
        "topic_name": "Город, услуги и поездки",
        "skill_name": "Лексика: город, услуги и поездки",
        "cefr_level": "A2",
        "is_extension": False,
        "display_order": 1,
    },
    {
        "skill_code": "VOC-11",
        "topic_name": "Учёба и профессии: расширение",
        "skill_name": (
            "Лексика: учёба и профессии — расширение"
        ),
        "cefr_level": "A2",
        "is_extension": False,
        "display_order": 1,
    },
    {
        "skill_code": "VOC-12",
        "topic_name": "Здоровье, одежда и покупки",
        "skill_name": (
            "Лексика: здоровье, одежда и покупки"
        ),
        "cefr_level": "A2",
        "is_extension": False,
        "display_order": 1,
    },
    {
        "skill_code": "VOC-13",
        "topic_name": "Культура, природа и общение",
        "skill_name": (
            "Лексика: культура, природа и общение"
        ),
        "cefr_level": "A2",
        "is_extension": False,
        "display_order": 1,
    },
    {
        "skill_code": "VOC-14",
        "topic_name": "Учёба, работа и развитие",
        "skill_name": "Лексика: учёба, работа и развитие",
        "cefr_level": "B1",
        "is_extension": True,
        "display_order": 1,
    },
    {
        "skill_code": "VOC-15",
        "topic_name": (
            "Общество, мнение и абстрактные понятия"
        ),
        "skill_name": (
            "Лексика: общество, мнение и абстрактные понятия"
        ),
        "cefr_level": "B1",
        "is_extension": True,
        "display_order": 1,
    },
]