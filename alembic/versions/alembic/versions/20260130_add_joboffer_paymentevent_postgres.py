from alembic import op

# Ajusta estos identificadores según el nombre real de tu archivo
revision = "20260131_cleanup_indexes"
down_revision = "20260130_add_joboffer_paymentevent_postgres"
branch_labels = None
depends_on = None

def upgrade():
    # payment_events: ya tenemos UNIQUE en payment_id desde el modelo; elimina índice nombrado si existe
    try:
        op.drop_index("ix_payment_events_payment_id", table_name="payment_events")
    except Exception:
        pass

    # job_offers: elimina índices simples redundantes si existen (los gestionará Alembic según index=True)
    for idx_name in [
        "ix_job_offers_dueno_id",
        "ix_job_offers_local_id",
        "ix_job_offers_categoria",
        "ix_job_offers_fecha_fin",
    ]:
        try:
            op.drop_index(idx_name, table_name="job_offers")
        except Exception:
            pass

    # Mantener el índice compuesto que sí usamos
    # Si no existe aún, puedes crearlo (no falla si ya existe en la base por create_all)
    try:
        op.create_index("ix_job_offers_activa_ciudad", "job_offers", ["activa", "ciudad"])
    except Exception:
        pass

def downgrade():
    # No recreamos índices redundantes en downgrade
    pass