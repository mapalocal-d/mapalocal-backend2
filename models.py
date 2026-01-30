from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Boolean, JSON, Index, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import relationship
from sqlalchemy.types import TypeDecorator, CHAR
from datetime import datetime
import uuid

from database import Base

# =====================================================
# UUID HÍBRIDO (POSTGRES / SQLITE)
# =====================================================

class GUID(TypeDecorator):
    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(32))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        return str(value).replace("-", "")

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        return uuid.UUID(value)

# =====================================================
# USUARIOS
# =====================================================

class Usuario(Base):
    __tablename__ = "usuarios"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4, index=True)
    correo = Column(String, unique=True, index=True, nullable=False)
    nombre = Column(String, nullable=False)
    contrasena = Column(String, nullable=False)
    # Main espera roles "USUARIO" | "DUENO"
    rol = Column(String, default="USUARIO", index=True)
    creado_en = Column(DateTime, default=datetime.utcnow)

    locales = relationship("Local", back_populates="dueno")
    favoritos = relationship("Favorito", back_populates="usuario")
    jobs = relationship("JobOffer", back_populates="dueno", cascade="all, delete", passive_deletes=True)

# =====================================================
# LOCALES / SERVICIOS
# =====================================================

class Local(Base):
    __tablename__ = "locales"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, nullable=False)
    # Main usa tipos texto como "LOCALES ESTABLECIDOS" y "SERVICIOS (A DOMICILIO)"
    tipo = Column(String, nullable=False)
    categoria = Column(String, nullable=False)
    subcategoria = Column(String, nullable=True)

    descripcion = Column(String, default="Sin descripción")
    ciudad = Column(String, nullable=False, index=True)
    latitud = Column(Float)
    longitud = Column(Float)

    whatsapp = Column(String)
    maps_link = Column(String)
    foto1_url = Column(String)
    foto2_url = Column(String)

    horarios = Column(JSON)

    pago_al_dia = Column(Boolean, default=False, index=True)
    fecha_vencimiento = Column(DateTime, index=True)

    visitas = Column(Integer, default=0)
    clics_whatsapp = Column(Integer, default=0)
    clics_maps = Column(Integer, default=0)

    # Campos de plan requeridos por el main mejorado
    hot_slots_remaining = Column(Integer, default=0)
    analytics_enabled = Column(Boolean, default=False)
    plan_type = Column(String, nullable=True)

    dueno_id = Column(GUID(), ForeignKey("usuarios.id", ondelete="SET NULL"))
    dueno = relationship("Usuario", back_populates="locales")

    ofertas = relationship("Oferta", back_populates="local", cascade="all, delete")
    resenas = relationship("Resena", back_populates="local", cascade="all, delete")
    seguidores = relationship("Favorito", back_populates="local", cascade="all, delete")
    job_offers = relationship("JobOffer", back_populates="local", cascade="all, delete", passive_deletes=True)

    __table_args__ = (
        Index("ix_locales_ciudad", "ciudad"),
        Index("ix_locales_tipo", "tipo"),
        Index("ix_locales_categoria", "categoria"),
        Index("ix_locales_pago_venc", "pago_al_dia", "fecha_vencimiento"),
    )

# =====================================================
# OFERTAS
# =====================================================

class Oferta(Base):
    __tablename__ = "ofertas"

    id = Column(Integer, primary_key=True, index=True)
    titulo = Column(String, nullable=False)
    precio = Column(Float, nullable=False)  # Cambiado a Float (migración pendiente)
    descripcion = Column(String)
    imagen_url = Column(String)

    creada_en = Column(DateTime, default=datetime.utcnow)
    fecha_fin = Column(DateTime)

    local_id = Column(Integer, ForeignKey("locales.id"))
    local = relationship("Local", back_populates="ofertas")

    dueno_id = Column(GUID(), ForeignKey("usuarios.id"))

# =====================================================
# RESEÑAS
# =====================================================

class Resena(Base):
    __tablename__ = "resenas"

    id = Column(Integer, primary_key=True, index=True)
    estrellas = Column(Integer, nullable=False)
    comentario = Column(String)
    creada_en = Column(DateTime, default=datetime.utcnow)

    local_id = Column(Integer, ForeignKey("locales.id"))
    usuario_id = Column(GUID(), ForeignKey("usuarios.id"), nullable=True)
    local = relationship("Local", back_populates="resenas")

# =====================================================
# FAVORITOS
# =====================================================

class Favorito(Base):
    __tablename__ = "favoritos"

    id = Column(Integer, primary_key=True, index=True)
    usuario_id = Column(GUID(), ForeignKey("usuarios.id"))
    local_id = Column(Integer, ForeignKey("locales.id"))

    usuario = relationship("Usuario", back_populates="favoritos")
    local = relationship("Local", back_populates="seguidores")

    __table_args__ = (
        UniqueConstraint("usuario_id", "local_id", name="uq_usuario_local"),
        Index("ix_favoritos_usuario_id", "usuario_id"),
        Index("ix_favoritos_local_id", "local_id"),
    )

# =====================================================
# RESET PASSWORD
# =====================================================

class PasswordReset(Base):
    __tablename__ = "password_resets"

    id = Column(Integer, primary_key=True, index=True)
    correo = Column(String, index=True)
    codigo = Column(String)
    expira_en = Column(DateTime)
    # Límite de intentos de verificación (protección fuerza bruta)
    intentos = Column(Integer, default=0)

# =====================================================
# OFERTAS DE TRABAJO (JobOffer)
# =====================================================

class JobOffer(Base):
    __tablename__ = "job_offers"

    id = Column(Integer, primary_key=True, index=True)
    local_id = Column(Integer, ForeignKey("locales.id", ondelete="SET NULL"), nullable=True, index=True)
    dueno_id = Column(GUID(), ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True, index=True)

    titulo = Column(String(255), nullable=False)
    descripcion = Column(Text, nullable=True)
    categoria = Column(String(120), nullable=False, index=True)
    salario = Column(String(120), nullable=True)
    contacto = Column(String(255), nullable=True)
    imagen_url = Column(String(1024), nullable=True)
    ciudad = Column(String(120), nullable=True, index=True)  # campo ciudad para filtros

    creada_en = Column(DateTime, nullable=True)
    fecha_fin = Column(DateTime, nullable=True, index=True)
    activa = Column(Boolean, default=False, index=True)
    oferta_del_dia = Column(Boolean, default=False, index=True)

    # Relaciones
    local = relationship("Local", back_populates="job_offers")
    dueno = relationship("Usuario", back_populates="jobs")

    __table_args__ = (
        Index("ix_job_offers_categoria", "categoria"),
        Index("ix_job_offers_fecha_fin", "fecha_fin"),
        Index("ix_job_offers_dueno_id", "dueno_id"),
        Index("ix_job_offers_local_id", "local_id"),
        Index("ix_job_offers_activa_ciudad", "activa", "ciudad"),
    )

# =====================================================
# EVENTS DE PAGOS (idempotencia)
# =====================================================

class PaymentEvent(Base):
    __tablename__ = "payment_events"

    id = Column(Integer, primary_key=True, index=True)
    payment_id = Column(String(255), nullable=False, unique=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_payment_events_payment_id", "payment_id", unique=True),
    )