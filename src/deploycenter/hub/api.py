"""API HTTP del hub. Capa fina sobre `servicio.Hub`.

Dos puertas con credenciales distintas:

- `/api/agente/v1/*` — la usan los agentes, con su token propio en
  `Authorization: Bearer`. Nunca con la clave de servicio de la base, que no sale
  del perímetro de Accusys.
- `/api/v1/*` — la usa la web, con el JWT de la persona (Supabase Auth u otro
  proveedor). El token dice quién es; qué puede hacer lo dicen las tablas de
  usuarios, y lo vuelven a chequear las políticas RLS de la base. Sin validador
  configurado, esta puerta está cerrada.

Con `web`, el hub sirve además el frontend estático en `/` y su configuración
pública en `/config.json` (a qué proveedor de identidad hablarle). Mismo origen
para la web y la API: no hace falta CORS.

El long-poll es asíncrono: un agente esperando no ocupa un hilo, solo una
corrutina que consulta la base una vez por segundo (la consulta sí va al pool
de hilos, y dura milisegundos).
"""

import asyncio
import datetime
import time
from typing import Annotated, Any

from ..errores import ErrorDeployCenter
from . import servicio as srv

ESPERA_MAXIMA_S = 30


def crear_app(hub, validador=None, intervalo_poll_s=1.0, web=None, config_web=None):
    """`web` es la carpeta del frontend (o None para no servirlo) y `config_web`
    lo que se publica en /config.json: nada secreto, lo lee cualquiera."""
    try:
        from fastapi import Depends, FastAPI, Header, Query, Request, Response
        from fastapi.concurrency import run_in_threadpool
        from fastapi.responses import JSONResponse
        from pydantic import BaseModel, Field
    except ImportError as e:  # pragma: no cover
        raise ErrorDeployCenter(
            "el hub necesita FastAPI: instalá el paquete con el extra 'hub'") from e

    app = FastAPI(title="deployCenter hub", version="0.2.0",
                  docs_url=None, redoc_url=None, openapi_url=None)

    # -- cuerpos -------------------------------------------------------------- #

    class PedidoEnrolar(BaseModel):
        codigo: str = Field(max_length=40)
        host: str = Field(min_length=1, max_length=200)
        version: str | None = Field(default=None, max_length=40)

    class InstalacionReportada(BaseModel):
        nombre: str = Field(min_length=1, max_length=100)
        producto: str | None = Field(default=None, max_length=40)
        version: str | None = Field(default=None, max_length=40)
        estado: str | None = Field(default=None, max_length=20)
        bloqueada: bool = False
        punto_retorno: str | None = Field(default=None, max_length=40)

    class PedidoLatido(BaseModel):
        version: str | None = Field(default=None, max_length=40)
        instalaciones: list[InstalacionReportada] = Field(default_factory=list,
                                                           max_length=200)

    class Evento(BaseModel):
        ts: str | None = Field(default=None, max_length=40)
        evento: str = Field(max_length=60)
        datos: dict[str, Any] = Field(default_factory=dict)

    class PedidoEventos(BaseModel):
        eventos: list[Evento] = Field(max_length=srv.MAX_EVENTOS_POR_ENVIO)

    class PedidoResultado(BaseModel):
        resultado: str = Field(max_length=20)
        detalle: str | None = Field(default=None, max_length=4000)
        resumen: dict[str, Any] | None = None

    class PedidoCodigo(BaseModel):
        host: str | None = Field(default=None, max_length=200)

    class PedidoOrden(BaseModel):
        agente: str = Field(max_length=40)
        instalacion: str = Field(max_length=100)
        tipo: str = Field(max_length=20)
        release: str | None = Field(default=None, max_length=40)

    class PedidoTenant(BaseModel):
        id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")
        nombre: str = Field(min_length=1, max_length=200)

    class PedidoEstado(BaseModel):
        estado: str = Field(max_length=20)

    class PedidoProducto(BaseModel):
        mantenimiento_hasta: datetime.date
        autoservicio: bool = True
        canal: str = Field(default="estable", max_length=20)

    class PedidoHabilitacion(BaseModel):
        horas: int = Field(ge=1, le=srv.MAX_HORAS_HABILITACION)
        motivo: str | None = Field(default=None, max_length=500)

    class PedidoUsuario(BaseModel):
        rol: str = Field(max_length=20)
        tenant: str | None = Field(default=None, max_length=40)
        nombre: str | None = Field(default=None, max_length=200)
        email: str | None = Field(default=None, max_length=320)

    # -- errores -------------------------------------------------------------- #

    @app.exception_handler(srv.ErrorHub)
    async def _error_hub(_request: Request, e: srv.ErrorHub):
        return JSONResponse(status_code=e.codigo_http, content={"detalle": e.mensaje})

    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.orm.exc import StaleDataError

    @app.exception_handler(StaleDataError)
    async def _fila_no_modificada(_request: Request, _e: StaleDataError):
        """Con RLS, un UPDATE que la política no admite no toca filas."""
        return JSONResponse(status_code=403,
                            content={"detalle": "la base no permitió modificar ese registro"})

    @app.exception_handler(DBAPIError)
    async def _error_base(_request: Request, e: DBAPIError):
        """Si la RLS de la base rechaza algo que la aplicación dejó pasar, es un
        403 y no un 500: la segunda barrera funcionó."""
        mensaje = str(getattr(e, "orig", e))
        if "row-level security" in mensaje or "permission denied" in mensaje:
            return JSONResponse(status_code=403,
                                content={"detalle": "la base rechazó la operación por permisos"})
        raise e

    # -- credenciales ---------------------------------------------------------- #

    def _bearer(authorization):
        if not authorization or not authorization.lower().startswith("bearer "):
            return None
        return authorization[7:].strip()

    def agente_actual(authorization: str | None = Header(default=None)):
        return hub.autenticar(_bearer(authorization))

    AgenteActual = Annotated[dict, Depends(agente_actual)]

    def persona(authorization: str | None = Header(default=None)):
        if validador is None:
            raise srv.Prohibido("la API de la web no está habilitada: falta configurar la "
                                "identidad (SUPABASE_URL o DC_JWT_*)")
        return hub.perfil(validador.validar(_bearer(authorization)))

    Persona = Annotated[srv.Perfil, Depends(persona)]

    if web is not None:
        _cabeceras_web(app, config_web or {})

    # -- salud ------------------------------------------------------------------ #

    @app.get("/api/salud")
    def salud():
        return {"ok": True}

    # -- canal del agente ------------------------------------------------------ #

    @app.post("/api/agente/v1/enrolar")
    def enrolar(pedido: PedidoEnrolar):
        return hub.enrolar(pedido.codigo, pedido.host, pedido.version)

    @app.post("/api/agente/v1/latido")
    def latido(pedido: PedidoLatido, agente: AgenteActual):
        return hub.latido(agente["id"], pedido.version,
                          [i.model_dump() for i in pedido.instalaciones])

    @app.get("/api/agente/v1/ordenes/siguiente")
    async def siguiente(response: Response, agente: AgenteActual,
                        espera: Annotated[float, Query(ge=0)] = 0):
        limite = time.monotonic() + min(espera, ESPERA_MAXIMA_S)
        while True:
            orden = await run_in_threadpool(hub.tomar_orden, agente["id"])
            if orden is not None:
                return orden
            if time.monotonic() >= limite:
                response.status_code = 204
                return None
            await asyncio.sleep(intervalo_poll_s)

    @app.post("/api/agente/v1/ordenes/{orden_id}/eventos")
    def eventos(orden_id: str, pedido: PedidoEventos, agente: AgenteActual):
        return hub.registrar_eventos(agente["id"], orden_id,
                                     [e.model_dump() for e in pedido.eventos])

    @app.get("/api/agente/v1/ordenes/{orden_id}/control")
    def control(orden_id: str, agente: AgenteActual):
        return hub.control(agente["id"], orden_id)

    @app.post("/api/agente/v1/ordenes/{orden_id}/resultado")
    def resultado(orden_id: str, pedido: PedidoResultado, agente: AgenteActual):
        return hub.cerrar_orden(agente["id"], orden_id, pedido.resultado,
                                pedido.detalle, pedido.resumen)

    # -- web ------------------------------------------------------------------- #

    @app.get("/api/v1/yo")
    def yo(p: Persona):
        return p.a_dict()

    @app.get("/api/v1/catalogo")
    def catalogo(p: Persona):
        return hub.catalogo_web(perfil=p)

    @app.get("/api/v1/tenants")
    def tenants(p: Persona):
        return hub.tenants(perfil=p)

    @app.get("/api/v1/parque")
    def parque(p: Persona, tenant: str | None = None):
        return hub.parque(tenant_id=tenant, perfil=p)

    @app.get("/api/v1/ordenes")
    def ordenes(p: Persona, instalacion: str | None = None, tenant: str | None = None,
                limite: Annotated[int, Query(ge=1, le=500)] = 50):
        return hub.ordenes(perfil=p, tenant_id=tenant, instalacion=instalacion, limite=limite)

    @app.post("/api/v1/ordenes", status_code=201)
    def crear_orden(pedido: PedidoOrden, p: Persona):
        return hub.crear_orden(pedido.agente, pedido.instalacion, pedido.tipo,
                               release=pedido.release, perfil=p)

    @app.get("/api/v1/ordenes/{orden_id}")
    def ver_orden(orden_id: str, p: Persona):
        return hub.orden(orden_id, perfil=p)

    @app.post("/api/v1/ordenes/{orden_id}/cancelar")
    def cancelar(orden_id: str, p: Persona):
        hub.cancelar_orden(orden_id, perfil=p)
        return {"ok": True}

    @app.post("/api/v1/ordenes/{orden_id}/cancelar-rollback")
    def cancelar_rollback(orden_id: str, p: Persona):
        hub.pedir_cancelacion_rollback(orden_id, perfil=p)
        return {"ok": True}

    @app.post("/api/v1/tenants", status_code=201)
    def crear_tenant(pedido: PedidoTenant, p: Persona):
        return hub.crear_tenant(pedido.id, pedido.nombre, perfil=p)

    @app.get("/api/v1/tenants/{tenant_id}")
    def ver_tenant(tenant_id: str, p: Persona):
        return hub.parametria(tenant_id, perfil=p)

    @app.put("/api/v1/tenants/{tenant_id}/estado")
    def estado_tenant(tenant_id: str, pedido: PedidoEstado, p: Persona):
        hub.cambiar_estado_tenant(tenant_id, pedido.estado, perfil=p)
        return {"ok": True}

    @app.put("/api/v1/tenants/{tenant_id}/productos/{producto}")
    def adquirir(tenant_id: str, producto: str, pedido: PedidoProducto, p: Persona):
        hub.adquirir(tenant_id, producto, pedido.mantenimiento_hasta,
                     autoservicio=pedido.autoservicio, canal=pedido.canal, perfil=p)
        return hub.parametria(tenant_id, perfil=p)

    @app.post("/api/v1/tenants/{tenant_id}/codigos")
    def codigo(tenant_id: str, pedido: PedidoCodigo, p: Persona):
        return hub.emitir_codigo(tenant_id, host=pedido.host, perfil=p)

    @app.post("/api/v1/agentes/{agente_id}/revocar")
    def revocar(agente_id: str, p: Persona):
        hub.revocar_agente(agente_id, perfil=p)
        return {"ok": True}

    @app.get("/api/v1/habilitaciones")
    def habilitaciones(p: Persona, tenant: str | None = None):
        return hub.habilitaciones(perfil=p, tenant_id=tenant)

    @app.post("/api/v1/habilitaciones", status_code=201)
    def habilitar(pedido: PedidoHabilitacion, p: Persona):
        return hub.habilitar_asistencia(p, pedido.horas, pedido.motivo)

    @app.post("/api/v1/habilitaciones/{habilitacion_id}/revocar")
    def revocar_habilitacion(habilitacion_id: int, p: Persona):
        return hub.revocar_habilitacion(p, habilitacion_id)

    @app.get("/api/v1/usuarios")
    def usuarios(p: Persona, tenant: str | None = None):
        return hub.usuarios(perfil=p, tenant_id=tenant)

    @app.put("/api/v1/usuarios/{usuario_id}")
    def asignar_usuario(usuario_id: str, pedido: PedidoUsuario, p: Persona):
        return hub.asignar_usuario(usuario_id, pedido.rol, tenant_id=pedido.tenant,
                                   nombre=pedido.nombre, email=pedido.email, perfil=p)

    @app.delete("/api/v1/usuarios/{usuario_id}")
    def quitar_usuario(usuario_id: str, p: Persona):
        hub.quitar_usuario(usuario_id, perfil=p)
        return {"ok": True}

    @app.get("/api/v1/auditoria")
    def auditoria(p: Persona, tenant: str | None = None,
                  limite: Annotated[int, Query(ge=1, le=500)] = 100):
        return hub.auditoria(perfil=p, tenant_id=tenant, limite=limite)

    # -- frontend --------------------------------------------------------------- #
    # va al final: lo que no es de la API cae en los archivos de la web

    if web is not None:
        from fastapi.staticfiles import StaticFiles

        @app.get("/config.json")
        def config():
            return config_web or {}

        app.mount("/", StaticFiles(directory=str(web), html=True), name="web")

    return app


def _cabeceras_web(app, config_web):
    """La web guarda el token de la sesión en el navegador: la CSP limita de dónde
    puede cargar scripts (solo de acá) y a dónde puede conectarse (acá y al
    proveedor de identidad)."""
    conectar = " ".join(["'self'", *filter(None, [config_web.get("supabase_url")])])
    csp = ("default-src 'self'; script-src 'self'; "
           "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
           "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; "
           f"connect-src {conectar}; frame-ancestors 'none'; base-uri 'none'; "
           "form-action 'none'")

    @app.middleware("http")
    async def _cabeceras(request, call_next):
        respuesta = await call_next(request)
        respuesta.headers.setdefault("X-Content-Type-Options", "nosniff")
        respuesta.headers.setdefault("Referrer-Policy", "no-referrer")
        if not request.url.path.startswith("/api/"):
            respuesta.headers.setdefault("Content-Security-Policy", csp)
            respuesta.headers.setdefault("X-Frame-Options", "DENY")
            # la web cambia con cada versión del hub: que no quede una vieja en caché
            respuesta.headers.setdefault("Cache-Control", "no-cache")
        return respuesta
