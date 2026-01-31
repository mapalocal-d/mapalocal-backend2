# MapaLocal API - Versión mejorada y robusta para producción
# - Seguridad: SECRET_KEY obligatoria, HMAC webhook robusto, CORS seguro
# - Validaciones: ciudad/categoría accent-aware, teléfono E.164, maps_link
# - Imágenes: Pillow requerido, re-encode/remueve EXIF
# - Transacciones: with db.begin(), with_for_update() en hot_slots/suscripciones
# - Webhook: idempotencia con PaymentEvent y procesamiento atómico (UNIQUE + IntegrityError)
# - BackgroundTasks: fuera de la transacción
# - Analytics: registro de eventos y endpoints protegidos por plan
# - Suscripciones y pagos (MercadoPago SDK + fallback HTTP)
# - Autenticación: registro/login/reset con bcrypt y JWT, limitación de intentos de reset
# - Búsquedas y cercanos, favoritos, trabajos
# - Rate limiting opcional para /local/subir-foto (fastapi-limiter si está disponible)
# - NOTA: Requiere columnas y constraints en modelos:
#   - PasswordReset.intentos (int, default 0)
#   - PaymentEvent.payment_id UNIQUE
#   - Usuario.correo UNIQUE
#   - Favorito(usuario_id, local_id) UNIQUE
#   - Índices recomendados en Local(ciudad, tipo, categoria), JobOffer(ciudad, categoria, activa+fecha_fin), AnalyticsEvent(local_id, created_at)

from fastapi import FastAPI, HTTPException, Depends, Request, UploadFile, File, Query, BackgroundTasks
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field, validator
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
import jwt
import pytz
import os
import mercadopago
import math
import uuid
import re
import logging
import io
import hmac
import hashlib
import requests
import difflib
import unicodedata
import secrets

import cloudinary
import cloudinary.uploader

from sqlalchemy.orm import Session
from sqlalchemy import desc, func, or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from database import get_db, engine, Base
from models import Usuario, Local, Oferta, Favorito, Resena, JobOffer, PaymentEvent, PasswordReset,AnalyticsEvent

# Opcional: Pillow y phonenumbers (exigidos en startup para producción segura)
try:
    from PIL import Image
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False

try:
    import phonenumbers
    PHONENUM_AVAILABLE = True
except Exception:
    PHONENUM_AVAILABLE = False

# Rate limiting opcional (fastapi-limiter + redis)
try:
    from fastapi_limiter import FastAPILimiter
    from fastapi_limiter.depends import RateLimiter
    import redis.asyncio as redis
    LIMITER_AVAILABLE = True
except Exception:
    LIMITER_AVAILABLE = False
    RateLimiter = None

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mapalocal")

# =========================
# CONFIG
# =========================

CLAVE_SECRETA = os.getenv("SECRET_KEY")
if not CLAVE_SECRETA:
    raise RuntimeError("SECRET_KEY no configurada en el entorno")

ALGORITMO = "HS256"
MINUTOS_TOKEN_DEFAULT = int(os.getenv("MINUTOS_TOKEN_DEFAULT", 60 * 24))      # 1 día
MINUTOS_TOKEN_DUENO = int(os.getenv("MINUTOS_TOKEN_DUENO", 60 * 24 * 7))     # 7 d��as
ZONA_HORARIA = pytz.timezone(os.getenv("TZ", "America/Santiago"))

MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN")
if not MP_ACCESS_TOKEN:
    raise RuntimeError("MP_ACCESS_TOKEN no configurado en el entorno")
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)

MP_WEBHOOK_KEY = os.getenv("MP_WEBHOOK_KEY", None)
ALLOW_INSECURE_WEBHOOKS = os.getenv("ALLOW_INSECURE_WEBHOOKS", "0") == "1"

# Cloudinary
CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME")
CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET")
CLOUDINARY_CONFIGURED = False
if CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET:
    cloudinary.config(cloud_name=CLOUDINARY_CLOUD_NAME, api_key=CLOUDINARY_API_KEY, api_secret=CLOUDINARY_API_SECRET, secure=True)
    CLOUDINARY_CONFIGURED = True
    logger.info("Cloudinary configurado")
else:
    logger.info("Cloudinary NO configurado; se usará almacenamiento local para uploads")

app = FastAPI(title="MapaLocal API - Producción")

# Static folder
app.mount("/static", StaticFiles(directory="static"), name="static")
os.makedirs("static/uploads", exist_ok=True)

# CORS (credenciales requieren orígenes expl��citos, no '*')
origins_env = os.getenv("ALLOWED_ORIGINS", "")
allow_origins = [o.strip() for o in origins_env.split(",") if o.strip() and o.strip() != "*"]
if not allow_origins:
    # Fallback seguro para desarrollo
    allow_origins = ["http://localhost:3000", "http://127.0.0.1:3000"]
