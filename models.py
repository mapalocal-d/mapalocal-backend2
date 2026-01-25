from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime

from database import Base

class Usuario(Base):
    __tablename__ = "usuarios"

    id = Column(Integer, primary_key=True, index=True)
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

    dueno_id = Column(Integer, ForeignKey("usuarios.id"))
    dueno = relationship("Usuario", back_populates="locales")


class Oferta(Base):
    __tablename__ = "ofertas"

    id = Column(Integer, primary_key=True, index=True)
    titulo = Column(String, nullable=False)
    precio = Column(String, nullable=False)
    descripcion = Column(String)
    imagen_url = Column(String)
    creada_en = Column(DateTime, default=datetime.utcnow)

    dueno_id = Column(Integer, ForeignKey("usuarios.id"))
