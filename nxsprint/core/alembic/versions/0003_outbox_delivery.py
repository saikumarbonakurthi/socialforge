"""outbox delivery tracking

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # batch mode so the foreign keys also work on SQLite (dev)
    with op.batch_alter_table("outbox") as batch:
        batch.add_column(sa.Column("project_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("nudge_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("leased_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("attempts", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("last_error", sa.Text(), nullable=True))
        batch.create_foreign_key("fk_outbox_project", "project", ["project_id"], ["id"])
        batch.create_foreign_key("fk_outbox_nudge", "nudge", ["nudge_id"], ["id"])
        batch.create_index("ix_outbox_nudge_id", ["nudge_id"])


def downgrade() -> None:
    with op.batch_alter_table("outbox") as batch:
        batch.drop_index("ix_outbox_nudge_id")
        batch.drop_constraint("fk_outbox_nudge", type_="foreignkey")
        batch.drop_constraint("fk_outbox_project", type_="foreignkey")
        for col in ("last_error", "delivered_at", "attempts", "leased_at", "nudge_id", "project_id"):
            batch.drop_column(col)
