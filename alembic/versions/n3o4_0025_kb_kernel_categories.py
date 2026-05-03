"""Default kb.always_inject=true для категорий rule/preference/alias.

Фикс UX-регрессии после 0024: бот сохраняет новый факт через
`knowledge_teach` intent с `always_inject=false` (server_default false),
и поведенческие правила (типа «не спрашивай про доли») попадают в
кэш-блок только когда юзер вручную поднимет флаг. Lazy-lookup их
не подцепит без триггера → правило мёртвое до следующего совпадения.

Решение: для категорий, которые по своей семантике должны влиять на
КАЖДЫЙ ответ бота (rule/preference/alias), `always_inject` дефолтится в
true. Категории справочного характера (entity/glossary/pattern) остаются
по-прежнему: попадают в кэш только если им вручную выставили true.

Параллельно — backfill: все active rule/preference/alias переводим в
always_inject=true (раз уж семантика теперь такая). entity/glossary/
pattern не трогаем — у них уже свой backfill из 0024 (glossary all-in,
preference all-in, остальные — индивидуально).

Revision ID: n3o40025kbcat
Revises: m2n30024kbflag
Create Date: 2026-05-03
"""

from __future__ import annotations

from alembic import op

revision = "n3o40025kbcat"
down_revision = "m2n30024kbflag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Backfill (idempotent): все active rule/preference/alias → kernel.
    op.execute(
        """
        UPDATE knowledge_base
           SET always_inject = true
         WHERE is_active = true
           AND category IN ('rule', 'preference', 'alias')
           AND always_inject = false
        """
    )

    # Триггер: все будущие INSERT-ы в этих категориях получают true,
    # если caller явно не передал false. Колонка всё ещё может быть
    # явно false (для устаревших правил типа shares-disabled-temp,
    # которые юзер сам захочет в lazy).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION kb_default_always_inject()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.category IN ('rule', 'preference', 'alias')
               AND NEW.always_inject IS NOT DISTINCT FROM false THEN
                NEW.always_inject := true;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_kb_default_always_inject ON knowledge_base;
        CREATE TRIGGER trg_kb_default_always_inject
        BEFORE INSERT ON knowledge_base
        FOR EACH ROW EXECUTE FUNCTION kb_default_always_inject();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_kb_default_always_inject ON knowledge_base")
    op.execute("DROP FUNCTION IF EXISTS kb_default_always_inject()")
    # Не откатываем backfill — это не safe, юзер может уже зависеть от
    # always_inject=true на новых категориях.
