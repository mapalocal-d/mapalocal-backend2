from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr
from datetime import datetime, timedelta
from typing import Optional, List
import jwt
import pytz
import os

from sqlalchemy.orm import Session

from database import get_db, engine, Base
from models import Usuario, Local, Oferta

# =========================
# CONFIGURACIÓN
# =========================

CLAVE_SECRETA = os.getenv("SECRET_KEY", "MAPALOCAL_2026_KEY")
ALGORITMO = "HS256"
MINUTOS_TOKEN = 60 * 24

app = FastAPI(title="MapaLocal Backend")

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
# MODELOS Pydantic (Validación de datos)
# =========================

class UsuarioRegistro(BaseModel):
    correo: EmailStr
    nombre: str
    contrasena: str
    rol: str

class Token(BaseModel):
    access_token: str
    token_type: str

class LocalCrear(BaseModel):
    nombre: str
    categoria: str
    ciudad: str
    latitud: float
    longitud: float
    hora_apertura: Optional[str] = "09:00"
    hora_cierre: Optional[str] = "20:00"
    whatsapp: Optional[str] = None # <-- ARREGLADO: Ahora puedes enviar el número

class OfertaCrear(BaseModel):
    titulo: str
    precio: str
    descripcion: Optional[str] = None
    imagen_url: Optional[str] = None
    local_id: int # <-- ARREGLADO: Obligatorio para conectar con el local

# =========================
# UTILIDADES
# =========================

def encriptar(password: str):
    return encriptador.hash(password[:72])

def verificar(password: str, hash_guardado: str):
    return encriptador.verify(password[:72], hash_guardado)

def crear_token(datos: dict):
    datos = datos.copy()
    datos["exp"] = datetime.utcnow() + timedelta(minutes=MINUTOS_TOKEN)
    return jwt.encode(datos, CLAVE_SECRETA, algorithm=ALGORITMO)

def usuario_actual(token: str = Depends(oauth2), db: Session = Depends(get_db)):
    try:
        datos = jwt.decode(token, CLAVE_SECRETA, algorithms=[ALGORITMO])
        correo = datos.get("sub")
        usuario = db.query(Usuario).filter(Usuario.correo == correo).first()
        if not usuario:
            raise HTTPException(status_code=401, detail="Token inválido")
        return usuario
    except:
        raise HTTPException(status_code=401, detail="Sesión inválida")

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

    rol_final = usuario.rol.upper()
    if rol_final not in ["DUENO", "USUARIO"]:
        raise HTTPException(status_code=400, detail="Rol inválido")

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
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    usuario = db.query(Usuario).filter(Usuario.correo == form.username.lower()).first()
    if not usuario or not verificar(form.password, usuario.contrasena):
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")

    token = crear_token({"sub": usuario.correo, "rol": usuario.rol})
    return {"access_token": token, "token_type": "bearer"}

# =========================
# LOCALES Y OFERTAS
# =========================

@app.post("/local/crear")
def crear_local(data: LocalCrear, user: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    if user.rol != "DUENO":
        raise HTTPException(status_code=403, detail="Acceso denegado")

    nuevo = Local(**data.dict(), dueno_id=user.id)
    db.add(nuevo)
    db.commit()
    return {"mensaje": "Local creado"}

@app.post("/oferta/crear")
def crear_oferta(oferta: OfertaCrear, user: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    if user.rol != "DUENO":
        raise HTTPException(status_code=403, detail="Acceso denegado")

    # Verificar que el local pertenezca al dueño
    local = db.query(Local).filter(Local.id == oferta.local_id, Local.dueno_id == user.id).first()
    if not local:
        raise HTTPException(status_code=404, detail="El local no existe o no te pertenece")

    # Limpiamos ofertas anteriores de este local específico
    db.query(Oferta).filter(Oferta.local_id == oferta.local_id).delete()
    
    nueva = Oferta(**oferta.dict(), dueno_id=user.id)
    db.add(nueva)
    db.commit()
    return {"mensaje": "Oferta publicada correctamente"}

# =========================
# MAPA (VISIÓN PARA CLIENTES)
# =========================

@app.get("/mapa/locales")
def ver_locales(ciudad: str, db: Session = Depends(get_db)):
    locales = db.query(Local).filter(Local.ciudad.ilike(f"%{ciudad}%")).all()
    resultados = []

    for local in locales:
        # Estado de apertura
        abierto = abierto_por_horario(local) if local.modo == "AUTO" else (local.abierto == 1)

        # Buscar la oferta de este local específico
        oferta = db.query(Oferta).filter(Oferta.local_id == local.id).first()

        resultados.append({
            "id": local.id,
            "nombre": local.nombre,
            "categoria": local.categoria,
            "abierto_ahora": abierto,
            "coords": {"lat": local.latitud, "lng": local.longitud},
            "links": {
                "google_maps": f"https://www.google.com/maps/search/?api=1&query={local.latitud},{local.longitud}",
                "whatsapp": f"https://wa.me/{local.whatsapp}" if local.whatsapp else None
            },
            "oferta": {
                "titulo": oferta.titulo,
                "precio": oferta.precio,
                "descripcion": oferta.descripcion
            } if oferta else None
        })

    return resultados