import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

load_dotenv()

# =====================================================
# DATABASE URL
# =====================================================

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

if not DATABASE_URL:
    # Fallback seguro para desarrollo: SQLite en archivo local
    DATABASE_URL = "sqlite:///./mapalocal.db"

# Corrección para PostgreSQL moderno
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# =====================================================
# Engine config (ajustable por entorno)
# =====================================================

DB_ECHO = os.getenv("DB_ECHO", "0") == "1"
DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "10"))
DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "20"))
DB_POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "300"))

# Conexión
connect_args = {}
engine_kwargs = {
    "echo": DB_ECHO,
    "pool_pre_ping": True,
}

# Ajustes específicos por dialecto
if DATABASE_URL.startswith("sqlite"):
    # SQLite no usa los parámetros de pool como Postgres; requiere check_same_thread=False para FastAPI
    connect_args = {"check_same_thread": False}
    engine_kwargs["connect_args"] = connect_args
else:
    # Solo aplica pool_size y max_overflow en motores no SQLite
    engine_kwargs.update({
        "pool_recycle": DB_POOL_RECYCLE,
        "pool_size": DB_POOL_SIZE,
        "max_overflow": DB_MAX_OVERFLOW,
    })

engine = create_engine(DATABASE_URL, **engine_kwargs)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,  # evita expirar objetos tras commit; útil para respuestas API
    bind=engine,
)

Base = declarative_base()

# =====================================================
# DEPENDENCIA DB
# =====================================================

def get_db():
    """
    Provee una sesión por request.
    Hace rollback si ocurre una excepción durante el manejo del request,
    y cierra siempre la sesión al finalizar.
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        # Evita dejar la sesión en estado inválido
        db.rollback()
        raise
    finally:
        db.close()