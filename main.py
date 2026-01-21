from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi_utils.tasks import repeat_every
from datetime import datetime
import requests
import pytz

# -----------------------------------------
# CONFIGURACIÓN SUPABASE
# -----------------------------------------
SUPABASE_URL = "https://kbllizenngnezwhlbcww.supabase.co"
SUPABASE_ANON_KEY = "sb_publishable_Ul6swWuO6afYmWjMxSZv2g_eML0KpKe"

TIMEZONE = pytz.timezone("America/Santiago")

HEADERS = {
    "apikey": SUPABASE_ANON_KEY,
    "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
    "Content-Type": "application/json"
}

# -----------------------------------------
# APP
# -----------------------------------------
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

# -----------------------------------------
# FUNCIONES SUPABASE
# -----------------------------------------
def obtener_locales():
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/locales?select=*",
        headers=HEADERS
    )
    return r.json() if r.status_code == 200 else []

def actualizar_local(local_id, estado, modo):
    requests.patch(
        f"{SUPABASE_URL}/rest/v1/locales?id=eq.{local_id}",
        headers=HEADERS,
        json={
            "estado": estado,
            "modo": modo
        }
    )

def calcular_estado(local):
    hora_actual = datetime.now(TIMEZONE).hour
    return (
        "ABIERTO"
        if local["hora_apertura"] <= hora_actual < local["hora_cierre"]
        else "CERRADO"
    )

# -----------------------------------------
# AUTOMÁTICO (SE EJECUTA SOLO)
# -----------------------------------------
@app.on_event("startup")
@repeat_every(seconds=60)
def actualizar_estados_auto():
    for local in obtener_locales():
        if local["modo"] == "AUTO":
            nuevo_estado = calcular_estado(local)
            if local["estado"] != nuevo_estado:
                actualizar_local(local["id"], nuevo_estado, "AUTO")

# -----------------------------------------
# ENDPOINTS
# -----------------------------------------
@app.get("/")
def home():
    return {"status": "MapaLocal backend activo"}

@app.get("/estado")
def estado():
    locales = obtener_locales()
    if not locales:
        return {"error": "No hay locales"}
    l = locales[0]
    return {
        "estado": l["estado"],
        "modo": l["modo"]
    }

@app.post("/abrir")
def abrir():
    l = obtener_locales()[0]
    actualizar_local(l["id"], "ABIERTO", "MANUAL")
    return {"ok": True}

@app.post("/cerrar")
def cerrar():
    l = obtener_locales()[0]
    actualizar_local(l["id"], "CERRADO", "MANUAL")
    return {"ok": True}

@app.post("/modo/{modo}")
def cambiar_modo(modo: str):
    modo = modo.upper()
    if modo not in ["AUTO", "MANUAL"]:
        return {"error": "Modo inválido"}
    l = obtener_locales()[0]
    actualizar_local(l["id"], l["estado"], modo)
    return {"ok": True}
