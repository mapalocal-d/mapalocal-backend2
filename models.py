from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Boolean
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
    # ROL: Para diferenciar Cliente de Dueño (Modo InDriver)
    rol = Column(String, nullable=False, default="USUARIO") 
    creado_en = Column(DateTime, default=datetime.utcnow)

    locales = relationship("Local", back_populates="dueno")


class Local(Base):
    __tablename__ = "locales"
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, nullable=False)
    
    # NUEVOS CAMPOS PARA CATEGORÍAS Y SERVICIOS
    tipo = Column(String, nullable=False) # "LOCALES" o "SERVICIOS (A DOMICILIO)"
    categoria = Column(String, nullable=False) # Ej: "Gásfiter" o "Barbería"
    descripcion = Column(String, default="Sin descripción")
    
    ciudad = Column(String, nullable=False)
    
    # Coordenadas: Ahora son opcionales (Float) para que los servicios no fallen si no tienen local
    latitud = Column(Float, nullable=True, default=0.0)
    longitud = Column(Float, nullable=True, default=0.0)
    
    hora_apertura = Column(String, default="09:00")
    hora_cierre = Column(String, default="20:00")
    whatsapp = Column(String) 
    
    # SISTEMA DE PAGO AUTOMÁTICO ($2.000)
    pago_al_dia = Column(Boolean, default=False) 
    fecha_vencimiento = Column(DateTime, nullable=True)

    modo = Column(String, default="AUTO")
    abierto = Column(Integer, default=1) 

    dueno_id = Column(UUID(as_uuid=True), ForeignKey("usuarios.id"))
    dueno = relationship("Usuario", back_populates="locales")
    
    ofertas = relationship("Oferta", back_populates="local", cascade="all, delete-orphan")


class Oferta(Base):
    __tablename__ = "ofertas"
    id = Column(Integer, primary_key=True, index=True)
    titulo = Column(String, nullable=False)
    precio = Column(String, nullable=False)
    descripcion = Column(String)
    imagen_url = Column(String)
    creada_en = Column(DateTime, default=datetime.utcnow)

    local_id = Column(Integer, ForeignKey("locales.id"))
    local = relationship("Local", back_populates="ofertas")
    
    dueno_id = Column(UUID(as_uuid=True), ForeignKey("usuarios.id"))