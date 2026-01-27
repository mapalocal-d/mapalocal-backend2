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
import mercadopago 

from sqlalchemy.orm import Session
from database import get_db, engine, Base
from models import Usuario, Local, Oferta

# =========================
# CONFIGURACIÓN DE ENTORNO
# =========================

CLAVE_SECRETA = os.getenv("SECRET_KEY", "MAPALOCAL_2026_KEY")
ALGORITMO = "HS256"
MINUTOS_TOKEN = 60 * 24
ZONA_HORARIA = pytz.timezone("America/Santiago")

# CONFIGURACIÓN MERCADO PAGO
MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN", "TEST-TU-ACCESS-TOKEN") 
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)

app = FastAPI(title="MapaLocal API - Sistema de Suscripción")

# Habilitar CORS para que Flutter pueda conectarse
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
    # Crea las tablas si no existen al iniciar
    Base.metadata.create_all(bind=engine)

# =========================
# MODELOS DE DATOS (PYDANTIC)
# =========================

class UsuarioRegistro(BaseModel):
    correo: EmailStr
    nombre: str
    contrasena: str
    rol: str # "DUENO" o "USUARIO"

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
# SEGURIDAD Y UTILIDADES
# =========================

oauth2 = OAuth2PasswordBearer(tokenUrl="/auth/login")
encriptador = CryptContext(schemes=["bcrypt"], deprecated="auto")

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
            raise HTTPException(status_code=401, detail="Usuario no encontrado")
        return usuario
    except:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")

def obtener_hora_chile():
    return datetime.now(ZONA_HORARIA)

# =========================
# RUTAS DE AUTENTICACIÓN
# =========================

@app.post("/auth/registro")
def registro(usuario: UsuarioRegistro, db: Session = Depends(get_db)):
    correo = usuario.correo.lower()
    if db.query(Usuario).filter(Usuario.correo == correo).first():
        raise HTTPException(status_code=400, detail="El correo ya está registrado")

    rol_final = usuario.rol.upper()
    if rol_final not in ["DUENO", "USUARIO"]:
        raise HTTPException(status_code=400, detail="Rol inválido. Use DUENO o USUARIO")

    nuevo_usuario = Usuario(
        correo=correo,
        nombre=usuario.nombre,
        contrasena=encriptar(usuario.contrasena),
        rol=rol_final
    )
    db.add(nuevo_usuario)
    db.commit()
    return {"mensaje": "Registro exitoso", "rol": rol_final}

@app.post("/auth/login", response_model=Token)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    usuario = db.query(Usuario).filter(Usuario.correo == form.username.lower()).first()
    if not usuario or not verificar(form.password, usuario.contrasena):
        raise HTTPException(status_code=401, detail="Correo o contraseña incorrectos")

    token = crear_token({"sub": usuario.correo, "rol": usuario.rol})
    return {"access_token": token, "token_type": "bearer", "rol": usuario.rol}

# =========================
# RUTAS DE MERCADO PAGO
# =========================

@app.post("/pagos/crear-preferencia")
def crear_preferencia(user: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    local = db.query(Local).filter(Local.dueno_id == user.id).first()
    if not local:
        raise HTTPException(status_code=404, detail="Debes registrar un negocio primero")

    preference_data = {
        "items": [
            {
                "title": f"Suscripción MapaLocal: {local.nombre}",
                "quantity": 1,
                "unit_price": 2000,
                "currency_id": "CLP"
            }
        ],
        "external_reference": str(local.id),
        "notification_url": "https://TU-URL-DE-RAILWAY.app/pagos/webhook", 
        "back_urls": {
            "success": "https://mapalocal.cl/exito",
            "failure": "https://mapalocal.cl/fallo"
        },
        "auto_return": "approved"
    }
    
    resultado = sdk.preference().create(preference_data)
    return {"init_point": resultado["response"]["init_point"]}

@app.post("/pagos/webhook")
async def webhook(request: Request, db: Session = Depends(get_db)):
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
# RUTAS DE NEGOCIO Y OFERTAS
# =========================

@app.post("/local/crear")
def crear_local(data: LocalCrear, user: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    if user.rol != "DUENO":
        raise HTTPException(status_code=403, detail="Solo dueños pueden registrar negocios")
    
    # Validar categorías
    if data.tipo not in CATEGORIAS_MASTER or data.categoria not in CATEGORIAS_MASTER[data.tipo]:
        raise HTTPException(status_code=400, detail="Categoría o Tipo inválido")

    nuevo_local = Local(
        **data.dict(), 
        dueno_id=user.id, 
        pago_al_dia=False # Inicia oculto hasta que pague
    )
    db.add(nuevo_local)
    db.commit()
    return {"mensaje": "Negocio creado. Realiza el pago para aparecer en el mapa."}

@app.post("/oferta/publicar")
def publicar_oferta(data: OfertaCrear, user: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    local = db.query(Local).filter(Local.dueno_id == user.id).first()
    
    if not local:
        raise HTTPException(status_code=404, detail="No tienes un negocio registrado")
    
    if not local.pago_al_dia:
        raise HTTPException(status_code=402, detail="Pago requerido para publicar ofertas")

    # Limpiar oferta anterior (Solo una por día)
    db.query(Oferta).filter(Oferta.local_id == local.id).delete()

    nueva_oferta = Oferta(
        **data.dict(),
        local_id=local.id,
        dueno_id=user.id,
        creada_en=obtener_hora_chile()
    )
    db.add(nueva_oferta)
    db.commit()
    return {"mensaje": "Oferta del día publicada exitosamente"}

# =========================
# BÚSQUEDA PÚBLICA (FILTRADA)
# =========================

@app.get("/buscar")
def buscar(tipo: str, categoria: str, ciudad: str, db: Session = Depends(get_db)):
    ahora = datetime.now()
    
    # EL FILTRO CRÍTICO: Solo locales con pago_al_dia = True y fecha vigente
    query = db.query(Local).filter(
        Local.tipo == tipo,
        Local.categoria == categoria,
        Local.ciudad.ilike(f"%{ciudad}%"),
        Local.pago_al_dia == True,
        Local.fecha_vencimiento > ahora
    )
    
    locales = query.all()
    resultados = []
    hoy_inicio = obtener_hora_chile().replace(hour=0, minute=0, second=0, microsecond=0)

    for local in locales:
        # Obtener oferta si es de hoy
        oferta = db.query(Oferta).filter(
            Oferta.local_id == local.id,
            Oferta.creada_en >= hoy_inicio
        ).first()

        item = {
            "id": local.id,
            "nombre": local.nombre,
            "descripcion": local.descripcion,
            "whatsapp": f"https://wa.me/{local.whatsapp}" if local.whatsapp else None,
            "coords": {"lat": local.latitud, "lng": local.longitud} if local.tipo == "LOCALES" else None,
            "oferta": {
                "titulo": oferta.titulo,
                "precio": oferta.precio,
                "imagen": oferta.imagen_url
            } if oferta else None
        }
        resultados.append(item)

    return resultados

@app.get("/config/categorias")
def obtener_categorias():
    return CATEGORIAS_MASTER