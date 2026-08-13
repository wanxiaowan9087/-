"""persist structured robot recommendations on assistant messages"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260813_0005"
down_revision = "20260813_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {column["name"] for column in inspect(op.get_bind()).get_columns("messages")}
    if "product_recommendations" not in existing:
        op.add_column("messages", sa.Column("product_recommendations", sa.JSON(), nullable=True))


def downgrade() -> None:
    existing = {column["name"] for column in inspect(op.get_bind()).get_columns("messages")}
    if "product_recommendations" in existing:
        op.drop_column("messages", "product_recommendations")