app.add_middleware(CORSMiddleware, allow_origins=allow_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# =========================
# CONSTANTS: categories, cities, plans
# =========================

CATEGORIAS_MASTER = {
    "LOCALES ESTABLECIDOS": [
        "Almacén / Minimarket (General)", "Almacén / Minimarket (con CajaVecina)",
        "Botillería (General)", "Botillería (con CajaVecina)", "Carnicería",
        "Panadería / Pastelería", "Frutería y Verdulería", "Fiambrería",
        "Comida Rápida (Local)", "Restaurante / Cafetería", "Centro Médico",
        "Psicólogo (Consulta)", "Psiquiatra (Consulta)", "Kinesiólogo (Centro)",
        "Oftalmólogo / Óptica", "Clínica Dental", "Farmacia", "Veterinaria",
        "Barbería", "Peluquería Dama", "Centro de Estética", "Estudio de Tatuajes", "Manicure",
        "Ferretería", "Venta de Gas", "Venta de Agua Purificada", "Bazar / Paquetería", "Venta de Leña",
        "Librería", "Zapatería", "Repuestos Automotriz", "Ropa y Accesorios", "Electrónica y Celulares"
    ],
    "SERVICIOS (A DOMICILIO)": [
        "Gásfiter", "Electricista", "Maestro Constructor", "Pintor", "Cerrajero",
        "Técnico de Lavadoras", "Técnico de Refrigeración", "Limpieza de Alfombras / Sillones",
        "Clases Particulares", "Enfermería a Domicilio", "Kinesiólogo a Domicilio",
        "Fonoaudiólogo", "Personal Trainer", "Psicólogo (Online/Domicilio)",
        "Mecánico a Domicilio", "Lavado de Autos (Detailing)", "Grúa / Asistencia en Ruta",
        "Paseador de Perros", "Cuidado de Adultos Mayores", "Niñera (Babysitter)", "Fumigación"
    ]
}
ALL_CATEGORIES = [c for sub in CATEGORIAS_MASTER.values() for c in sub]

JOB_CATEGORIES = ["Ventas", "Atención al Cliente", "Cocina", "Limpieza", "Transporte", "Construcción", "Salud", "Educación", "Administración", "Tecnología", "Oficios", "Belleza", "Logística", "Otro"]

CITIES_BY_REGION = {
    "II": {"region_name": "Antofagasta", "ciudades": ["Antofagasta", "Calama", "Tocopilla", "Mejillones", "Taltal", "María Elena"]},
    "III": {"region_name": "Atacama", "ciudades": ["Copiapó", "Caldera", "Chañaral", "Diego de Almagro", "Vallenar", "Tierra Amarilla"]},
    "IV": {"region_name": "Coquimbo", "ciudades": ["La Serena", "Coquimbo", "Ovalle", "Illapel", "Andacollo", "Los Vilos", "La Higuera", "Combarbalá"]}
}
ALL_CITIES_CANONICAL = [c for r in CITIES_BY_REGION.values() for c in r["ciudades"]]
ALLOWED_CITIES = {unicodedata.normalize("NFKD", c).casefold() for c in ALL_CITIES_CANONICAL}

PLAN_PRICES = {"LOCAL_BASIC": 2500, "LOCAL_PRO": 3300, "SERV_BASIC": 3000, "SERV_PRO": 3700}
PLAN_HOT_SLOTS = {"LOCAL_BASIC": 1, "LOCAL_PRO": 2, "SERV_BASIC": 1, "SERV_PRO": 2}

PRICE_PUBLICAR_TRABAJO = 2000

# =========================
# UTIL: text normalization (accent-aware)
# =========================

def norm_text_for_compare(s: Optional[str]) -> str:
    if not s:
        return ""
    s2 = s.strip()
    s2 = unicodedata.normalize("NFKD", s2)
    s2 = "".join(ch for ch in s2 if not unicodedata.combining(ch))
    return s2.casefold()

def normalize_choice(value: Optional[str], choices: List[str]) -> Optional[str]:
    if not value:
        return None
    vnorm = norm_text_for_compare(value)
    for c in choices:
        if norm_text_for_compare(c) == vnorm:
            return c
    return None

def normalize_city(city: Optional[str]) -> Optional[str]:
    return normalize_choice(city, ALL_CITIES_CANONICAL)

def valid_city(city: Optional[str]) -> bool:
    return normalize_city(city) is not None

# =========================
# UTIL: phones (phonenumbers E.164) and maps link validation
# =========================

def normalize_phone_e164(phone: Optional[str], default_region: str = "CL") -> Optional[str]:
    if not phone:
        return None
    if PHONENUM_AVAILABLE:
        try:
            p = phonenumbers.parse(phone, default_region)
            if not phonenumbers.is_valid_number(p):
                return None
            e164 = phonenumbers.format_number(p, phonenumbers.PhoneNumberFormat.E164)
            return e164.lstrip("+")
        except Exception:
            return None
    # fallback mínimo
    digits = re.sub(r"\D+", "", phone)
    if 8 <= len(digits) <= 15:
        return digits
    return None

def valid_maps_link(url: Optional[str]) -> bool:
    if not url:
        return False
    try:
        from urllib.parse import urlparse
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return False
        host = (p.netloc or "").lower()
        return any(domain in host for domain in ("google.com", "maps.app.goo.gl", "openstreetmap.org"))
    except Exception:
        return False

# =========================
# UTIL: images (Pillow requerido)
# =========================

ALLOWED_EXT = {"png", "jpg", "jpeg", "gif", "webp"}
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", 5 * 1024 * 1024))

def validate_image_bytes(content: bytes):
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="Archivo demasiado grande")
    if not PIL_AVAILABLE:
        raise HTTPException(status_code=500, detail="Pillow es requerido para manejo seguro de imágenes")
    try:
        img = Image.open(io.BytesIO(content))
        img.verify()
    except Exception:
        raise HTTPException(status_code=400, detail="Archivo no es una imagen válida")

def sanitize_image_and_get_bytes(content: bytes, ext_hint: Optional[str] = "jpg") -> bytes:
    validate_image_bytes(content)
    try:
        img = Image.open(io.BytesIO(content))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        out = io.BytesIO()
        fmt = "JPEG" if (ext_hint or "jpg").lower() in ("jpg", "jpeg") else (ext_hint or "png").upper()
        if fmt not in ("JPEG", "PNG", "WEBP"):
            fmt = "JPEG"
        img.save(out, format=fmt, quality=85)  # re-encode to strip EXIF
        out.seek(0)
        return out.read()
    except Exception:
        raise HTTPException(status_code=400, detail="Error procesando imagen")

def save_upload_file_local(upload_file: UploadFile, dest_folder: str = "static/uploads") -> str:
    try:
        upload_file.file.seek(0)
    except Exception:
        pass
    filename = upload_file.filename or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    if ext not in ALLOWED_EXT:
        raise HTTPException(status_code=400, detail="Extensión no permitida")
    content = upload_file.file.read()
    content = sanitize_image_and_get_bytes(content, ext_hint=ext)
    safe_name = f"{uuid.uuid4().hex}.{ext}"
    os.makedirs(dest_folder, exist_ok=True)
    path = os.path.join(dest_folder, safe_name)
    with open(path, "wb") as f:
        f.write(content)
    return f"/{path.replace(os.sep, '/')}"

def upload_to_cloudinary(upload_file: UploadFile) -> str:
    try:
        upload_file.file.seek(0)
    except Exception:
        pass
    content = upload_file.file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Archivo vacío")
    filename = upload_file.filename or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    if ext not in ALLOWED_EXT:
        raise HTTPException(status_code=400, detail="Extensión no permitida")
    content = sanitize_image_and_get_bytes(content, ext_hint=ext)
    public_id = uuid.uuid4().hex
    try:
        result = cloudinary.uploader.upload(io.BytesIO(content), public_id=public_id, resource_type="image", overwrite=False, folder="mapalocal")
        secure_url = result.get("secure_url") or result.get("url")
        if not secure_url:
            logger.exception("Cloudinary no devolvió secure_url: %s", result)
            raise HTTPException(status_code=500, detail="Error subiendo imagen")
        return secure_url
    except Exception as e:
        logger.exception("Error subiendo a Cloudinary: %s", e)
        raise HTTPException(status_code=500, detail="Error subiendo imagen a Cloudinary")

def save_upload_file_secure(upload_file: UploadFile) -> str:
    if CLOUDINARY_CONFIGURED:
        return upload_to_cloudinary(upload_file)
    else:
        return save_upload_file_local(upload_file)

# =========================
# TOKEN / AUTH
# =========================

