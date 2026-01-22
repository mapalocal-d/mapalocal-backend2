from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from passlib.context import CryptContext
from datetime import datetime, timedelta
import pytz
import requests
import os
import jwt

# ---------------------------
# Configuración
# ---------------------------
SUPABASE_URL = "https://kbllizenngnezwhlbcww.supabase.co"
SUPABASE_ANON_KEY = "sb_publishable_Ul6swWuO6afYmWjMxSZv2g_eML0KpKe"
JWT_SECRET = "TU_SECRET_KEY"  # Cambia esto a algo seguro
JWT_ALGORITHM = "HS256"

HORA_APERTURA_DEFAULT = 9
HORA_CIERRE_DEFAULT = 20

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# ---------------------------
# Funciones Supabase
# ---------------------------

def supabase_get(table, filtro=""):
    url = f"{SUPABASE_URL}/rest/v1/{table}?{filtro}" if filtro else f"{SUPABASE_URL}/rest/v1/{table}?select=*"
    headers = {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
        "Content-Type": "application/json"
    }
    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        return r.json()
    return []

def supabase_post(table, data):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
        "Content-Type": "application/json"
    }
    r = requests.post(url, headers=headers, json=data)
    return r.status_code in [200, 201]

def supabase_patch(table, filtro, data):
    url = f"{SUPABASE_URL}/rest/v1/{table}?{filtro}"
    headers = {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
        "Content-Type": "application/json"
    }
    r = requests.patch(url, headers=headers, json=data)
    return r.status_code in [200, 204]

# ---------------------------
# Autenticación JWT
# ---------------------------

def create_token(user_id, rol):
    payload = {
        "user_id": user_id,
        "rol": rol,
        "exp": datetime.utcnow() + timedelta(days=1)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def verify_token(token=Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except:
        raise HTTPException(status_code=401, detail="Token inválido")

# ---------------------------
# FastAPI
# ---------------------------

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

# ---------------------------
# Endpoints de usuarios
# ---------------------------

@app.post("/registro")
def registro(nombre: str, correo: str, password: str, rol: str, nombre_local: str = None):
    if rol not in ["DUENO", "CLIENTE"]:
        raise HTTPException(status_code=400, detail="Rol inválido")
    # Verificar si ya existe
    if supabase_get("usuarios", f"correo=eq.{correo}"):
        raise HTTPException(status_code=400, detail="Correo ya registrado")
    hashed = pwd_context.hash(password)
    data = {"nombre": nombre, "correo": correo, "password": hashed, "rol": rol}
    if rol == "DUENO":
        data["nombre_local"] = nombre_local or "Sin nombre"
        data["estado"] = "CERRADO"
        data["modo"] = "MANUAL"
    supabase_post("usuarios", data)
    return {"mensaje": "Usuario registrado"}

@app.post("/login")
def login(form: OAuth2PasswordRequestForm = Depends()):
    usuarios = supabase_get("usuarios", f"correo=eq.{form.username}")
    if not usuarios:
        raise HTTPException(status_code=400, detail="Usuario no encontrado")
    user = usuarios[0]
    if not pwd_context.verify(form.password, user["password"]):
        raise HTTPException(status_code=400, detail="Contraseña incorrecta")
    token = create_token(user["id"], user["rol"])
    return {"access_token": token, "token_type": "bearer"}

# ---------------------------
# Endpoints para dueños (protegidos)
# ---------------------------

def verificar_dueno(token_data=Depends(verify_token)):
    if token_data["rol"] != "DUENO":
        raise HTTPException(status_code=403, detail="Acceso denegado")
    return token_data

@app.get("/estado")
def estado(token_data=Depends(verify_token)):
    if token_data["rol"] == "DUENO":
        local = supabase_get("usuarios", f"id=eq.{token_data['user_id']}")[0]
        return {"estado": local.get("estado"), "modo": local.get("modo")}
    else:
        # CLIENTE solo puede ver locales activos
        locales = supabase_get("usuarios", "rol=eq.DUENO")
        locales_activos = [l for l in locales if l.get("estado")=="ABIERTO"]
        return {"locales": locales_activos}

@app.post("/abrir")
def abrir(token_data=Depends(verificar_dueno)):
    local_id = token_data["user_id"]
    exito = supabase_patch("usuarios", f"id=eq.{local_id}", {"estado":"ABIERTO","modo":"MANUAL"})
    if exito:
        return {"mensaje":"Local abierto"}
    return {"error":"No se pudo abrir"}

@app.post("/cerrar")
def cerrar(token_data=Depends(verificar_dueno)):
    local_id = token_data["user_id"]
    exito = supabase_patch("usuarios", f"id=eq.{local_id}", {"estado":"CERRADO","modo":"MANUAL"})
    if exito:
        return {"mensaje":"Local cerrado"}
    return {"error":"No se pudo cerrar"}

@app.post("/modo/{modo}")
def cambiar_modo(modo: str, token_data=Depends(verificar_dueno)):
    local_id = token_data["user_id"]
    if modo.upper() not in ["AUTO","MANUAL"]:
        raise HTTPException(status_code=400, detail="Modo inválido")
    exito = supabase_patch("usuarios", f"id=eq.{local_id}", {"modo":modo.upper()})
    if exito:
        return {"mensaje":f"Modo cambiado a {modo.upper()}"}
    return {"error":"No se pudo cambiar el modo"}

@app.post("/auto")
def modo_auto(token_data=Depends(verificar_dueno)):
    local_id = token_data["user_id"]
    local = supabase_get("usuarios", f"id=eq.{local_id}")[0]
    if local["modo"] != "AUTO":
        return {"mensaje":"Local no está en modo automático"}
    zona = pytz.timezone("America/Santiago")
    hora_actual = datetime.now(zona).hour
    hora_apertura = int(os.getenv("HORA_APERTURA", HORA_APERTURA_DEFAULT))
    hora_cierre = int(os.getenv("HORA_CIERRE", HORA_CIERRE_DEFAULT))
    estado_nuevo = "ABIERTO" if hora_apertura <= hora_actual < hora_cierre else "CERRADO"
    supabase_patch("usuarios", f"id=eq.{local_id}", {"estado":estado_nuevo})
    return {"mensaje":f"Estado actualizado a {estado_nuevo}"}

@app.post("/oferta")
def publicar_oferta(titulo: str, descripcion: str, precio: float, foto_url: str, token_data=Depends(verificar_dueno)):
    local_id = token_data["user_id"]
    fecha_actual = datetime.now(pytz.timezone("America/Santiago"))
    fecha_exp = fecha_actual.replace(hour=23,minute=59,second=59)
    data = {
        "local_id": local_id,
        "titulo": titulo,
        "descripcion": descripcion,
        "precio": precio,
        "foto": foto_url,
        "fecha_creacion": fecha_actual.isoformat(),
        "fecha_expiracion": fecha_exp.isoformat()
    }
    exito = supabase_post("ofertas", data)
    if exito:
        return {"mensaje":"Oferta publicada"}
    return {"error":"No se pudo publicar"}

@app.get("/oferta")
def ver_oferta():
    fecha_actual = datetime.now(pytz.timezone("America/Santiago"))
    ofertas = supabase_get("ofertas", f"fecha_expiracion=gt.{fecha_actual.isoformat()}")
    return {"ofertas": ofertas if ofertas else []}

