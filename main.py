from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr
from datetime import datetime, timedelta
from typing import Optional
import jwt
import pytz

# =========================
# CONFIGURACIÓN
# =========================
CLAVE_SECRETA = "MAPALOCAL_CAMBIAR_LUEGO"
ALGORITMO = "HS256"
MINUTOS_TOKEN = 60 * 24

app = FastAPI(title="MapaLocal Backend")

oauth2 = OAuth2PasswordBearer(tokenUrl="auth/login")
encriptador = CryptContext(schemes=["bcrypt"], deprecated="auto")

zona_chile = pytz.timezone("America/Santiago")

# =========================
# BASES DE DATOS (MEMORIA)
# =========================
usuarios_db = {}
locales_db = {}   # clave: correo dueño
ofertas_db = {}   # clave: correo dueño

# =========================
# MODELOS
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
    # bcrypt no admite más de 72 bytes
    return encriptador.hash(contrasena[:72])

def verificar(contrasena: str, hash_guardado: str):
    return encriptador.verify(contrasena[:72], hash_guardado)

def crear_token(datos: dict):
    datos = datos.copy()
    datos["exp"] = datetime.utcnow() + timedelta(minutes=MINUTOS_TOKEN)
    return jwt.encode(datos, CLAVE_SECRETA, algorithm=ALGORITMO)

def usuario_actual(token: str = Depends(oauth2)):
    try:
        datos = jwt.decode(token, CLAVE_SECRETA, algorithms=[ALGORITMO])
        correo = datos.get("sub")
        if correo not in usuarios_db:
            raise HTTPException(status_code=401, detail="Token inválido")
        return usuarios_db[correo]
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token inválido")

def abierto_por_horario(local):
    try:
        ahora = datetime.now(zona_chile).time()
        inicio = datetime.strptime(local["hora_apertura"], "%H:%M").time()
        fin = datetime.strptime(local["hora_cierre"], "%H:%M").time()
        return inicio <= ahora <= fin
    except:
        return False

def oferta_activa(oferta):
    # Opcional: podemos agregar fecha fin específica
    return True

# =========================
# AUTH
# =========================
@app.post("/auth/registro")
def registro(usuario: UsuarioRegistro):
    correo = usuario.correo.lower()
    if correo in usuarios_db:
        raise HTTPException(status_code=400, detail="Usuario ya existe")
    
    usuarios_db[correo] = {
        "correo": correo,
        "nombre": usuario.nombre,
        "contrasena": encriptar(usuario.contrasena),
        "rol": usuario.rol.upper()
    }

    return {"mensaje": "Usuario creado"}

@app.post("/auth/login", response_model=Token)
def login(form: OAuth2PasswordRequestForm = Depends()):
    correo = form.username.lower()
    usuario = usuarios_db.get(correo)

    if not usuario or not verificar(form.password, usuario["contrasena"]):
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")

    token = crear_token({"sub": usuario["correo"], "rol": usuario["rol"]})
    return {"access_token": token, "token_type": "bearer"}

# =========================
# LOCALES (DUEÑO)
# =========================
@app.post("/local/crear")
def crear_local(data: LocalCrear, user=Depends(usuario_actual)):
    if user["rol"] != "DUENO":
        raise HTTPException(status_code=403, detail="Solo dueños pueden crear locales")

    locales_db[user["correo"]] = {
        **data.dict(),
        "modo": "AUTO",
        "abierto": False
    }

    return {"mensaje": "Local creado"}

@app.post("/local/manual/{estado}")
def modo_manual(estado: str, user=Depends(usuario_actual)):
    if user["correo"] not in locales_db:
        raise HTTPException(status_code=404, detail="No tienes local registrado")
    
    local = locales_db[user["correo"]]
    local["modo"] = "MANUAL"
    local["abierto"] = estado.upper() == "ABRIR"
    return {"mensaje": f"Estado actualizado a {local['abierto']}"}

# =========================
# OFERTAS (DUEÑO)
# =========================
@app.post("/oferta/crear")
def crear_oferta(oferta: OfertaCrear, user=Depends(usuario_actual)):
    if user["rol"] != "DUENO":
        raise HTTPException(status_code=403, detail="Solo dueños pueden crear ofertas")

    ofertas_db[user["correo"]] = {
        "oferta": oferta.dict(),
        "fecha": datetime.now(zona_chile)
    }

    return {"mensaje": "Oferta publicada"}

# =========================
# MAPA (USUARIOS)
# =========================
@app.get("/mapa/locales")
def ver_locales(ciudad: str, categoria: str):
    resultados = []

    for correo, local in locales_db.items():
        abierto = local["abierto"]

        if local["modo"] == "AUTO" and local.get("hora_apertura"):
            abierto = abierto_por_horario(local)

        if not abierto:
            continue

        if local["ciudad"].lower() != ciudad.lower() or local["categoria"].lower() != categoria.lower():
            continue

        oferta = None
        if correo in ofertas_db and oferta_activa(ofertas_db[correo]):
            oferta = ofertas_db[correo]["oferta"]

        resultados.append({
            "nombre": local["nombre"],
            "categoria": local["categoria"],
            "latitud": local["latitud"],
            "longitud": local["longitud"],
            "oferta": oferta
        })

    return resultados
