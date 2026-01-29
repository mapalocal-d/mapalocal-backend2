from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Boolean, JSON
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import relationship
from sqlalchemy.types import TypeDecorator, CHAR
from datetime import datetime
import uuid 

from database import Base

# =========================
# TRADUCTOR DE UUID
# =========================
class GUID(TypeDecorator):
    """Traductor: Usa UUID en Postgres (Railway) y Texto en SQLite (Local)"""
    impl = CHAR
    cache_ok = True
    def load_dialect_impl(self, dialect):
        if dialect.name == 'postgresql':
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        else:
            return dialect.type_descriptor(CHAR(32))

    def process_bind_param(self, value, dialect):
        if value is None: return value
        return str(value).replace('-', '')

    def process_result_value(self, value, dialect):
        if value is None: return value
        return uuid.UUID(value)

# =========================
# MODELOS DE BASE DE DATOS
# =========================

class Usuario(Base):
    __tablename__ = "usuarios"
    id = Column(GUID(), primary_key=True, default=uuid.uuid4, index=True)
    correo = Column(String, unique=True, index=True, nullable=False)
    nombre = Column(String, nullable=False)
    contrasena = Column(String, nullable=False)
    rol = Column(String, nullable=False, default="USUARIO") 
    creado_en = Column(DateTime, default=datetime.utcnow)

    locales = relationship("Local", back_populates="dueno")
    favoritos = relationship("Favorito", back_populates="usuario")

class Local(Base):
    __tablename__ = "locales"
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, nullable=False)
    tipo = Column(String, nullable=False) 
    categoria = Column(String, nullable=False) 
    descripcion = Column(String, default="Sin descripción")
    ciudad = Column(String, nullable=False)
    latitud = Column(Float, nullable=True)
    longitud = Column(Float, nullable=True)
    
    # Datos de Contacto y Fotos
    whatsapp = Column(String, nullable=True)
    maps_link = Column(String, nullable=True)
    foto1_url = Column(String, nullable=True)
    foto2_url = Column(String, nullable=True)
    
    # Horarios y Estado
    horarios = Column(JSON, nullable=True)
    pago_al_dia = Column(Boolean, default=False) 
    fecha_vencimiento = Column(DateTime, nullable=True)
    
    # Métricas
    visitas = Column(Integer, default=0)
    clics_whatsapp = Column(Integer, default=0)
    clics_maps = Column(Integer, default=0)

    # Relaciones
    dueno_id = Column(GUID(), ForeignKey("usuarios.id"))
    dueno = relationship("Usuario", back_populates="locales")
    ofertas = relationship("Oferta", back_populates="local", cascade="all, delete-orphan")
    resenas = relationship("Resena", back_populates="local", cascade="all, delete-orphan")
    seguidores = relationship("Favorito", back_populates="local", cascade="all, delete-orphan")

class Oferta(Base):
    __tablename__ = "ofertas"
    id = Column(Integer, primary_key=True, index=True)
    titulo = Column(String, nullable=False)
    precio = Column(String, nullable=False)
    descripcion = Column(String)
    imagen_url = Column(String)
    creada_en = Column(DateTime, default=datetime.utcnow)
    fecha_fin = Column(DateTime, nullable=True)

    local_id = Column(Integer, ForeignKey("locales.id"))
    local = relationship("Local", back_populates="ofertas")
    dueno_id = Column(GUID(), ForeignKey("usuarios.id"))

class Resena(Base):
    __tablename__ = "resenas"
    id = Column(Integer, primary_key=True, index=True)
    estrellas = Column(Integer, nullable=False)
    comentario = Column(String)
    creada_en = Column(DateTime, default=datetime.utcnow)
    local_id = Column(Integer, ForeignKey("locales.id"))
    local = relationship("Local", back_populates="resenas")

class Favorito(Base):
    __tablename__ = "favoritos"
    id = Column(Integer, primary_key=True, index=True)
    usuario_id = Column(GUID(), ForeignKey("usuarios.id"))
    local_id = Column(Integer, ForeignKey("locales.id"))
    
    usuario = relationship("Usuario", back_populates="favoritos")
    local = relationship("Local", back_populates="seguidores")