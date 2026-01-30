"""Add job_offers and payment_events tables (Postgres UUID)

Revision ID: 20260130_add_joboffer_paymentevent_postgres
Revises: 
Create Date: 2026-01-30 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260130_add_joboffer_paymentevent_postgres"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    # ---- JobOffer table ----
    op.create_table(
        "job_offers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("local_id", sa.Integer, sa.ForeignKey("locales.id", ondelete="SET NULL"), nullable=True),
        sa.Column("dueno_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True),
        sa.Column("titulo", sa.String(length=255), nullable=False),
        sa.Column("descripcion", sa.Text, nullable=True),
        sa.Column("categoria", sa.String(length=120), nullable=False),
        sa.Column("salario", sa.String(length=120), nullable=True),
        sa.Column("contacto", sa.String(length=255), nullable=True),
        sa.Column("imagen_url", sa.String(length=1024), nullable=True),
        sa.Column("creada_en", sa.DateTime, nullable=True),
        sa.Column("fecha_fin", sa.DateTime, nullable=True),
        sa.Column("activa", sa.Boolean, nullable=True, server_default=sa.text("false")),
        sa.Column("oferta_del_dia", sa.Boolean, nullable=True, server_default=sa.text("false")),
    )
    op.create_index("ix_job_offers_categoria", "job_offers", ["categoria"])
    op.create_index("ix_job_offers_fecha_fin", "job_offers", ["fecha_fin"])
    op.create_index("ix_job_offers_dueno_id", "job_offers", ["dueno_id"])
    op.create_index("ix_job_offers_local_id", "job_offers", ["local_id"])

    # ---- PaymentEvent table ----
    op.create_table(
        "payment_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("payment_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_payment_events_payment_id", "payment_events", ["payment_id"], unique=True)


def downgrade():
    op.drop_index("ix_payment_events_payment_id", table_name="payment_events")
    op.drop_table("payment_events")

    op.drop_index("ix_job_offers_local_id", table_name="job_offers")
    op.drop_index("ix_job_offers_dueno_id", table_name="job_offers")
    op.drop_index("ix_job_offers_fecha_fin", table_name="job_offers")
    op.drop_index("ix_job_offers_categoria", table_name="job_offers")
    op.drop_table("job_offers")
    