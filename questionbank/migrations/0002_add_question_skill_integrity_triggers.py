from django.db import migrations


FORWARD_SQL = """
CREATE OR REPLACE FUNCTION validate_question_skill_integrity(
    p_question_id bigint
)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    v_primary_count integer;
    v_weight_sum numeric;
BEGIN
    -- Nếu Question đã bị xóa thì không cần kiểm tra nữa.
    IF NOT EXISTS (
        SELECT 1
        FROM question
        WHERE question_id = p_question_id
    ) THEN
        RETURN;
    END IF;

    SELECT
        COUNT(*) FILTER (WHERE is_primary = TRUE),
        COALESCE(SUM(weight), 0)
    INTO
        v_primary_count,
        v_weight_sum
    FROM question_skill
    WHERE question_id = p_question_id;

    IF v_primary_count <> 1 THEN
        RAISE EXCEPTION
            'Question % must have exactly one primary skill; found %',
            p_question_id,
            v_primary_count
            USING ERRCODE = '23514';
    END IF;

    IF ABS(v_weight_sum - 1.0) > 0.001 THEN
        RAISE EXCEPTION
            'Question % skill weights must sum to 1 ± 0.001; found %',
            p_question_id,
            v_weight_sum
            USING ERRCODE = '23514';
    END IF;
END;
$$;


CREATE OR REPLACE FUNCTION trg_check_question_skill_integrity()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        PERFORM validate_question_skill_integrity(
            OLD.question_id
        );

        RETURN OLD;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        PERFORM validate_question_skill_integrity(
            NEW.question_id
        );

        IF OLD.question_id IS DISTINCT FROM NEW.question_id THEN
            PERFORM validate_question_skill_integrity(
                OLD.question_id
            );
        END IF;

        RETURN NEW;
    END IF;

    PERFORM validate_question_skill_integrity(
        NEW.question_id
    );

    RETURN NEW;
END;
$$;


CREATE CONSTRAINT TRIGGER trg_qs_integrity_deferred
AFTER INSERT OR UPDATE OR DELETE
ON question_skill
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION trg_check_question_skill_integrity();


CREATE OR REPLACE FUNCTION trg_check_question_has_skill()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM validate_question_skill_integrity(
        NEW.question_id
    );

    RETURN NEW;
END;
$$;


CREATE CONSTRAINT TRIGGER trg_question_skill_required_deferred
AFTER INSERT
ON question
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION trg_check_question_has_skill();
"""


REVERSE_SQL = """
DROP TRIGGER IF EXISTS
    trg_question_skill_required_deferred
    ON question;

DROP TRIGGER IF EXISTS
    trg_qs_integrity_deferred
    ON question_skill;

DROP FUNCTION IF EXISTS
    trg_check_question_has_skill();

DROP FUNCTION IF EXISTS
    trg_check_question_skill_integrity();

DROP FUNCTION IF EXISTS
    validate_question_skill_integrity(bigint);
"""


class Migration(migrations.Migration):

    dependencies = [
        ("questionbank", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql=FORWARD_SQL,
            reverse_sql=REVERSE_SQL,
        ),
    ]