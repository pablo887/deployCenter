"""API HTTP del hub. Capa fina sobre `servicio.Hub`.

Dos puertas con credenciales distintas:

- `/api/agente/v1/*` — la usan los agentes, con su token propio en
  `Authorization: Bearer`. Nunca con la clave de servicio de la base, que no sale
  del perímetro de Accusys.
- `/api/v1/*` — la usa la web. Hasta que entre Supabase Auth, la protege un
  token de administración (`DC_HUB_TOKEN_ADMIN`). Sin ese token configurado, la
  puerta está cerrada.

El long-poll es asíncrono: un agente esperando no ocupa un hilo, solo una
corrutina que consulta la base una vez por segundo (la consulta sí va al pool
de hilos, y dura milisegundos).
"""

import asyncio
import hmac
import time
from typing import Annotated, Any

from ..errores import ErrorDeployCenter
from . import servicio as srv

ESPERA_MAXIMA_S = 30


def crear_app(hub, token_admin=None, intervalo_poll_s=1.0):
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
        pedida_por: str = Field(min_length=1, max_length=200)

    # -- errores -------------------------------------------------------------- #

    @app.exception_handler(srv.ErrorHub)
    async def _error_hub(_request: Request, e: srv.ErrorHub):
        return JSONResponse(status_code=e.codigo_http, content={"detalle": e.mensaje})

    # -- credenciales ---------------------------------------------------------- #

    def _bearer(authorization):
        if not authorization or not authorization.lower().startswith("bearer "):
            return None
        return authorization[7:].strip()

    def agente_actual(authorization: str | None = Header(default=None)):
        return hub.autenticar(_bearer(authorization))

    AgenteActual = Annotated[dict, Depends(agente_actual)]

    def admin(authorization: str | None = Header(default=None)):
        if not token_admin:
            raise srv.Prohibido("la API de administración no está habilitada en este hub")
        recibido = _bearer(authorization) or ""
        if not hmac.compare_digest(recibido.encode(), token_admin.encode()):
            raise srv.NoAutorizado("token de administración inválido")
        return True

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

    # -- web (administración, provisoria hasta Supabase Auth) ------------------ #

    @app.post("/api/v1/tenants/{tenant_id}/codigos", dependencies=[Depends(admin)])
    def codigo(tenant_id: str, pedido: PedidoCodigo):
        return hub.emitir_codigo(tenant_id, host=pedido.host)

    @app.get("/api/v1/parque", dependencies=[Depends(admin)])
    def parque(tenant: str | None = None):
        return hub.parque(tenant_id=tenant)

    @app.post("/api/v1/ordenes", status_code=201, dependencies=[Depends(admin)])
    def crear_orden(pedido: PedidoOrden):
        return hub.crear_orden(pedido.agente, pedido.instalacion, pedido.tipo,
                               release=pedido.release, pedida_por=pedido.pedida_por)

    @app.get("/api/v1/ordenes/{orden_id}", dependencies=[Depends(admin)])
    def ver_orden(orden_id: str):
        return hub.orden(orden_id)

    @app.post("/api/v1/ordenes/{orden_id}/cancelar", dependencies=[Depends(admin)])
    def cancelar(orden_id: str):
        hub.cancelar_orden(orden_id)
        return {"ok": True}

    @app.post("/api/v1/ordenes/{orden_id}/cancelar-rollback", dependencies=[Depends(admin)])
    def cancelar_rollback(orden_id: str):
        hub.pedir_cancelacion_rollback(orden_id)
        return {"ok": True}

    @app.post("/api/v1/agentes/{agente_id}/revocar", dependencies=[Depends(admin)])
    def revocar(agente_id: str):
        hub.revocar_agente(agente_id)
        return {"ok": True}

    return app
