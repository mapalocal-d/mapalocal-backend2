import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

load_dotenv()

# Obtenemos la URL de la base de datos de las variables de entorno
DATABASE_URL = os.getenv("DATABASE_URL")

# Si no hay URL (en local), usamos SQLite por defecto
if not DATABASE_URL:
    DATABASE_URL = "sqlite:///./mapalocal.db"

# Si usamos PostgreSQL (Supabase), corregimos el prefijo si es necesario
# SQLAlchemy 1.4+ requiere 'postgresql://' en lugar de 'postgres://'
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Configuración de argumentos de conexión
connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

# Creamos el motor con pool_pre_ping para evitar desconexiones en Supabase/Railway
engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args=connect_args,
    pool_pre_ping=True,  # Verifica si la conexión sigue viva antes de usarla
    pool_recycle=300     # Reinicia conexiones cada 5 minutos
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()