oauth2 = OAuth2PasswordBearer(tokenUrl="/auth/login")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def encriptar(password: str) -> str:
    return pwd_context.hash(password)

def verificar(password: str, hash_guardado: str) -> bool:
    return pwd_context.verify(password, hash_guardado)

def crear_token_for_user(u: Usuario):
    minutos = MINUTOS_TOKEN_DUENO if getattr(u, "rol", "").upper() == "DUENO" else MINUTOS_TOKEN_DEFAULT
    exp = datetime.utcnow() + timedelta(minutes=minutos)
    payload = {"sub": u.correo, "rol": u.rol, "exp": exp}
    return jwt.encode(payload, CLAVE_SECRETA, algorithm=ALGORITMO)

def usuario_actual(token: str = Depends(oauth2), db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(token, CLAVE_SECRETA, algorithms=[ALGORITMO])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token inválido")
    correo = payload.get("sub")
    if not correo:
        raise HTTPException(status_code=401, detail="Token inválido")
    usuario = db.query(Usuario).filter(Usuario.correo == correo).first()
    if not usuario:
        raise HTTPException(status_code=401, detail="Usuario no encontrado")
    if payload.get("rol") != usuario.rol:
        raise HTTPException(status_code=401, detail="Token inválido (rol mismatch)")
    return usuario

# Reset codes con hash y límite de intentos
def make_reset_code() -> str:
    return secrets.token_hex(3).upper()

def hash_reset_code(code: str, salt: str) -> str:
    return hashlib.sha256((salt + code).encode()).hexdigest()

def store_reset_code(db: Session, correo: str, minutes: int = 30) -> str:
    code = make_reset_code()
    salt = secrets.token_hex(16)
    h = hash_reset_code(code, salt)
    composite = f"{salt}${h}"
    pr = PasswordReset(correo=correo, codigo=composite, expira_en=datetime.utcnow() + timedelta(minutes=minutes))
    # requiere columna intentos con default 0
    if hasattr(pr, "intentos"):
        pr.intentos = 0
    db.add(pr)
    db.commit()
    return code

def verify_reset_code(db: Session, correo: str, code: str) -> bool:
    pr = db.query(PasswordReset).filter(PasswordReset.correo == correo).order_by(desc(PasswordReset.expira_en)).first()
    if not pr or pr.expira_en < datetime.utcnow():
        return False
    # bloqueo por intentos
    current_intentos = getattr(pr, "intentos", 0)
    if current_intentos >= 5:
        return False
    try:
        salt, h = pr.codigo.split("$", 1)
    except Exception:
        return False
    ok = (hash_reset_code(code, salt) == h)
    # incrementa intentos si falla
    try:
        if hasattr(pr, "intentos") and not ok:
            pr.intentos = current_intentos + 1
            db.commit()
    except Exception:
        db.rollback()
    return ok

# =========================
# helpers: time and payment info
# =========================

def obtener_hora_chile() -> datetime:
    return datetime.now(ZONA_HORARIA)

def normalize_dt(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return ZONA_HORARIA.localize(dt)
    return dt.astimezone(ZONA_HORARIA)

def get_payment_info(payment_id: str) -> dict:
    try:
        resp = sdk.payment().get(payment_id)
        return resp.get("response", {})
    except Exception:
        try:
            headers = {"Authorization": f"Bearer {MP_ACCESS_TOKEN}"}
            r = requests.get(f"https://api.mercadopago.com/v1/payments/{payment_id}", headers=headers, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception:
            logger.exception("Error obteniendo pago de MercadoPago (SDK y fallback fallaron)")
            raise

# =========================
# SQLAlchemy serialization
# =========================

def sqlalchemy_to_dict(obj: Any) -> Dict[str, Any]:
    if obj is None:
        return {}
    data = {}
    for key, value in vars(obj).items():
        if key.startswith("_sa_instance_state"):
            continue
        if isinstance(value, datetime):
            try:
                val = value
                if val.tzinfo is None:
                    val = ZONA_HORARIA.localize(val)
                data[key] = val.isoformat()
            except Exception:
                data[key] = str(value)
        else:
            data[key] = value
    return data

# =========================
# Pydantic schemas
# =========================

class UsuarioRegistroSchema(BaseModel):
    correo: EmailStr
    nombre: str = Field(..., max_length=150)
    contrasena: str
    rol: str
    @validator("rol")
    def validar_rol(cls, v):
        val = (v or "").upper()
        if val not in ("USUARIO", "DUENO"):
            raise ValueError("rol inválido")
        return val

class TokenSchema(BaseModel):
    access_token: str
    token_type: str
    rol: str

class ResetRequestSchema(BaseModel):
    correo: EmailStr

class ResetPasswordSchema(BaseModel):
    correo: EmailStr
    codigo: str
    nueva_contrasena: str

class LocalCrearSchema(BaseModel):
    nombre: str
    tipo: str
    categoria: str
    ciudad: str
    latitud: Optional[float] = 0.0
    longitud: Optional[float] = 0.0
    whatsapp: Optional[str] = None
    maps_link: Optional[str] = None
    descripcion: Optional[str] = Field("Sin descripción", max_length=2000)
    foto1_url: Optional[str] = None
    foto2_url: Optional[str] = None
    horarios: Optional[Dict[str, Dict[str, Any]]] = None

class SubscribeRequestSchema(BaseModel):
    local_id: int
    plan: str

class TrabajoCrearSchema(BaseModel):
    titulo: str
    descripcion: Optional[str] = None
    categoria: str
    salario: Optional[str] = None
    contacto: Optional[str] = None
    oferta_del_dia: Optional[bool] = False
    ciudad: Optional[str] = None
    local_id: Optional[int] = None

# =========================
# STARTUP: enforce critical dependencies + opcional rate limiter (sin create_all en producción)
# =========================

@app.on_event("startup")
def check_critical_deps():
    missing = []
    if not PIL_AVAILABLE:
        missing.append("Pillow")
    if not PHONENUM_AVAILABLE:
        missing.append("phonenumbers")
    if missing:
        msg = f"Dependencias críticas faltantes: {', '.join(missing)}. Instálalas y reinicia. e.g. pip install pillow phonenumbers"
        logger.error(msg)
        raise RuntimeError(msg)
    # En PostgreSQL NO crear tablas automáticamente aquí.
    # Usa Alembic para manejar el esquema: 'alembic upgrade head' antes de iniciar Uvicorn.

@app.on_event("startup")
async def init_rate_limiter():
    if LIMITER_AVAILABLE:
        try:
            redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
            r = redis.from_url(redis_url, encoding="utf-8", decode_responses=True)
            await FastAPILimiter.init(r)
            logger.info("Rate limiter inicializado")
        except Exception:
            logger.warning("No se pudo inicializar FastAPI-Limiter; endpoint de subida sin rate limit")

# =========================
# ENDPOINTS: auth, config
# =========================

@app.post("/auth/registro")
def registro(usuario: UsuarioRegistroSchema, db: Session = Depends(get_db)):
    try:
        with db.begin():
            if db.query(Usuario).filter(Usuario.correo == usuario.correo.lower()).first():
                raise HTTPException(status_code=400, detail="Correo ya registrado")
            nuevo = Usuario(correo=usuario.correo.lower(), nombre=usuario.nombre, contrasena=encriptar(usuario.contrasena), rol=usuario.rol.upper())
            db.add(nuevo)
        return {"mensaje": "Ok"}
    except SQLAlchemyError:
        logger.exception("Error registrando usuario")
        raise HTTPException(status_code=500, detail="Error interno")

@app.post("/auth/login", response_model=TokenSchema)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    u = db.query(Usuario).filter(Usuario.correo == form.username.lower()).first()
    if not u or not verificar(form.password, u.contrasena):
        raise HTTPException(status_code=401, detail="Credenciales inválidas")
    token = crear_token_for_user(u)
    return {"access_token": token, "token_type": "bearer", "rol": u.rol}

@app.post("/auth/reset-request")
def reset_request(data: ResetRequestSchema, db: Session = Depends(get_db)):
    try:
        code = store_reset_code(db, data.correo, minutes=30)
        if os.getenv("ENV", "").lower() != "production":
            logger.info("Password reset code for %s : %s", data.correo, code)
        return {"mensaje": "Código enviado"}
    except Exception:
        logger.exception("Error creando password reset")
        raise HTTPException(status_code=500, detail="Error interno")

@app.post("/auth/reset-password")
def reset_password(data: ResetPasswordSchema, db: Session = Depends(get_db)):
    try:
        if not verify_reset_code(db, data.correo, data.codigo):
            raise HTTPException(status_code=400, detail="Código inválido o expirado")
        u = db.query(Usuario).filter(Usuario.correo == data.correo.lower()).first()
        if not u:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")
        u.contrasena = encriptar(data.nueva_contrasena)
        db.query(PasswordReset).filter(PasswordReset.correo == data.correo).delete()
        db.commit()
        return {"mensaje": "Contraseña actualizada exitosamente"}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error en reset-password")
        db.rollback()
        raise HTTPException(status_code=500, detail="Error interno")

@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.utcnow().isoformat()}

@app.get("/config/ciudades")
def get_ciudades():
    return {"regions": [{"code": code, "region_name": info["region_name"], "ciudades": info["ciudades"]} for code, info in CITIES_BY_REGION.items()]}

@app.get("/config/categorias")
def get_categorias():
    return {"groups": CATEGORIAS_MASTER, "all": ALL_CATEGORIES, "job_categories": JOB_CATEGORIES}

# =========================
# Uploads (Rate limit opcional)
# =========================

upload_deps = [Depends(RateLimiter(times=10, seconds=60))] if RateLimiter else []

@app.post("/local/subir-foto", dependencies=upload_deps)
async def subir_foto(file: UploadFile = File(...), user: Usuario = Depends(usuario_actual)):
    if not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Debe ser una imagen")
    url = save_upload_file_secure(file)
    return {"url": url}

# =========================
# Locales create/edit/delete con normalización y autorización DUENO
# =========================

@app.post("/local/crear")
def crear_local(data: LocalCrearSchema, user: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    if user.rol != "DUENO":
        raise HTTPException(status_code=403, detail="Solo dueños pueden crear locales")
    if data.tipo not in CATEGORIAS_MASTER:
        suger = suggest_close(data.tipo, list(CATEGORIAS_MASTER.keys()))
        raise HTTPException(status_code=400, detail={"msg": "Tipo inválido", "suggestions": suger})
    grupo = CATEGORIAS_MASTER.get(data.tipo, [])
    match_cat = normalize_choice(data.categoria, grupo)
    if match_cat is None:
        suger = suggest_close(data.categoria, grupo + ALL_CATEGORIES)
        raise HTTPException(status_code=400, detail={"msg": "Categoría inválida para el tipo", "suggestions": suger})
    ciudad_canon = normalize_city(data.ciudad)
    if not ciudad_canon:
        suger = suggest_close(data.ciudad, ALL_CITIES_CANONICAL)
        raise HTTPException(status_code=400, detail={"msg": "Ciudad no permitida", "suggestions": suger})
    if data.maps_link and not valid_maps_link(data.maps_link):
        raise HTTPException(status_code=400, detail="maps_link inválido")
    whatsapp_normal = normalize_phone_e164(data.whatsapp) if data.whatsapp else None
    data_dict = data.dict()
    data_dict["ciudad"] = ciudad_canon
    data_dict["categoria"] = match_cat
    data_dict["whatsapp"] = whatsapp_normal
    try:
        with db.begin():
            nuevo = Local(**data_dict, dueno_id=user.id, pago_al_dia=False, visitas=0, clics_whatsapp=0, clics_maps=0, hot_slots_remaining=0, analytics_enabled=False, plan_type=None)
            db.add(nuevo)
        return {"id": nuevo.id}
    except SQLAlchemyError:
        logger.exception("Error creando local")
        raise HTTPException(status_code=500, detail="Error interno")

@app.put("/local/editar/{local_id}")
def editar_local(local_id: int, data: LocalCrearSchema, user: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    if user.rol != "DUENO":
        raise HTTPException(status_code=403, detail="Solo dueños pueden editar locales")
    l = db.query(Local).filter(Local.id == local_id, Local.dueno_id == user.id).first()
    if not l:
        raise HTTPException(status_code=404, detail="Local no encontrado")
    if data.tipo not in CATEGORIAS_MASTER:
        suger = suggest_close(data.tipo, list(CATEGORIAS_MASTER.keys()))
        raise HTTPException(status_code=400, detail={"msg": "Tipo inválido", "suggestions": suger})
    grupo = CATEGORIAS_MASTER.get(data.tipo, [])
    match_cat = normalize_choice(data.categoria, grupo)
    if match_cat is None:
        suger = suggest_close(data.categoria, grupo + ALL_CATEGORIES)
        raise HTTPException(status_code=400, detail={"msg": "Categoría inválida para el tipo", "suggestions": suger})
    ciudad_canon = normalize_city(data.ciudad)
    if not ciudad_canon:
        suger = suggest_close(data.ciudad, ALL_CITIES_CANONICAL)
        raise HTTPException(status_code=400, detail={"msg": "Ciudad no permitida", "suggestions": suger})
    if data.maps_link and not valid_maps_link(data.maps_link):
        raise HTTPException(status_code=400, detail="maps_link inválido")
    whatsapp_normal = normalize_phone_e164(data.whatsapp) if data.whatsapp else None
    data_dict = data.dict()
    data_dict["ciudad"] = ciudad_canon
    data_dict["categoria"] = match_cat
    data_dict["whatsapp"] = whatsapp_normal
    try:
        with db.begin():
            for k, v in data_dict.items():
                setattr(l, k, v)
        return {"mensaje": "Actualizado"}
    except SQLAlchemyError:
        logger.exception("Error editando local")
        raise HTTPException(status_code=500, detail="Error interno")

@app.delete("/local/eliminar/{local_id}")
def eliminar_local(local_id: int, user: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    if user.rol != "DUENO":
        raise HTTPException(status_code=403, detail="Solo dueños pueden eliminar locales")
    try:
        with db.begin():
            l = db.query(Local).filter(Local.id == local_id, Local.dueno_id == user.id).first()
            if not l:
                raise HTTPException(status_code=404, detail="Local no encontrado")
            db.delete(l)
        return {"mensaje": "Eliminado"}
    except SQLAlchemyError:
        logger.exception("Error eliminando local")
        raise HTTPException(status_code=500, detail="Error interno")

# =========================
# Analytics events
# =========================

def record_analytic_event(db: Session, local_id: Optional[int], usuario_id: Optional[str], tipo: str, ip: Optional[str], user_agent: Optional[str]):
    try:
        ev = AnalyticsEvent(local_id=local_id, usuario_id=usuario_id, tipo=tipo, ip=ip, user_agent=(user_agent[:512] if user_agent else None), created_at=datetime.utcnow())
        db.add(ev)
        return ev
    except Exception:
        logger.exception("Error registrando AnalyticsEvent")
        return None

@app.get("/local/whatsapp/{local_id}")
def registrar_whatsapp(local_id: int, request: Request, db: Session = Depends(get_db)):
    l = db.query(Local).filter(Local.id == local_id).first()
    if not l or not l.whatsapp:
        raise HTTPException(status_code=404, detail="Local o número de WhatsApp no encontrado")
    numero = normalize_phone_e164(l.whatsapp)
    if not numero:
        raise HTTPException(status_code=404, detail="Número inválido")
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    try:
        with db.begin():
            record_analytic_event(db, local_id=local_id, usuario_id=None, tipo="click_whatsapp", ip=ip, user_agent=ua)
            db.query(Local).filter(Local.id == local_id).update({Local.clics_whatsapp: func.coalesce(Local.clics_whatsapp, 0) + 1}, synchronize_session=False)
    except SQLAlchemyError:
        logger.exception("Error registrando click_whatsapp")
    whatsapp_url = f"https://wa.me/{numero}"
    return RedirectResponse(url=whatsapp_url, status_code=302)

@app.get("/local/detalle/{local_id}")
def detalle_local(local_id: int, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")
    try:
        with db.begin():
            record_analytic_event(db, local_id=local_id, usuario_id=None, tipo="view_profile", ip=ip, user_agent=ua)
            db.query(Local).filter(Local.id == local_id).update({Local.visitas: func.coalesce(Local.visitas, 0) + 1}, synchronize_session=False)
    except SQLAlchemyError:
        logger.exception("Error registrando view_profile")
    l = db.query(Local).filter(Local.id == local_id).first()
    if not l:
        raise HTTPException(status_code=404, detail="Local no encontrado")
    info = calcular_estado_y_aura(getattr(l, "horarios", None)) if hasattr(l, "horarios") else {"texto": "Horario no definido", "color": "#808080"}
    resenas = db.query(Resena).filter(Resena.local_id == local_id).order_by(desc(Resena.creada_en)).limit(20).all()
    return {"local": sqlalchemy_to_dict(l), "estado_actual": info["texto"], "aura_color": info["color"], "resenas": [sqlalchemy_to_dict(r) for r in resenas], "whatsapp": getattr(l, "whatsapp", None), "maps_link": getattr(l, "maps_link", None)}

# =========================
# Suscripciones y pagos
# =========================

@app.post("/pagos/subscribe/manual")
def pagar_suscripcion_manual(req: SubscribeRequestSchema, user: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    l = db.query(Local).filter(Local.id == req.local_id, Local.dueno_id == user.id).first()
    if not l:
        raise HTTPException(status_code=404, detail="Local no encontrado")
    plan = req.plan
    if plan not in PLAN_PRICES:
        raise HTTPException(status_code=400, detail="Plan inválido")
    if l.tipo == "LOCALES ESTABLECIDOS" and not plan.startswith("LOCAL_"):
        raise HTTPException(status_code=400, detail="Plan incompatible con tipo de local")
    if l.tipo == "SERVICIOS (A DOMICILIO)" and not plan.startswith("SERV_"):
        raise HTTPException(status_code=400, detail="Plan incompatible con tipo de local")
    price = PLAN_PRICES[plan]
    pref = {"items": [{"title": f"Suscripción {plan} - {l.nombre}", "quantity": 1, "unit_price": price, "currency_id": "CLP", "description": f"WhatsApp: {l.whatsapp or ''} Maps: {l.maps_link or ''}"}], "external_reference": f"SUB:{plan}:{l.id}"}
    try:
        init_point = sdk.preference().create(pref)["response"]["init_point"]
    except Exception:
        logger.exception("Error creando preferencia de suscripción")
        raise HTTPException(status_code=500, detail="Error creando preferencia de pago")
    return {"init_point": init_point, "whatsapp": l.whatsapp, "maps_link": l.maps_link}

@app.post("/pagos/subscribe/automatic")
def pagar_suscripcion_automatic(local_id: int, plan: str, user: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    l = db.query(Local).filter(Local.id == local_id, Local.dueno_id == user.id).first()
    if not l:
        raise HTTPException(status_code=404, detail="Local no encontrado")
    if plan not in PLAN_PRICES:
        raise HTTPException(status_code=400, detail="Plan inválido")
    if l.tipo == "LOCALES ESTABLECIDOS" and not plan.startswith("LOCAL_"):
        raise HTTPException(status_code=400, detail="Plan incompatible con tipo de local")
    if l.tipo == "SERVICIOS (A DOMICILIO)" and not plan.startswith("SERV_"):
        raise HTTPException(status_code=400, detail="Plan incompatible con tipo de local")
    price = PLAN_PRICES[plan]
    preapproval = {"reason": f"Suscripción Automática {plan} - {l.nombre}", "auto_recurring": {"frequency": 1, "frequency_type": "months", "transaction_amount": price, "currency_id": "CLP"}, "back_url": "https://tuapp.com", "external_reference": f"SUB:{plan}:{l.id}"}
    try:
        init_point = sdk.preapproval().create(preapproval)["response"]["init_point"]
    except Exception:
        logger.exception("Error creando preapproval MercadoPago")
        raise HTTPException(status_code=500, detail="Error creando suscripción automática")
    return {"init_point": init_point, "whatsapp": l.whatsapp, "maps_link": l.maps_link}

# =========================
# Trabajos: crear y destacar
# =========================

@app.post("/trabajo/crear")
def crear_trabajo(data: TrabajoCrearSchema, file: Optional[UploadFile] = File(None), user: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    if data.oferta_del_dia:
        raise HTTPException(status_code=402, detail="No permitido crear con oferta_del_dia sin pagar el destaque")
    ciudad_final = None
    local_id = data.local_id
    if local_id is not None:
        l = db.query(Local).filter(Local.id == local_id, Local.dueno_id == user.id).first()
        if not l:
            raise HTTPException(status_code=404, detail="Local no encontrado o no perteneciente al usuario")
        ciudad_final = l.ciudad
    else:
        if not data.ciudad:
            raise HTTPException(status_code=400, detail="Debe indicar ciudad o vincular a un local")
        ciudad_final = normalize_city(data.ciudad)
        if not ciudad_final:
            suger = suggest_close(data.ciudad, ALL_CITIES_CANONICAL)
            raise HTTPException(status_code=400, detail={"msg": "Ciudad inválida para trabajo", "suggestions": suger})
    try:
        with db.begin():
            job = JobOffer(local_id=local_id, dueno_id=user.id, titulo=data.titulo, descripcion=data.descripcion, categoria=data.categoria, salario=data.salario, contacto=data.contacto, ciudad=ciudad_final, imagen_url=None, creada_en=None, fecha_fin=None, activa=False, oferta_del_dia=False)
            db.add(job)
            db.flush()
            if file:
                if not file.content_type.startswith("image/"):
                    raise HTTPException(status_code=400, detail="Debe ser una imagen")
                url = save_upload_file_secure(file)
                job.imagen_url = url
            pref = {"items": [{"title": f"Publicación Trabajo - {job.titulo}", "quantity": 1, "unit_price": PRICE_PUBLICAR_TRABAJO, "currency_id": "CLP"}], "external_reference": f"JOB:{job.id}"}
            try:
                init_point = sdk.preference().create(pref)["response"]["init_point"]
            except Exception:
                logger.exception("Error creando preferencia para publicación de trabajo")
                raise HTTPException(status_code=500, detail="Error creando preferencia de pago")
        return {"mensaje": "Draft creado. Paga para publicar.", "job_id": job.id, "init_point": init_point}
    except HTTPException:
        raise
    except SQLAlchemyError:
        logger.exception("Error creando trabajo")
        raise HTTPException(status_code=500, detail="Error interno")

@app.post("/trabajo/upgrade/destacar/{job_id}")
def upgrade_trabajo_destacar(job_id: int, user: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    job = db.query(JobOffer).filter(JobOffer.id == job_id, JobOffer.dueno_id == user.id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado")
    if not job.activa:
        raise HTTPException(status_code=400, detail="Publica primero la oferta antes de destacarla")
    if job.oferta_del_dia:
        raise HTTPException(status_code=400, detail="Oferta ya destacada")
    if job.local_id:
        try:
            with db.begin():
                l = db.query(Local).filter(Local.id == job.local_id).with_for_update().first()
                if l and (l.hot_slots_remaining or 0) > 0:
                    l.hot_slots_remaining = (l.hot_slots_remaining or 0) - 1
                    job.oferta_del_dia = True
                    return {"mensaje": "Oferta destacada usando slot del plan", "hot_slots_remaining": l.hot_slots_remaining}
        except SQLAlchemyError:
            logger.exception("Error consumiendo hot slot")
            raise HTTPException(status_code=500, detail="Error interno")
    pref = {"items": [{"title": f"Destacar Oferta - {job.titulo}", "quantity": 1, "unit_price": PRICE_PUBLICAR_TRABAJO, "currency_id": "CLP"}], "external_reference": f"JOB_HOT:{job.id}"}
    try:
        init_point = sdk.preference().create(pref)["response"]["init_point"]
    except Exception:
        logger.exception("Error creando preferencia para destacar trabajo")
        raise HTTPException(status_code=500, detail="Error creando preferencia de pago")
    return {"mensaje": "Paga para destacar la oferta", "init_point": init_point}

# =========================
# Webhook robusto con idempotencia y notificaciones fuera de la transacción
# =========================

def extract_signature_from_header(signature_header: Optional[str]) -> Optional[str]:
    if not signature_header:
        return None
    # soporta 'sha256=<hex>' y valor crudo
    return signature_header.split("=", 1)[-1].strip()

@app.post("/pagos/webhook")
async def pagos_webhook(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    body = await request.body()
    headers = {k: v for k, v in request.headers.items()}
    signature_header = headers.get("X-Hub-Signature-256") or headers.get("x-hub-signature-256") or headers.get("X-Hub-Signature") or headers.get("x-hub-signature")
    sig = extract_signature_from_header(signature_header)
    if MP_WEBHOOK_KEY:
        if not sig:
            logger.warning("Webhook sin cabecera de firma")
            raise HTTPException(status_code=403, detail="Firma de webhook inválida")
        computed = hmac.new(MP_WEBHOOK_KEY.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(computed, sig):
            logger.warning("Webhook firma no coincide")
            raise HTTPException(status_code=403, detail="Firma de webhook inválida")
    else:
        if not ALLOW_INSECURE_WEBHOOKS:
            logger.warning("Webhook recibido pero MP_WEBHOOK_KEY no configurada")
            raise HTTPException(status_code=403, detail="Webhook no permitido en este entorno")

    try:
        data = await request.json()
    except Exception:
        logger.exception("Webhook payload inválido")
        raise HTTPException(status_code=400, detail="Payload inválido")
    tipo = data.get("type")
    payment_id = data.get("data", {}).get("id")
    if tipo != "payment" or not payment_id:
        logger.info("Webhook tipo no procesado: %s", tipo)
        return {"status": "ignored"}

    notify_tasks: List[tuple] = []  # (to, subject, body)
    try:
        with db.begin():
            # idempotencia robusta: rely en UNIQUE(payment_id) y manejar IntegrityError
            pe = PaymentEvent(payment_id=str(payment_id), created_at=obtener_hora_chile())
            db.add(pe)
            try:
                db.flush()
            except IntegrityError:
                # Ya existe (carrera simultánea)
                logger.info("PaymentEvent ya procesado: %s", payment_id)
                return {"status": "ok", "idempotent": True}

            p_info = get_payment_info(payment_id)
            if p_info.get("status") != "approved":
                logger.info("Pago no aprobado: %s", p_info.get("status"))
                return {"status": "ok", "status_mp": p_info.get("status")}
            ref = p_info.get("external_reference", "") or ""

            if ref.startswith("SUB:"):
                parts = ref.split(":")
                if len(parts) >= 3 and parts[2].isdigit():
                    plan = parts[1]
                    if plan not in PLAN_PRICES:
                        logger.warning("Webhook SUB con plan inválido: %s", plan)
                        return {"status": "ignored"}
                    l_id = int(parts[2])
                    l = db.query(Local).filter(Local.id == l_id).with_for_update().first()
                    if l:
                        l.pago_al_dia = True
                        inicio = normalize_dt(l.fecha_vencimiento) if (getattr(l, "fecha_vencimiento", None) and normalize_dt(l.fecha_vencimiento) > obtener_hora_chile()) else obtener_hora_chile()
                        l.fecha_vencimiento = inicio + timedelta(days=30)
                        slots = PLAN_HOT_SLOTS.get(plan, 0)
                        l.hot_slots_remaining = (l.hot_slots_remaining or 0) + slots
                        l.plan_type = plan
                        l.analytics_enabled = plan.endswith("PRO")

            elif ref.startswith("JOB:"):
                parts = ref.split(":")
                if len(parts) >= 2 and parts[1].isdigit():
                    job_id = int(parts[1])
                    job = db.query(JobOffer).filter(JobOffer.id == job_id).first()
                    if job:
                        now = obtener_hora_chile()
                        job.activa = True
                        job.creada_en = now
                        job.fecha_fin = now + timedelta(days=30)
                        owner = db.query(Usuario).filter(Usuario.id == job.dueno_id).first()
                        if owner:
                            notify_tasks.append((owner.correo, "Tu oferta fue publicada", f"Tu oferta '{job.titulo}' fue publicada."))

            elif ref.startswith("JOB_HOT:"):
                parts = ref.split(":")
                if len(parts) >= 2 and parts[1].isdigit():
                    job_id = int(parts[1])
                    job = db.query(JobOffer).filter(JobOffer.id == job_id).first()
                    if job:
                        if job.local_id:
                            l = db.query(Local).filter(Local.id == job.local_id).with_for_update().first()
                            if l and (l.hot_slots_remaining or 0) > 0:
                                l.hot_slots_remaining = (l.hot_slots_remaining or 0) - 1
                                job.oferta_del_dia = True
                            else:
                                job.oferta_del_dia = True
                        else:
                            job.oferta_del_dia = True

            elif ref.startswith("MANUAL:"):
                parts = ref.split(":")
                if len(parts) >= 3 and parts[1].isdigit() and parts[2].isdigit():
                    l_id = int(parts[1]); meses = int(parts[2])
                    dias = meses * 30
                    l = db.query(Local).filter(Local.id == l_id).with_for_update().first()
                    if l:
                        l.pago_al_dia = True
                        inicio = normalize_dt(l.fecha_vencimiento) if (getattr(l, "fecha_vencimiento", None) and normalize_dt(l.fecha_vencimiento) > obtener_hora_chile()) else obtener_hora_chile()
                        l.fecha_vencimiento = inicio + timedelta(days=dias)

        # Enviar notificaciones fuera de la transacción
        for to, sub, bod in notify_tasks:
            background_tasks.add_task(send_notification_stub, to, sub, bod)

        logger.info("Webhook processed payment_id=%s ref=%s", payment_id, ref)
        return {"status": "ok"}
    except SQLAlchemyError:
        logger.exception("DB error processing webhook")
        raise HTTPException(status_code=500, detail="Error interno")
    except Exception:
        logger.exception("Error processing webhook")
        raise HTTPException(status_code=500, detail="Error interno")

def send_notification_stub(to_email: str, subject: str, body: str):
    logger.info("ENVIAR NOTIF a %s: %s - %s", to_email, subject, body)

# =========================
# Favoritos
# =========================

@app.post("/favoritos/toggle/{local_id}")
def toggle_fav(local_id: int, user: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    try:
        with db.begin():
            f = db.query(Favorito).filter(Favorito.usuario_id == user.id, Favorito.local_id == local_id).first()
            if f:
                db.delete(f)
                return {"mensaje": "Quitado"}
            else:
                fav = Favorito(usuario_id=user.id, local_id=local_id)
                db.add(fav)
                try:
                    db.flush()  # puede disparar IntegrityError si UNIQUE ya existe
                    return {"mensaje": "Agregado"}
                except IntegrityError:
                    return {"mensaje": "Ya agregado"}
    except SQLAlchemyError:
        logger.exception("Error toggling favorito")
        raise HTTPException(status_code=500, detail="Error interno")

@app.get("/usuario/mis-favoritos")
def mis_favoritos(user: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    favs = db.query(Favorito, Local).join(Local, Favorito.local_id == Local.id).filter(Favorito.usuario_id == user.id).all()
    resultados = []
    for fav, l in favs:
        l_dict = sqlalchemy_to_dict(l)
        l_dict["whatsapp"] = getattr(l, "whatsapp", None)
        l_dict["maps_link"] = getattr(l, "maps_link", None)
        resultados.append(l_dict)
    return resultados

# =========================
# Trabajos: listar (FIX ciudad canónica)
# =========================

@app.get("/trabajo/list")
def listar_trabajos(categoria: Optional[str] = None, oferta_del_dia: Optional[bool] = None, ciudad: Optional[str] = None, db: Session = Depends(get_db), limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    ahora = obtener_hora_chile()
    q = db.query(JobOffer).filter(JobOffer.activa == True, JobOffer.fecha_fin > ahora)
    if categoria:
        if categoria not in JOB_CATEGORIES:
            suger = suggest_close(categoria, JOB_CATEGORIES)
            raise HTTPException(status_code=400, detail={"msg": "Categoría inválida", "suggestions": suger})
        q = q.filter(JobOffer.categoria == categoria)
    if oferta_del_dia is not None:
        q = q.filter(JobOffer.oferta_del_dia == oferta_del_dia)
    if ciudad:
        ciudad_canon = normalize_city(ciudad)
        if not ciudad_canon:
            suger = suggest_close(ciudad, ALL_CITIES_CANONICAL)
            raise HTTPException(status_code=400, detail={"msg": "Ciudad inválida para búsqueda.", "suggestions": suger})
        # Comparar por valor canónico exacto en ambos lados
        q = q.outerjoin(Local, JobOffer.local_id == Local.id).filter(
            or_(JobOffer.ciudad == ciudad_canon, Local.ciudad == ciudad_canon)
        )
    trabajos = q.order_by(desc(JobOffer.creada_en)).limit(limit).offset(offset).all()
    return [sqlalchemy_to_dict(t) for t in trabajos]

# =========================
# Búsquedas / Cercanos
# =========================

@app.get("/buscar/cercanos")
def buscar_cercanos(lat: float, lon: float, radio_km: float = 2.0, db: Session = Depends(get_db)):
    ahora = obtener_hora_chile()
    activos = db.query(Local).filter(Local.pago_al_dia == True, Local.fecha_vencimiento > ahora).all()
    locales_cercanos = []
    for l in activos:
        dist = calcular_distancia_safe(lat, lon, l.latitud, l.longitud)
        if dist is None:
            continue
        if dist <= radio_km:
            l_dict = sqlalchemy_to_dict(l)
            info = calcular_estado_y_aura(getattr(l, "horarios", None)) if hasattr(l, "horarios") else {"texto": "Horario no definido", "color": "#808080"}
            l_dict.update({"distancia_km": round(dist, 2), "estado_actual": info["texto"], "aura_color": info["color"], "whatsapp": getattr(l, "whatsapp", None), "maps_link": getattr(l, "maps_link", None)})
            locales_cercanos.append(l_dict)
    return sorted(locales_cercanos, key=lambda x: x["distancia_km"])

@app.get("/buscar")
def buscar(tipo: Optional[str] = None, categoria: Optional[str] = None, ciudad: Optional[str] = None, db: Session = Depends(get_db)):
    ahora = obtener_hora_chile()
    q = db.query(Local).filter(Local.pago_al_dia == True, Local.fecha_vencimiento > ahora)
    if tipo:
        if tipo not in CATEGORIAS_MASTER:
            suger = suggest_close(tipo, list(CATEGORIAS_MASTER.keys()))
            raise HTTPException(status_code=400, detail={"msg": "Tipo inválido", "suggestions": suger})
        q = q.filter(Local.tipo == tipo)
    if categoria:
        match_cat = normalize_choice(categoria, ALL_CATEGORIES)
        if not match_cat:
            suger = suggest_close(categoria, ALL_CATEGORIES)
            raise HTTPException(status_code=400, detail={"msg": "Categoría inválida", "suggestions": suger})
        q = q.filter(Local.categoria == match_cat)
    if ciudad:
        ciudad_canon = normalize_city(ciudad)
        if not ciudad_canon:
            suger = suggest_close(ciudad, ALL_CITIES_CANONICAL)
            raise HTTPException(status_code=400, detail={"msg": "Ciudad inválida", "suggestions": suger})
        q = q.filter(Local.ciudad == ciudad_canon)
    locales = q.all()
    resultado = []
    for l in locales:
        l_dict = sqlalchemy_to_dict(l)
        info = calcular_estado_y_aura(getattr(l, "horarios", None)) if hasattr(l, "horarios") else {"texto": "Horario no definido", "color": "#808080"}
        l_dict.update({"estado_actual": info["texto"], "aura_color": info["color"], "whatsapp": getattr(l, "whatsapp", None), "maps_link": getattr(l, "maps_link", None)})
        if l.tipo == "SERVICIOS (A DOMICILIO)":
            l_dict["latitud"], l_dict["longitud"] = None, None
        resultado.append(l_dict)
    return resultado

# =========================
# Analytics: owner endpoints
# =========================

@app.get("/dueno/local/{local_id}/events")
def get_local_events(local_id: int, user: Usuario = Depends(usuario_actual), db: Session = Depends(get_db), limit: int = Query(100, le=1000)):
    l = db.query(Local).filter(Local.id == local_id, Local.dueno_id == user.id).first()
    if not l:
        raise HTTPException(status_code=404, detail="Local no encontrado")
    if not getattr(l, "analytics_enabled", False):
        raise HTTPException(status_code=403, detail="Estadísticas no habilitadas para este plan")
    events = db.query(AnalyticsEvent).filter(AnalyticsEvent.local_id == local_id).order_by(desc(AnalyticsEvent.created_at)).limit(limit).all()
    return [{"tipo": e.tipo, "ip": e.ip, "ua": e.user_agent, "created_at": e.created_at.isoformat()} for e in events]

@app.get("/dueno/local/{local_id}/stats")
def local_stats(local_id: int, user: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    l = db.query(Local).filter(Local.id == local_id, Local.dueno_id == user.id).first()
    if not l:
        raise HTTPException(status_code=404, detail="Local no encontrado")
    if not getattr(l, "analytics_enabled", False):
        raise HTTPException(status_code=403, detail="Estadísticas no habilitadas para este plan")
    return {"id": l.id, "nombre": l.nombre, "visitas": l.visitas, "clics_whatsapp": getattr(l, "clics_whatsapp", 0), "clics_maps": getattr(l, "clics_maps", 0), "plan": getattr(l, "plan_type", None), "hot_slots_remaining": getattr(l, "hot_slots_remaining", 0), "fecha_vencimiento": l.fecha_vencimiento}

# =========================
# UTIL functions used earlier (completas para evitar archivo cortado)
# =========================

def suggest_close(value: str, choices: List[str], n: int = 3, cutoff: float = 0.6) -> List[str]:
    if not value:
        return []
    return difflib.get_close_matches(value, choices, n=n, cutoff=cutoff)

def calcular_distancia_safe(lat1, lon1, lat2, lon2):
    try:
        if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
            return None
        r = 6371.0
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
        return 2 * r * math.asin(math.sqrt(a))
    except Exception:
        return None

def calcular_estado_y_aura(horarios_json):
    if not horarios_json:
        return {"texto": "Horario no definido", "color": "#808080"}
    ahora = obtener_hora_chile()
    dias_map = {
        'monday': 'lunes',
        'tuesday': 'martes',
        'wednesday': 'miercoles',
        'thursday': 'jueves',
        'friday': 'viernes',
        'saturday': 'sabado',
        'sunday': 'domingo'
    }
    dia_actual = dias_map.get(ahora.strftime('%A').lower())
    config = horarios_json.get(dia_actual)
    if not config or not config.get('abierto'):
        return {"texto": "🔴 Cerrado", "color": "#FF0000"}
    hora_actual = ahora.strftime("%H:%M")
    try:
        if config['manana_inicio'] <= hora_actual <= config['manana_fin']:
            return {"texto": "🟢 Abierto", "color": "#39FF14"}
        if config.get('colacion'):
            if config['manana_fin'] < hora_actual < config['tarde_inicio']:
                return {"texto": "🟡 En colación", "color": "#FFFF00"}
        if config['tarde_inicio'] <= hora_actual <= config['tarde_fin']:
            return {"texto": "🟢 Abierto", "color": "#39FF14"}
    except Exception:
        return {"texto": "Horario no definido", "color": "#808080"}
    return {"texto": "🔴 Cerrado", "color": "#FF0000"}