from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID 
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid 

from database import Base

class Usuario(Base):
    __tablename__ = "usuarios"
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
    whatsapp = Column(String) # <-- AGREGADO: Para que el cliente te contacte
    modo = Column(String, default="AUTO")
    abierto = Column(Integer, default=1) # 1 para abierto, 0 para cerrado

    dueno_id = Column(UUID(as_uuid=True), ForeignKey("usuarios.id"))
    dueno = relationship("Usuario", back_populates="locales")
    
    # Relación para encontrar las ofertas de este local específico
    ofertas = relationship("Oferta", back_populates="local")


class Oferta(Base):
    __tablename__ = "ofertas"
    id = Column(Integer, primary_key=True, index=True)
    titulo = Column(String, nullable=False)
    precio = Column(String, nullable=False)
    descripcion = Column(String)
    imagen_url = Column(String)
    creada_en = Column(DateTime, default=datetime.utcnow)

    # CAMBIO: Conectamos la oferta al Local directamente
    local_id = Column(Integer, ForeignKey("locales.id"))
    local = relationship("Local", back_populates="ofertas")
    
    dueno_id = Column(UUID(as_uuid=True), ForeignKey("usuarios.id"))