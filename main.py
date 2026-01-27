from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr
from datetime import datetime, timedelta
from typing import Optional, List, Dict
import jwt
import pytz
import os
import mercadopago # Importante instalar: pip install mercadopago

from sqlalchemy.orm import Session
from database import get_db, engine, Base
from models import Usuario, Local, Oferta

# =========================
# CONFIGURACIÓN
# =========================

CLAVE_SECRETA = os.getenv("SECRET_KEY", "MAPALOCAL_2026_KEY")
ALGORITMO = "HS256"
MINUTOS_TOKEN = 60 * 24

# CONFIGURACIÓN MERCADO PAGO
# Reemplaza con tu Access Token de Producción de Mercado Pago
MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN", "TEST-TU-ACCESS-TOKEN") 
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)

app = FastAPI(title="MapaLocal Backend - Pro Edition")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CATEGORIAS_MASTER = {
    "LOCALES": [
        "Barbería", "Peluquería Dama", "Venta de Gas", 
        "Botillería", "Almacén", "Carnicería"
    ],
    "SERVICIOS (A DOMICILIO)": [
        "Gásfiter", "Electricista", "Maestro", 
        "Clases Particulares", "Mecánico", "Limpieza"
    ]
}

@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)

# =========================
# SEGURIDAD
# =========================

oauth2 = OAuth2PasswordBearer(tokenUrl="/auth/login")
encriptador = CryptContext(schemes=["bcrypt"], deprecated="auto")
zona_chile = pytz.timezone("America/Santiago")

class UsuarioRegistro(BaseModel):
    correo: EmailStr
    nombre: str
    contrasena: str
    rol: str

class Token(BaseModel):
    access_token: str
    token_type: str
    rol: str

class LocalCrear(BaseModel):
    nombre: str
    tipo: str  
    categoria: str 
    ciudad: str
    latitud: Optional[float] = 0.0
    longitud: Optional[float] = 0.0
    hora_apertura: Optional[str] = "09:00"
    hora_cierre: Optional[str] = "20:00"
    whatsapp: Optional[str] = None
    descripcion: Optional[str] = "Sin descripción"

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
    return {"access_token": token, "token_type": "bearer", "rol": usuario.rol}

# =========================
# MERCADO PAGO (PAGOS AUTOMÁTICOS)
# =========================

@app.post("/pagos/crear-preferencia")
def crear_preferencia(user: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    local = db.query(Local).filter(Local.dueno_id == user.id).first()
    if not local:
        raise HTTPException(status_code=404, detail="No tienes un negocio registrado")

    preference_data = {
        "items": [
            {
                "title": f"Suscripción Mensual - {local.nombre}",
                "quantity": 1,
                "unit_price": 2000,
                "currency_id": "CLP"
            }
        ],
        "external_reference": str(local.id),
        "notification_url": "https://TU-URL-DE-RAILWAY.app/pagos/webhook", # Reemplazar por tu URL real
        "back_urls": {
            "success": "https://mapalocal.cl/pago-exitoso",
            "failure": "https://mapalocal.cl/pago-fallido"
        },
        "auto_return": "approved"
    }
    
    result = sdk.preference().create(preference_data)
    return {"init_point": result["response"]["init_point"]}

@app.post("/pagos/webhook")
async def webhook(request: Request, db: Session = Depends(get_db)):
    # Mercado Pago envía notificaciones de pagos exitosos
    params = request.query_params
    if params.get("type") == "payment":
        payment_id = params.get("data.id")
        payment_info = sdk.payment().get(payment_id)
        
        if payment_info["response"]["status"] == "approved":
            local_id = int(payment_info["response"]["external_reference"])
            local = db.query(Local).filter(Local.id == local_id).first()
            if local:
                local.pago_al_dia = True
                local.fecha_vencimiento = datetime.now() + timedelta(days=30)
                db.commit()
    return {"status": "ok"}

# =========================
# LOCALES Y OFERTAS
# =========================

@app.post("/local/crear")
def crear_local(data: LocalCrear, user: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    if user.rol != "DUENO":
        raise HTTPException(status_code=403, detail="Acceso denegado")
    
    if data.tipo not in CATEGORIAS_MASTER:
        raise HTTPException(status_code=400, detail="Tipo de negocio no existe")
    if data.categoria not in CATEGORIAS_MASTER[data.tipo]:
        raise HTTPException(status_code=400, detail="Subcategoría no válida")

    # Inicia con pago_al_dia = False hasta que pase por Mercado Pago
    nuevo = Local(**data.dict(), dueno_id=user.id, pago_al_dia=False)
    db.add(nuevo)
    db.commit()
    return {"mensaje": "Negocio registrado. Procede al pago para activar."}

@app.get("/local/mi-estado")
def mi_estado(user: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    local = db.query(Local).filter(Local.dueno_id == user.id).first()
    if not local:
        return {"registrado": False}
    return {
        "registrado": True,
        "nombre": local.nombre,
        "pago_al_dia": local.pago_al_dia,
        "vencimiento": local.fecha_vencimiento
    }

# =========================
# BÚSQUEDA FILTRADA (SÓLO PAGADOS)
# =========================

@app.get("/buscar")
def buscar(tipo: str, subcategoria: str, ciudad: str, db: Session = Depends(get_db)):
    ahora = datetime.now()
    
    # FILTRO MAESTRO: Solo locales activos y con pago vigente
    query = db.query(Local).filter(
        Local.ciudad.ilike(f"%{ciudad}%"),
        Local.tipo == tipo,
        Local.categoria == subcategoria,
        Local.pago_al_dia == True,
        Local.fecha_vencimiento > ahora
    )
    
    locales = query.all()
    resultados = []
    hoy_inicio = datetime.now(zona_chile).replace(hour=0, minute=0, second=0, microsecond=0)

    for local in locales:
        abierto = abierto_por_horario(local) if local.tipo == "LOCALES" else True
        oferta = db.query(Oferta).filter(
            Oferta.local_id == local.id,
            Oferta.creada_en >= hoy_inicio
        ).first()

        item = {
            "id": local.id,
            "nombre": local.nombre,
            "tipo": local.tipo,
            "subcategoria": local.categoria,
            "descripcion": local.descripcion,
            "whatsapp": f"https://wa.me/{local.whatsapp}" if local.whatsapp else None,
            "oferta": {
                "titulo": oferta.titulo,
                "precio": oferta.precio,
                "imagen": oferta.imagen_url
            } if oferta else None
        }

        if local.tipo == "LOCALES":
            item["coords"] = {"lat": local.latitud, "lng": local.longitud}
            item["abierto_ahora"] = abierto
            item["google_maps"] = f"https://www.google.com/maps?q={local.latitud},{local.longitud}"

        resultados.append(item)

    return resultados

@app.get("/config/categorias")
def obtener_categorias():
    return CATEGORIAS_MASTER