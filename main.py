from fastapi import FastAPI, HTTPException, Depends, Request, UploadFile, File, Header
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr
from datetime import datetime, timedelta, timezone 
from typing import Optional, List, Dict
from PIL import Image 
import io
import jwt
import pytz
import os
import mercadopago 
import random
import string
import math
import shutil

from sqlalchemy.orm import Session
from database import get_db, engine, Base
from models import Usuario, Local, Oferta, Favorito, Resena 

# =========================
# CONFIGURACIÓN
# =========================

CLAVE_SECRETA = os.getenv("SECRET_KEY", "MAPALOCAL_2026_KEY")
APP_AUTH_KEY = "MAPALOCAL_APP_SECURE_TOKEN_2026" 
ALGORITMO = "HS256"
MINUTOS_TOKEN = 60 * 24
ZONA_HORARIA = pytz.timezone("America/Santiago")
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB límite seguridad

MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN", "TEST-TU-ACCESS-TOKEN") 
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)

app = FastAPI(title="MapaLocal API - Versión Final Profesional")

app.mount("/static", StaticFiles(directory="static"), name="static")
os.makedirs("static/uploads", exist_ok=True)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CATEGORIAS_MASTER = {
    "LOCALES ESTABLECIDOS": [
        "Almacén / Minimarket (General)", "Almacén / Minimarket (con CajaVecina)",
        "Botillería (General)", "Botillería (con CajaVecina)", "Carnicería", 
        "Panadería", "Pastelería", "Frutería y Verdulería", "Fiambrería", 
        "Comida Rápida (Local)", "Restaurante" ,"Cafetería", "Centro Médico",
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

@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)

# =========================
# MODELOS DE DATOS
# =========================

class UsuarioRegistro(BaseModel):
    correo: EmailStr
    nombre: str
    contrasena: str
    rol: str

class Token(BaseModel):
    access_token: str
    token_type: str
    rol: str

class ResetPasswordRequest(BaseModel):
    correo: EmailStr
    codigo: str
    nueva_contrasena: str

class SolicitudReset(BaseModel):
    correo: EmailStr

class HorarioDia(BaseModel):
    abierto: bool = True
    maana_inicio: str = "09:00"
    maana_fin: str = "14:00"
    colacion: bool = True
    tarde_inicio: Optional[str] = "15:30"
    tarde_fin: Optional[str] = "20:00"

class LocalCrear(BaseModel):
    nombre: str
    tipo: str  
    categoria: str 
    ciudad: str
    latitud: Optional[float] = None 
    longitud: Optional[float] = None 
    whatsapp: Optional[str] = None
    maps_link: Optional[str] = None
    descripcion: Optional[str] = "Sin descripción"
    foto1_url: Optional[str] = None
    foto2_url: Optional[str] = None
    horarios: Optional[Dict[str, HorarioDia]] = None

class OfertaCrear(BaseModel):
    titulo: str
    precio: str
    descripcion: Optional[str] = None
    imagen_url: Optional[str] = None
    dias_duracion: int = 1 

class ResenaCrear(BaseModel):
    estrellas: int 
    comentario: Optional[str] = None

class SolicitudPago(BaseModel):
    local_id: int
    meses: int

# =========================
# SEGURIDAD Y UTILS
# =========================

oauth2 = OAuth2PasswordBearer(tokenUrl="/auth/login")
encriptador = CryptContext(schemes=["bcrypt"], deprecated="auto")

def encriptar(password: str): return encriptador.hash(password[:72])
def verificar(password: str, hash_guardado: str): return encriptador.verify(password[:72], hash_guardado)

def obtener_hora_chile(): 
    return datetime.now(ZONA_HORARIA)

def crear_token(datos: dict):
    datos = datos.copy()
    datos["exp"] = datetime.now(timezone.utc) + timedelta(minutes=MINUTOS_TOKEN)
    return jwt.encode(datos, CLAVE_SECRETA, algorithm=ALGORITMO)

def usuario_actual(token: str = Depends(oauth2), db: Session = Depends(get_db)):
    try:
        datos = jwt.decode(token, CLAVE_SECRETA, algorithms=[ALGORITMO])
        usuario = db.query(Usuario).filter(Usuario.correo == datos.get("sub")).first()
        if not usuario: raise HTTPException(status_code=401)
        return usuario
    except jwt.PyJWTError: 
        raise HTTPException(status_code=401, detail="Sesión expirada o inválida")

def calcular_distancia(lat1, lon1, lat2, lon2):
    r = 6371 
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return 2 * r * math.asin(math.sqrt(a))

def calcular_estado_y_aura(horarios_json):
    if not horarios_json: return {"texto": "Horario no definido", "color": "#808080", "esta_abierto": False}
    ahora = obtener_hora_chile()
    dias_map = {'monday':'lunes','tuesday':'martes','wednesday':'miercoles','thursday':'jueves','friday':'viernes','saturday':'sabado','sunday':'domingo'}
    dia_actual = dias_map.get(ahora.strftime('%A').lower())
    config = horarios_json.get(dia_actual)
    
    if not config or not config.get('abierto'): 
        return {"texto": "🔴 Cerrado", "color": "#FF0000", "esta_abierto": False}
    
    hora_actual = ahora.strftime("%H:%M")
    
    if config['maana_inicio'] <= hora_actual <= config['maana_fin']: 
        return {"texto": "🟢 Abierto", "color": "#39FF14", "esta_abierto": True}
    
    if config.get('colacion'):
        if config['maana_fin'] < hora_actual < config['tarde_inicio']: 
            return {"texto": "🟡 En colación", "color": "#FFFF00", "esta_abierto": False}
            
    if config['tarde_inicio'] <= hora_actual <= config['tarde_fin']: 
        return {"texto": "🟢 Abierto", "color": "#39FF14", "esta_abierto": True}
        
    return {"texto": "🔴 Cerrado", "color": "#FF0000", "esta_abierto": False}

# =========================
# RUTAS AUTH & PERFIL
# =========================

@app.post("/auth/registro")
def registro(usuario: UsuarioRegistro, db: Session = Depends(get_db)):
    if db.query(Usuario).filter(Usuario.correo == usuario.correo.lower()).first():
        raise HTTPException(status_code=400, detail="Ya existe")
    nuevo = Usuario(correo=usuario.correo.lower(), nombre=usuario.nombre, contrasena=encriptar(usuario.contrasena), rol=usuario.rol.upper())
    db.add(nuevo); db.commit()
    return {"mensaje": "Ok"}

@app.post("/auth/login", response_model=Token)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    u = db.query(Usuario).filter(Usuario.correo == form.username.lower()).first()
    if not u or not verificar(form.password, u.contrasena):
        raise HTTPException(status_code=401, detail="Error de credenciales")
    return {"access_token": crear_token({"sub": u.correo, "rol": u.rol}), "token_type": "bearer", "rol": u.rol}

@app.post("/auth/solicitar-reset")
def solicitar_reset(data: SolicitudReset, db: Session = Depends(get_db)):
    u = db.query(Usuario).filter(Usuario.correo == data.correo.lower()).first()
    if not u: raise HTTPException(status_code=404, detail="Correo no registrado")
    # Aquí iría el envío de email real. Por ahora simulamos éxito.
    return {"mensaje": "Si el correo existe, se envió un código de recuperación"}

@app.post("/auth/reset-password")
def reset_password(data: ResetPasswordRequest, db: Session = Depends(get_db)):
    u = db.query(Usuario).filter(Usuario.correo == data.correo.lower()).first()
    if not u: raise HTTPException(status_code=404, detail="Usuario no encontrado")
    # Validación simple: en producción compararías 'data.codigo' con uno guardado en BD
    u.contrasena = encriptar(data.nueva_contrasena)
    db.commit()
    return {"mensaje": "Contraseña actualizada exitosamente"}

@app.post("/auth/cambiar-rol")
def cambiar_rol(user: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    user.rol = "DUENO" if user.rol == "USUARIO" else "USUARIO"
    db.commit(); return {"rol": user.rol}

@app.get("/dueno/mis-locales-simple")
def listar_locales_simple(user: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    locales = db.query(Local).filter(Local.dueno_id == user.id).all()
    return [{"id": l.id, "nombre": l.nombre} for l in locales]

@app.get("/dueno/perfil")
def perfil_dueno(user: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    locales = db.query(Local).filter(Local.dueno_id == user.id).all()
    resultado = []
    ahora = datetime.now()
    for l in locales:
        dias = max(0, (l.fecha_vencimiento - ahora).days) if l.fecha_vencimiento else 0
        resultado.append({
            "id": l.id, "nombre": l.nombre, "dias_restantes": dias, 
            "pago_al_dia": l.pago_al_dia and (l.fecha_vencimiento > ahora if l.fecha_vencimiento else False),
            "visitas": l.visitas, 
            "clics_whatsapp": getattr(l, 'clics_whatsapp', 0),
            "clics_maps": getattr(l, 'clics_maps', 0),
            "alerta": f"⚠️ Vence en {dias} días" if 0 < dias <= 5 else None
        })
    return {"nombre": user.nombre, "locales": resultado}

# =========================
# GESTIÓN DE LOCALES Y FOTOS
# =========================

@app.post("/local/subir-foto")
async def subir_foto(file: UploadFile = File(...), user: Usuario = Depends(usuario_actual)):
    if not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Debe ser una imagen")
    
    # Validación de tamaño para evitar ataques de denegación de servicio
    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="Imagen demasiado pesada (máx 5MB)")

    try:
        img = Image.open(io.BytesIO(contents))
        if img.mode in ("RGBA", "P"): img = img.convert("RGB")
        img.thumbnail((800, 800))
        
        name = f"local_{user.id}_{random.randint(1000,9999)}.jpg"
        path = os.path.join("static/uploads", name)
        img.save(path, "JPEG", quality=85, optimize=True)
        return {"url": f"/static/uploads/{name}"}
    except Exception:
        raise HTTPException(status_code=500, detail="Error procesando la imagen")

@app.delete("/local/borrar-foto")
def borrar_foto(url: str, user: Usuario = Depends(usuario_actual)):
    # Seguridad: Solo borrar si la foto pertenece al usuario (el nombre incluye su ID)
    filename = url.split("/")[-1]
    if not filename.startswith(f"local_{user.id}_"):
        raise HTTPException(status_code=403, detail="No puedes borrar esta foto")
    
    full_path = os.path.join("static/uploads", filename)
    if os.path.exists(full_path):
        os.remove(full_path)
        return {"mensaje": "Foto eliminada"}
    raise HTTPException(status_code=404, detail="Archivo no encontrado")

@app.post("/local/crear")
def crear_local(data: LocalCrear, user: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    if data.tipo == "SERVICIOS (A DOMICILIO)":
        data.latitud, data.longitud, data.maps_link = None, None, None
    elif data.latitud is None or data.longitud is None:
        raise HTTPException(status_code=400, detail="Locales establecidos requieren ubicación")

    nuevo = Local(**data.dict(), dueno_id=user.id, pago_al_dia=False, visitas=0)
    db.add(nuevo); db.commit(); return {"id": nuevo.id}

@app.put("/local/editar/{local_id}")
def editar_local(local_id: int, data: LocalCrear, user: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    l = db.query(Local).filter(Local.id == local_id, Local.dueno_id == user.id).first()
    if not l: raise HTTPException(status_code=403, detail="No autorizado")
    
    if data.tipo == "SERVICIOS (A DOMICILIO)":
        data.latitud, data.longitud = None, None

    for k, v in data.dict().items(): setattr(l, k, v)
    db.commit(); return {"mensaje": "Actualizado"}

@app.delete("/local/eliminar/{local_id}")
def eliminar_local(local_id: int, user: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    l = db.query(Local).filter(Local.id == local_id, Local.dueno_id == user.id).first()
    if not l: raise HTTPException(status_code=403, detail="No autorizado")
    db.delete(l); db.commit(); return {"mensaje": "Eliminado"}

# =========================
# MÉTRICAS Y TRACKING
# =========================

@app.post("/local/track-click/{local_id}")
def track_click(local_id: int, tipo: str, db: Session = Depends(get_db), x_app_source: Optional[str] = Header(None)):
    if x_app_source != APP_AUTH_KEY: raise HTTPException(status_code=403, detail="Origen no autorizado")
    l = db.query(Local).filter(Local.id == local_id).first()
    if not l: raise HTTPException(status_code=404)
    
    if tipo == "whatsapp": l.clics_whatsapp = getattr(l, 'clics_whatsapp', 0) + 1
    elif tipo == "maps": l.clics_maps = getattr(l, 'clics_maps', 0) + 1
    else: raise HTTPException(status_code=400, detail="Tipo inválido")
        
    db.commit(); return {"status": "ok"}

# =========================
# PAGOS (MERCADO PAGO)
# =========================

@app.post("/pagos/manual")
def pago_manual(pago: SolicitudPago, user: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    l = db.query(Local).filter(Local.id == pago.local_id, Local.dueno_id == user.id).first()
    if not l: raise HTTPException(status_code=404)
    precio = 2000 if l.tipo == "LOCALES ESTABLECIDOS" else 3000
    pref = {
        "items": [{"title": f"Plan {pago.meses} Meses - {l.nombre}", "quantity": 1, "unit_price": precio * pago.meses, "currency_id": "CLP"}],
        "external_reference": f"MANUAL:{l.id}:{pago.meses}"
    }
    return {"init_point": sdk.preference().create(pref)["response"]["init_point"]}

@app.post("/pagos/automatico")
def pago_automatico(local_id: int, user: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    l = db.query(Local).filter(Local.id == local_id, Local.dueno_id == user.id).first()
    if not l: raise HTTPException(status_code=404)
    precio = 2000 if l.tipo == "LOCALES ESTABLECIDOS" else 3000
    plan = {
        "reason": f"Suscripción Automática {l.nombre}",
        "auto_recurring": {"frequency": 1, "frequency_type": "months", "transaction_amount": precio, "currency_id": "CLP"},
        "back_url": "https://tuapp.com", "external_reference": f"AUTO:{l.id}"
    }
    return {"init_point": sdk.preapproval().create(plan)["response"]["init_point"]}

@app.post("/pagos/webhook")
async def webhook(request: Request, db: Session = Depends(get_db)):
    try:
        data = await request.json()
        if data.get("type") == "payment":
            p_info = sdk.payment().get(data["data"]["id"])["response"]
            if p_info.get("status") == "approved":
                ref = p_info.get("external_reference")
                if not ref: return {"status": "error"}
                
                parts = ref.split(":")
                l_id, dias = int(parts[1]), (int(parts[2]) * 30 if "MANUAL" in ref else 30)
                
                l = db.query(Local).filter(Local.id == l_id).first()
                if l:
                    ahora = datetime.now()
                    inicio = l.fecha_vencimiento if (l.fecha_vencimiento and l.fecha_vencimiento > ahora) else ahora
                    l.fecha_vencimiento = inicio + timedelta(days=dias)
                    l.pago_al_dia = True
                    db.commit()
    except Exception as e: print(f"Error Webhook: {e}")
    return {"status": "ok"}

# =========================
# OFERTAS Y MANTENIMIENTO
# =========================

@app.post("/mantenimiento/limpiar-ofertas")
def limpiar_ofertas(db: Session = Depends(get_db), x_app_source: Optional[str] = Header(None)):
    if x_app_source != APP_AUTH_KEY: raise HTTPException(status_code=403)
    ahora = datetime.now()
    borrados = db.query(Oferta).filter(Oferta.fecha_fin < ahora).delete()
    db.commit()
    return {"mensaje": f"Se eliminaron {borrados} ofertas expiradas"}

@app.get("/cliente/muro-ofertas")
def muro_ofertas(user: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    ahora = datetime.now()
    return db.query(Oferta).join(Local).join(Favorito).filter(
        Favorito.usuario_id == user.id, 
        Oferta.fecha_fin >= ahora, 
        Local.pago_al_dia == True, 
        Local.fecha_vencimiento > ahora
    ).all()

@app.post("/oferta/publicar/{local_id}")
def publicar(local_id: int, data: OfertaCrear, user: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    l = db.query(Local).filter(Local.id == local_id, Local.dueno_id == user.id).first()
    if not l: raise HTTPException(status_code=404, detail="Negocio no encontrado")
    
    ahora_naive = obtener_hora_chile().replace(tzinfo=None) 
    if not l.pago_al_dia or (l.fecha_vencimiento and l.fecha_vencimiento < ahora_naive): 
        raise HTTPException(status_code=402, detail="Suscripción inactiva")
    
    db.query(Oferta).filter(Oferta.local_id == l.id).delete()
    expiracion = ahora_naive + timedelta(days=data.dias_duracion)
    
    nueva = Oferta(**data.dict(), local_id=l.id, dueno_id=user.id, creada_en=ahora_naive, fecha_fin=expiracion)
    db.add(nueva); db.commit()
    return {"mensaje": f"Publicada para {l.nombre}", "vence_el": expiracion}

# =========================
# BÚSQUEDA Y DETALLES
# =========================

@app.post("/local/resena/{local_id}")
def dejar_resena(local_id: int, data: ResenaCrear, user: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    nueva = Resena(**data.dict(), local_id=local_id, usuario_id=user.id)
    db.add(nueva); db.commit(); return {"mensaje": "Gracias"}

@app.post("/favoritos/toggle/{local_id}")
def toggle_fav(local_id: int, user: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    f = db.query(Favorito).filter(Favorito.usuario_id == user.id, Favorito.local_id == local_id).first()
    if f: db.delete(f); msg = "Quitado"
    else: db.add(Favorito(usuario_id=user.id, local_id=local_id)); msg = "Agregado"
    db.commit(); return {"mensaje": msg}

@app.get("/buscar/cercanos")
def buscar_cercanos(lat: float, lon: float, radio_km: float = 2.0, solo_abiertos: bool = False, db: Session = Depends(get_db)):
    ahora = datetime.now()
    activos = db.query(Local).filter(Local.pago_al_dia == True, Local.fecha_vencimiento > ahora, Local.latitud != None).all()
    locales_cercanos = []
    for l in activos:
        dist = calcular_distancia(lat, lon, l.latitud, l.longitud)
        if dist <= radio_km:
            info = calcular_estado_y_aura(l.horarios)
            if solo_abiertos and not info["esta_abierto"]: continue
            l_dict = {c.name: getattr(l, c.name) for c in l.__table__.columns}
            l_dict.update({"distancia_km": round(dist, 2), "estado_actual": info["texto"], "aura_color": info["color"]})
            locales_cercanos.append(l_dict)
    return sorted(locales_cercanos, key=lambda x: x["distancia_km"])

@app.get("/buscar")
def buscar(tipo: str, categoria: str, ciudad: str, solo_abiertos: bool = False, db: Session = Depends(get_db)):
    ahora = datetime.now()
    locales = db.query(Local).filter(Local.tipo == tipo, Local.categoria == categoria, Local.ciudad.ilike(f"%{ciudad}%"), Local.pago_al_dia == True, Local.fecha_vencimiento > ahora).all()
    resultado = []
    for l in locales:
        info = calcular_estado_y_aura(l.horarios)
        if solo_abiertos and not info["esta_abierto"]: continue
        l_dict = {c.name: getattr(l, c.name) for c in l.__table__.columns}
        l_dict.update({"estado_actual": info["texto"], "aura_color": info["color"]})
        if tipo == "SERVICIOS (A DOMICILIO)": l_dict["latitud"], l_dict["longitud"] = None, None
        resultado.append(l_dict)
    return resultado

@app.get("/local/detalle/{local_id}")
def detalle_local(local_id: int, db: Session = Depends(get_db)):
    l = db.query(Local).filter(Local.id == local_id).first()
    if not l: raise HTTPException(status_code=404)
    l.visitas += 1; db.commit()
    info = calcular_estado_y_aura(l.horarios)
    return {"local": l, "estado_actual": info["texto"], "aura_color": info["color"], "resenas": db.query(Resena).filter(Resena.local_id == local_id).all()}

@app.get("/config/categorias")
def get_cats(): return CATEGORIAS_MASTER