from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
import os

# =========================
# CONFIGURACIÓN DE BASE DE DATOS
# =========================
# Usa la URL de Supabase/Postgres si está en entorno de producción, sino SQLite local para desarrollo
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:miprimerproyecto@db.kbllizenngnezwhlbcww.supabase.co:5432/postgres"
)

# Crear el engine de SQLAlchemy
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    pool_pre_ping=True
)

# Crear sesión
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

# Base para definir modelos
Base = declarative_base()

# Dependencia para FastAPI
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
