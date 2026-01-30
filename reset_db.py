import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine("postgresql://postgres.kbllizenngnezwhlbcww:miprimerproyecto@aws-1-us-east-2.pooler.supabase.com:5432/postgres")

def reset():
    with engine.connect() as conn:
        print("--- Iniciando limpieza de base de datos ---")
        # Borramos las tablas en orden para evitar errores de llaves foráneas
        conn.execute(text("DROP TABLE IF EXISTS ofertas CASCADE;"))
        conn.execute(text("DROP TABLE IF EXISTS locales CASCADE;"))
        conn.execute(text("DROP TABLE IF EXISTS usuarios CASCADE;"))
        conn.commit()
        print("--- ¡Tablas borradas exitosamente! ---")

if __name__ == "__main__":
    reset()