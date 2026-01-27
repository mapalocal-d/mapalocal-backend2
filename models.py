from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Boolean
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import relationship
from sqlalchemy.types import TypeDecorator, CHAR
from datetime import datetime
import uuid 

from database import Base

# ========================================================
# TRADUCTOR DE UUID (Para que funcione en Local y Railway)
# ========================================================
class GUID(TypeDecorator):
    """Traductor: Usa UUID en Postgres y Texto en SQLite"""
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

# ========================================================
# MODELOS DE BASE DE DATOS
# ========================================================

class Usuario(Base):
    __tablename__ = "usuarios"
    # Usamos GUID() en lugar de UUID para compatibilidad total
    id = Column(GUID(), primary_key=True, default=uuid.uuid4, index=True)
    correo = Column(String, unique=True, index=True, nullable=False)
    nombre = Column(String, nullable=False)
    contrasena = Column(String, nullable=False)
    rol = Column(String, nullable=False, default="USUARIO") # DUENO o USUARIO
    creado_en = Column(DateTime, default=datetime.utcnow)

    locales = relationship("Local", back_populates="dueno")

class Local(Base):
    __tablename__ = "locales"
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, nullable=False)
    
    # Categorización InDriver
    tipo = Column(String, nullable=False) # "LOCALES" o "SERVICIOS (A DOMICILIO)"
    categoria = Column(String, nullable=False) 
    descripcion = Column(String, default="Sin descripción")
    ciudad = Column(String, nullable=False)
    
    # Ubicación (Opcional para servicios a domicilio)
    latitud = Column(Float, nullable=True, default=0.0)
    longitud = Column(Float, nullable=True, default=0.0)
    
    hora_apertura = Column(String, default="09:00")
    hora_cierre = Column(String, default="20:00")
    whatsapp = Column(String) 
    
    # Sistema de suscripción de $2.000
    pago_al_dia = Column(Boolean, default=False) 
    fecha_vencimiento = Column(DateTime, nullable=True)

    modo = Column(String, default="AUTO")
    abierto = Column(Integer, default=1) 

    # Relación con el Dueño
    dueno_id = Column(GUID(), ForeignKey("usuarios.id"))
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

    # Relación con el Local
    local_id = Column(Integer, ForeignKey("locales.id"))
    local = relationship("Local", back_populates="ofertas")
    
    # Relación directa con el Dueño (útil para validaciones rápidas)
    dueno_id = Column(GUID(), ForeignKey("usuarios.id"))