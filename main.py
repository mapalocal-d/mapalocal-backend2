from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr
from datetime import datetime, timedelta
from typing import Optional
import jwt
import pytz

from sqlalchemy.orm import Session
from database import engine, get_db, Base
import models

# =========================
# CONFIGURACIÓN
# =========================
CLAVE_SECRETA = "MAPALOCAL_CAMBIAR_LUEGO"
ALGORITMO = "HS256"
MINUTOS_TOKEN = 60 * 24

app = FastAPI(title="MapaLocal Backend")

# CREA TABLAS EN SUPABASE
Base.metadata.create_all(bind=engine)

oauth2 = OAuth2PasswordBearer(tokenUrl="auth/login")
encriptador = CryptContext(schemes=["bcrypt"], deprecated="auto")
zona_chile = pytz.timezone("America/Santiago")

# =========================
# MODELOS Pydantic
# =========================
class UsuarioRegistro(BaseModel):
    correo: EmailStr
    nombre: str
    contrasena: str
    rol: str  # DUENO / USUARIO

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
    descripcion: str
    imagen_url: str

# =========================
# UTILIDADES
# =========================
def encriptar(contrasena: str):
    return encriptador.hash(contrasena[:72])

def verificar(contrasena: str, hash_guardado: str):
    return encriptador.verify(contrasena[:72], hash_guardado)

def crear_token(datos: dict):
    datos = datos.copy()
    datos["exp"] = datetime.utcnow() + timedelta(minutes=MINUTOS_TOKEN)
    return jwt.encode(datos, CLAVE_SECRETA, algorithm=ALGORITMO)

def usuario_actual(
    token: str = Depends(oauth2),
    db: Session = Depends(get_db)
):
    try:
        datos = jwt.decode(token, CLAVE_SECRETA, algorithms=[ALGORITMO])
        correo = datos.get("sub")

        usuario = db.query(models.Usuario).filter(
            models.Usuario.correo == correo
        ).first()

        if not usuario:
            raise HTTPException(status_code=401, detail="Token inválido")

        return usuario

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token inválido")

# =========================
# AUTH
# =========================
@app.post("/auth/registro")
def registro(usuario: UsuarioRegistro, db: Session = Depends(get_db)):
    correo = usuario.correo.lower()

    existe = db.query(models.Usuario).filter(
        models.Usuario.correo == correo
    ).first()

    if existe:
        raise HTTPException(status_code=400, detail="Usuario ya existe")

    nuevo = models.Usuario(
        correo=correo,
        nombre=usuario.nombre,
        contrasena=encriptar(usuario.contrasena),
        rol=usuario.rol.upper()
    )

    db.add(nuevo)
    db.commit()

    return {"mensaje": "Usuario creado"}

@app.post("/auth/login", response_model=Token)
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    usuario = db.query(models.Usuario).filter(
        models.Usuario.correo == form.username.lower()
    ).first()

    if not usuario or not verificar(form.password, usuario.contrasena):
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")

    token = crear_token({"sub": usuario.correo, "rol": usuario.rol})
    return {"access_token": token, "token_type": "bearer"}
