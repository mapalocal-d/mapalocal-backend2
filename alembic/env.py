import os
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

# Importa Base de tus modelos para que Alembic conozca todas las tablas
from models import Base

# Config de Alembic
config = context.config

# Logging de Alembic según alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata objetivo (todas las tablas de tu app)
target_metadata = Base.metadata

def get_url() -> str:
    """
    Obtiene la URL de la base desde la variable de entorno DATABASE_URL.
    Corrige postgres:// a postgresql:// si es necesario.
    """
    url = os.getenv("DATABASE_URL", "")
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url

def run_migrations_offline():
    """
    Ejecuta migraciones en modo 'offline' (sin conexión directa),
    útil para generar scripts SQL. Usa la URL del entorno.
    """
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online():
    """
    Ejecuta migraciones en modo 'online' (conexión activa).
    """
    url = get_url()
    # Inyecta la URL al config
    config.set_main_option("sqlalchemy.url", url)

    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()

# Selecciona modo offline/online según invocación
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()