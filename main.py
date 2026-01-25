from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware # <--- CAMBIO 1: Importar CORS
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr
from datetime import datetime, timedelta
from typing import Optional
import jwt
import pytz
import os

from sqlalchemy.orm import Session

from database import get_db, engine, Base
from models import Usuario, Local, Oferta

# =========================
# CONFIGURACIÓN
# =========================

CLAVE_SECRETA = os.getenv("SECRET_KEY", "MAPALOCAL_CAMBIAR_LUEGO")
ALGORITMO = "HS256"
MINUTOS_TOKEN = 60 * 24

app = FastAPI(title="MapaLocal Backend")

# =========================
# CAMBIO 1: CONFIGURAR CORS (Evita error "Failed to Fetch")
# =========================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# CREAR TABLAS AL ARRANCAR
# =========================

@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)

# =========================
# SEGURIDAD
# =========================

oauth2 = OAuth2PasswordBearer(tokenUrl="/auth/login")
encriptador = CryptContext(schemes=["bcrypt"], deprecated="auto")
zona_chile = pytz.timezone("America/Santiago")

# =========================
# MODELOS Pydantic
# =========================

class UsuarioRegistro(BaseModel):
    correo: EmailStr
    nombre: str
    contrasena: str
    rol: str   # DUENO / USUARIO


class Token(BaseModel):
    access_token: str
    token_type: str


class LocalCrear(BaseModel):
    nombre: str
    categoria: str
    ciudad: str
    latitud: float
    longitud: float
    hora_apertura: Optional[str] = None
    hora_cierre: Optional[str] = None


class OfertaCrear(BaseModel):
    titulo: str
    precio: str
    descripcion: Optional[str] = None
    imagen_url: Optional[str] = None

# =========================
# UTILIDADES
# =========================

def encriptar(password: str):
    return encriptador.hash(password[:72])


def verificar(password: str, hash_guardado: str):
    return encriptador.verify(password[:72], hash_guardado)


def crear_token(datos: dict):
    datos = datos.copy()
    # Usar UTC explícito para evitar problemas de zona horaria
    datos["exp"] = datetime.utcnow() + timedelta(minutes=MINUTOS_TOKEN)
    return jwt.encode(datos, CLAVE_SECRETA, algorithm=ALGORITMO)


def usuario_actual(
    token: str = Depends(oauth2),
    db: Session = Depends(get_db)
):
    try:
        datos = jwt.decode(token, CLAVE_SECRETA, algorithms=[ALGORITMO])
        correo = datos.get("sub")

        usuario = db.query(Usuario).filter(
            Usuario.correo == correo
        ).first()

        if not usuario:
            raise HTTPException(status_code=401, detail="Token inválido")

        return usuario

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado")

    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token inválido")


def abierto_por_horario(local: Local):
    try:
        ahora = datetime.now(zona_chile).time()
        inicio = datetime.strptime(local.hora_apertura, "%H:%M").time()
        fin = datetime.strptime(local.hora_cierre, "%H:%M").time()
        return inicio <= ahora <= fin
    except:
        return False

# =========================
# AUTH
# =========================

@app.post("/auth/registro")
def registro(usuario: UsuarioRegistro, db: Session = Depends(get_db)):

    correo = usuario.correo.lower()

    if db.query(Usuario).filter(Usuario.correo == correo).first():
        raise HTTPException(status_code=400, detail="Usuario ya existe")

    # CAMBIO 2: Validar el ROL antes de guardarlo para evitar CheckViolation
    rol_final = usuario.rol.upper()
    if rol_final not in ["DUENO", "USUARIO"]:
        raise HTTPException(status_code=400, detail="El rol debe ser DUENO o USUARIO")

    nuevo = Usuario(
        correo=correo,
        nombre=usuario.nombre,
        contrasena=encriptar(usuario.contrasena),
        rol=rol_final
    )

    db.add(nuevo)
    db.commit()

    return {"mensaje": "Usuario creado correctamente"}


@app.post("/auth/login", response_model=Token)
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):

    usuario = db.query(Usuario).filter(
        Usuario.correo == form.username.lower()
    ).first()

    if not usuario or not verificar(form.password, usuario.contrasena):
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")

    token = crear_token({
        "sub": usuario.correo,
        "rol": usuario.rol
    })

    return {"access_token": token, "token_type": "bearer"}

# =========================
# USUARIO LOGUEADO
# =========================

@app.get("/usuarios/me")
def mi_usuario(user: Usuario = Depends(usuario_actual)):
    # CAMBIO 3: Devolver el ID (ahora es UUID) correctamente
    return {
        "id": str(user.id), 
        "correo": user.correo,
        "nombre": user.nombre,
        "rol": user.rol
    }

# =========================
# LOCALES
# =========================

@app.post("/local/crear")
def crear_local(
    data: LocalCrear,
    user: Usuario = Depends(usuario_actual),
    db: Session = Depends(get_db)
):

    if user.rol != "DUENO":
        raise HTTPException(status_code=403, detail="Solo dueños pueden crear locales")

    nuevo = Local(
        nombre=data.nombre,
        categoria=data.categoria,
        ciudad=data.ciudad,
        latitud=data.latitud,
        longitud=data.longitud,
        hora_apertura=data.hora_apertura,
        hora_cierre=data.hora_cierre,
        dueno_id=user.id # SQLAlchemy manejará el UUID automáticamente
    )

    db.add(nuevo)
    db.commit()

    return {"mensaje": "Local creado"}

# ... (El resto de tu código de Ofertas y Mapa sigue igual)