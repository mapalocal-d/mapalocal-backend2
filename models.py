## models.py

from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID # Necesario para Postgres/Supabase
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid # Para generar IDs automáticos

from database import Base

class Usuario(Base):
    __tablename__ = "usuarios"

    # Usamos UUID para que coincida con el estándar de Supabase y sea seguro
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    correo = Column(String, unique=True, index=True, nullable=False)
    nombre = Column(String, nullable=False)
    contrasena = Column(String, nullable=False)
    rol = Column(String, nullable=False)
    creado_en = Column(DateTime, default=datetime.utcnow)

    locales = relationship("Local", back_populates="dueno")


class Local(Base):
    __tablename__ = "locales"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, nullable=False)
    categoria = Column(String, nullable=False)
    ciudad = Column(String, nullable=False)
    latitud = Column(Float, nullable=False)
    longitud = Column(Float, nullable=False)
    hora_apertura = Column(String)
    hora_cierre = Column(String)
    modo = Column(String, default="AUTO")
    abierto = Column(Integer, default=0)

    # Este campo DEBE ser UUID para que la clave extranjera funcione con Usuario.id
    dueno_id = Column(UUID(as_uuid=True), ForeignKey("usuarios.id"))
    dueno = relationship("Usuario", back_populates="locales")


class Oferta(Base):
    __tablename__ = "ofertas"

    id = Column(Integer, primary_key=True, index=True)
    titulo = Column(String, nullable=False)
    precio = Column(String, nullable=False)
    descripcion = Column(String)
    imagen_url = Column(String)
    creada_en = Column(DateTime, default=datetime.utcnow)

    # Aquí también usamos UUID para mantener la consistencia
    dueno_id = Column(UUID(as_uuid=True), ForeignKey("usuarios.id"))