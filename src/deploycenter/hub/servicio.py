"""Reglas del hub, sin HTTP de por medio.

Todo lo que decide qué se le puede pedir a un agente está acá:

- **Enrolamiento.** Un código de un solo uso, con vencimiento, se canjea por un
  token propio del agente. Del token el hub guarda solo el hash: una copia de la
  base no alcanza para hacerse pasar por un agente.
- **Órdenes.** La habilitación se evalúa al encolar, no al ejecutar: un
  despliegue ya autorizado se completa aunque el mantenimiento venza en el medio.
  El rollback no se bloquea nunca por mantenimiento ni por suspensión.
- **Una orden abierta por instalación.** Es el mismo lock que toma el agente,
  adelantado: la segunda orden se rechaza en la web con el motivo a la vista, en
  vez de fallar en el servidor del cliente.
"""

import datetime
import hashlib
import secrets
import uuid
from contextlib import contextmanager

from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from .. import manifiesto as mf
from ..errores import ErrorArchivo, ErrorDeployCenter
from . import modelos as m
from .catalogo import Catalogo

# El agente hace long-poll y además manda un latido cada 30 s. Si en 90 s no
# apareció, está fuera de línea y el hub bloquea las órdenes para ese host.
UMBRAL_EN_LINEA_S = 90
VIGENCIA_CODIGO_H = 24
MAX_EVENTOS_POR_ENVIO = 500

# sin 0/O ni 1/I/L: el código se dicta por teléfono
ALFABETO_CODIGO = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

RESULTADOS = ("ok", "revertido", "degradado", "abortado", "rechazada", "bloqueada", "error")


class ErrorHub(ErrorDeployCenter):
    codigo_http = 400

    def __init__(self, mensaje):
        super().__init__(mensaje)
        self.mensaje = mensaje


class NoAutorizado(ErrorHub):
    codigo_http = 401


class Prohibido(ErrorHub):
    codigo_http = 403


class NoEncontrado(ErrorHub):
    codigo_http = 404


class Conflicto(ErrorHub):
    codigo_http = 409


class Rechazado(ErrorHub):
    """La orden es entendible pero la parametría o el estado no la permiten."""

    codigo_http = 422


def hash_secreto(valor):
    return hashlib.sha256(valor.encode("utf-8")).hexdigest()


def normalizar_codigo(codigo):
    return "".join(c for c in (codigo or "").upper() if c.isalnum())


def ahora_utc():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None, microsecond=0)


def _iso(fecha):
    return fecha.isoformat() + "Z" if fecha else None


def conectar(url_db):
    """Engine con las mínimas decisiones que importan para SQLite."""
    engine = create_engine(url_db, future=True)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def _pragmas(conexion, _registro):  # pragma: no cover - trivial
            cursor = conexion.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return engine


class Hub:
    def __init__(self, engine, catalogo, reloj=ahora_utc,
                 umbral_en_linea_s=UMBRAL_EN_LINEA_S):
        self.engine = engine
        self.catalogo = catalogo if isinstance(catalogo, Catalogo) else Catalogo(catalogo)
        self.reloj = reloj
        self.umbral_en_linea_s = umbral_en_linea_s
        self._sesiones = sessionmaker(bind=engine, expire_on_commit=False)

    @classmethod
    def desde_url(cls, url_db, raiz_catalogo, **kw):
        hub = cls(conectar(url_db), raiz_catalogo, **kw)
        hub.crear_tablas()
        return hub

    def crear_tablas(self):
        m.Base.metadata.create_all(self.engine)

    @contextmanager
    def sesion(self):
        with self._sesiones() as s, s.begin():
            yield s

    # ------------------------------------------------------------------ #
    # parametría (la carga Accusys)
    # ------------------------------------------------------------------ #

    def crear_tenant(self, tenant_id, nombre):
        with self.sesion() as s:
            if s.get(m.Tenant, tenant_id):
                raise Conflicto(f"ya existe el cliente {tenant_id!r}")
            s.add(m.Tenant(id=tenant_id, nombre=nombre, estado="activo"))
        return {"id": tenant_id, "nombre": nombre, "estado": "activo"}

    def cambiar_estado_tenant(self, tenant_id, estado):
        if estado not in ("activo", "suspendido"):
            raise Rechazado("el estado de un cliente es activo o suspendido")
        with self.sesion() as s:
            t = self._tenant(s, tenant_id)
            t.estado = estado

    def adquirir(self, tenant_id, producto, mantenimiento_hasta, autoservicio=True,
                 canal="estable"):
        if canal not in ("estable", "anticipado"):
            raise Rechazado("el canal es estable o anticipado")
        if isinstance(mantenimiento_hasta, str):
            mantenimiento_hasta = datetime.date.fromisoformat(mantenimiento_hasta)
        try:
            self.catalogo.datos_producto(producto)
        except ErrorArchivo as e:
            raise NoEncontrado(str(e)) from e
        with self.sesion() as s:
            self._tenant(s, tenant_id)
            tp = s.get(m.TenantProducto, (tenant_id, producto))
            if tp is None:
                tp = m.TenantProducto(tenant_id=tenant_id, producto=producto)
                s.add(tp)
            tp.mantenimiento_hasta = mantenimiento_hasta
            tp.autoservicio = autoservicio
            tp.canal = canal

    # ------------------------------------------------------------------ #
    # enrolamiento
    # ------------------------------------------------------------------ #

    def emitir_codigo(self, tenant_id, host=None, vigencia_h=VIGENCIA_CODIGO_H):
        """Devuelve el código en claro. Es la única vez que se ve: acá queda el hash."""
        cuerpo = "".join(secrets.choice(ALFABETO_CODIGO) for _ in range(8))
        codigo = f"DC-{cuerpo[:4]}-{cuerpo[4:]}"
        creado = self.reloj()
        expira = creado + datetime.timedelta(hours=vigencia_h)
        with self.sesion() as s:
            self._tenant(s, tenant_id)
            s.add(m.CodigoEnrolamiento(
                hash=hash_secreto(normalizar_codigo(codigo)), tenant_id=tenant_id,
                host=host, creado=creado, expira=expira))
        return {"codigo": codigo, "tenant": tenant_id, "host": host, "expira": _iso(expira)}

    def enrolar(self, codigo, host, version=None):
        """Canjea el código por credenciales propias del agente."""
        if not host:
            raise Rechazado("el agente tiene que informar el nombre del host")
        with self.sesion() as s:
            registro = s.scalar(select(m.CodigoEnrolamiento).where(
                m.CodigoEnrolamiento.hash == hash_secreto(normalizar_codigo(codigo))))
            # mismo mensaje para inexistente, usado y vencido: no se le da pista a
            # quien esté probando códigos
            if registro is None or registro.usado is not None or registro.expira < self.reloj():
                raise NoAutorizado("el código de enrolamiento no es válido o ya se usó")

            token = secrets.token_urlsafe(32)
            agente_id = "ag-" + uuid.uuid4().hex[:10]
            ahora = self.reloj()
            s.add(m.Agente(id=agente_id, tenant_id=registro.tenant_id, host=host,
                           token_hash=hash_secreto(token), version=version,
                           estado="activo", enrolado=ahora, ultimo_contacto=ahora))
            registro.usado = ahora
            registro.agente_id = agente_id
            tenant_id = registro.tenant_id
        return {"agente_id": agente_id, "token": token, "tenant": tenant_id}

    def autenticar(self, token):
        """Resuelve el token de un agente. Cada llamada autenticada cuenta como contacto."""
        if not token:
            raise NoAutorizado("falta el token del agente")
        with self.sesion() as s:
            agente = s.scalar(select(m.Agente).where(m.Agente.token_hash == hash_secreto(token)))
            if agente is None or agente.estado != "activo":
                raise NoAutorizado("token de agente inválido o revocado")
            agente.ultimo_contacto = self.reloj()
            return {"id": agente.id, "tenant": agente.tenant_id, "host": agente.host}

    def revocar_agente(self, agente_id):
        """Baja desde el hub, sin tocar el servidor: el token deja de servir y las
        órdenes que no llegó a tomar se cancelan."""
        with self.sesion() as s:
            agente = self._agente(s, agente_id)
            agente.estado = "revocado"
            for orden in s.scalars(select(m.Orden).where(
                    m.Orden.agente_id == agente_id, m.Orden.estado == m.PENDIENTE)):
                orden.estado = m.CANCELADA
                orden.detalle = "agente revocado"

    # ------------------------------------------------------------------ #
    # latido
    # ------------------------------------------------------------------ #

    def latido(self, agente_id, version=None, instalaciones=()):
        """El agente informa qué corre en su host. Lo que no informa, ya no está."""
        ahora = self.reloj()
        with self.sesion() as s:
            agente = self._agente(s, agente_id)
            agente.version = version or agente.version
            agente.ultimo_contacto = ahora

            actuales = {i.nombre: i for i in s.scalars(
                select(m.Instalacion).where(m.Instalacion.agente_id == agente_id))}
            reportadas = set()
            for dato in instalaciones:
                nombre = str(dato.get("nombre") or "")
                if not nombre:
                    continue
                reportadas.add(nombre)
                inst = actuales.get(nombre)
                if inst is None:
                    inst = m.Instalacion(agente_id=agente_id, nombre=nombre)
                    s.add(inst)
                inst.producto = dato.get("producto")
                inst.version = dato.get("version")
                inst.estado = dato.get("estado")
                inst.bloqueada = bool(dato.get("bloqueada"))
                inst.punto_retorno = dato.get("punto_retorno")
                inst.actualizado = ahora
            for nombre, inst in actuales.items():
                if nombre not in reportadas:
                    s.delete(inst)
        return {"hora": _iso(ahora)}

    def en_linea(self, agente):
        if agente.estado != "activo" or agente.ultimo_contacto is None:
            return False
        return (self.reloj() - agente.ultimo_contacto).total_seconds() <= self.umbral_en_linea_s

    # ------------------------------------------------------------------ #
    # órdenes: lado de la web
    # ------------------------------------------------------------------ #

    def crear_orden(self, agente_id, instalacion, tipo, release=None, pedida_por="?"):
        if tipo not in m.TIPOS:
            raise Rechazado(f"tipo de orden desconocido: {tipo!r}")
        with self.sesion() as s:
            agente = self._agente(s, agente_id)
            tenant = self._tenant(s, agente.tenant_id)
            if agente.estado != "activo":
                raise Rechazado(f"el agente de {agente.host} está revocado")
            if not self.en_linea(agente):
                raise Rechazado(
                    f"el agente de {agente.host} está fuera de línea "
                    f"(último contacto {_iso(agente.ultimo_contacto) or 'nunca'}); "
                    f"la orden no tendría quién la ejecute")

            inst = s.scalar(select(m.Instalacion).where(
                m.Instalacion.agente_id == agente_id, m.Instalacion.nombre == instalacion))
            if inst is None:
                raise NoEncontrado(
                    f"el agente de {agente.host} no reporta una instalación llamada "
                    f"{instalacion!r}")

            abierta = s.scalar(select(m.Orden).where(
                m.Orden.agente_id == agente_id, m.Orden.instalacion == instalacion,
                m.Orden.estado.in_(m.ABIERTAS)))
            if abierta is not None:
                raise Conflicto(
                    f"ya hay una orden abierta sobre {instalacion} "
                    f"({abierta.id}, {abierta.tipo}, {abierta.estado})")

            producto = inst.producto
            if tipo == m.ROLLBACK:
                # nunca se bloquea: ni por mantenimiento vencido ni por suspensión
                if not inst.punto_retorno:
                    raise Rechazado(f"{instalacion} no tiene punto de retorno registrado")
                release = inst.punto_retorno
            else:
                self._validar_habilitacion(s, tenant, inst, tipo, release)

            orden = m.Orden(
                id="ord-" + uuid.uuid4().hex[:10], tenant_id=tenant.id, agente_id=agente_id,
                instalacion=instalacion, tipo=tipo, producto=producto, release=release,
                estado=m.PENDIENTE, pedida_por=pedida_por, creada=self.reloj())
            s.add(orden)
            try:
                s.flush()
            except IntegrityError:
                raise Conflicto(
                    f"ya hay una orden abierta sobre {instalacion}") from None
            return self._orden_a_dict(orden)

    def _validar_habilitacion(self, s, tenant, inst, tipo, release):
        if tenant.estado != "activo":
            raise Rechazado(f"{tenant.nombre} está suspendido: el autoservicio está cortado")
        if not release:
            raise Rechazado(f"una orden de {tipo} necesita la versión")
        if not inst.producto:
            raise Rechazado(
                f"{inst.nombre} no reporta producto: la instalación inicial la hace Accusys")

        tp = s.get(m.TenantProducto, (tenant.id, inst.producto))
        if tp is None:
            raise Rechazado(f"{tenant.nombre} no tiene adquirido {inst.producto}")

        manifiesto = self.catalogo.release(inst.producto, release)
        if manifiesto is None:
            raise NoEncontrado(f"no existe el release {inst.producto} {release}")
        problemas = mf.validar(manifiesto, exigir_pin=True)
        if problemas:
            raise Rechazado(f"el release {release} no es publicable: {problemas[0]}")
        if manifiesto.get("canal") == "anticipado" and tp.canal != "anticipado":
            raise Rechazado(f"{release} es del canal anticipado y el cliente está en estable")
        if not mf.es_autoservicio(manifiesto):
            raise Rechazado(
                f"{release} trae migraciones de base: va por circuito asistido con Accusys")
        if not tp.autoservicio:
            raise Rechazado(
                f"{tenant.nombre} tiene el autoservicio de {inst.producto} deshabilitado")
        if not mf.habilitado_para(manifiesto, tp.mantenimiento_hasta):
            hasta = tp.mantenimiento_hasta.isoformat() if tp.mantenimiento_hasta else "sin fecha"
            raise Rechazado(
                f"{release} se publicó el {manifiesto['publicado']}, después del fin del "
                f"mantenimiento ({hasta})")
        if inst.version:
            if inst.version == release and tipo == m.DESPLEGAR:
                raise Rechazado(f"{release} ya está instalada en {inst.nombre}")
            if inst.version != release and not mf.permite_upgrade_desde(manifiesto, inst.version):
                raise Rechazado(
                    f"no se puede saltar de {inst.version} a {release}: el release admite "
                    f"{manifiesto['desde_version']}")

    def cancelar_orden(self, orden_id):
        with self.sesion() as s:
            orden = self._orden(s, orden_id)
            if orden.estado != m.PENDIENTE:
                raise Conflicto(
                    f"{orden_id} ya la tomó el agente ({orden.estado}); no se puede retirar")
            orden.estado = m.CANCELADA
            orden.terminada = self.reloj()

    def pedir_cancelacion_rollback(self, orden_id):
        """El operador frena la vuelta atrás desde la web. Viaja al agente en la
        respuesta a su próximo envío de eventos o consulta de control."""
        with self.sesion() as s:
            orden = self._orden(s, orden_id)
            if orden.tipo != m.DESPLEGAR or orden.estado not in (m.ENTREGADA, m.EN_CURSO):
                raise Conflicto(f"{orden_id} no es un despliegue en curso")
            orden.cancelar_rollback = True

    def orden(self, orden_id):
        with self.sesion() as s:
            orden = self._orden(s, orden_id)
            datos = self._orden_a_dict(orden)
            datos["eventos"] = [
                {"ts": e.ts_agente or _iso(e.recibido), "evento": e.evento, "datos": e.datos}
                for e in s.scalars(select(m.EventoOrden).where(
                    m.EventoOrden.orden_id == orden_id).order_by(m.EventoOrden.id))
            ]
            return datos

    def parque(self, tenant_id=None):
        with self.sesion() as s:
            consulta = select(m.Agente).order_by(m.Agente.tenant_id, m.Agente.host)
            if tenant_id:
                consulta = consulta.where(m.Agente.tenant_id == tenant_id)
            salida = []
            for agente in s.scalars(consulta):
                instalaciones = s.scalars(select(m.Instalacion).where(
                    m.Instalacion.agente_id == agente.id).order_by(m.Instalacion.nombre))
                salida.append({
                    "id": agente.id, "tenant": agente.tenant_id, "host": agente.host,
                    "version": agente.version, "estado": agente.estado,
                    "en_linea": self.en_linea(agente),
                    "ultimo_contacto": _iso(agente.ultimo_contacto),
                    "instalaciones": [
                        {"nombre": i.nombre, "producto": i.producto, "version": i.version,
                         "estado": i.estado, "bloqueada": i.bloqueada,
                         "punto_retorno": i.punto_retorno}
                        for i in instalaciones
                    ],
                })
            return salida

    # ------------------------------------------------------------------ #
    # órdenes: lado del agente
    # ------------------------------------------------------------------ #

    def tomar_orden(self, agente_id):
        """La orden más vieja pendiente para este agente, con su paquete. La marca
        entregada en la misma transacción: nunca se entrega dos veces."""
        with self.sesion() as s:
            orden = s.scalar(
                select(m.Orden)
                .where(m.Orden.agente_id == agente_id, m.Orden.estado == m.PENDIENTE)
                .order_by(m.Orden.creada, m.Orden.id)
                .limit(1)
                .with_for_update(skip_locked=True))
            if orden is None:
                return None
            datos = self._orden_a_dict(orden)
            if orden.tipo in (m.PREFLIGHT, m.DESPLEGAR):
                try:
                    datos["paquete"] = self.catalogo.paquete(orden.producto, orden.release)
                except ErrorArchivo as e:
                    orden.estado = m.TERMINADA
                    orden.resultado = "error"
                    orden.detalle = f"el hub no pudo armar el paquete: {e}"
                    orden.terminada = self.reloj()
                    return None
            orden.estado = m.ENTREGADA
            orden.entregada = self.reloj()
            datos["estado"] = m.ENTREGADA
            return datos

    def registrar_eventos(self, agente_id, orden_id, eventos):
        if len(eventos) > MAX_EVENTOS_POR_ENVIO:
            raise Rechazado(f"a lo sumo {MAX_EVENTOS_POR_ENVIO} eventos por envío")
        ahora = self.reloj()
        with self.sesion() as s:
            orden = self._orden_del_agente(s, agente_id, orden_id)
            if orden.estado == m.ENTREGADA:
                orden.estado = m.EN_CURSO
            for e in eventos:
                s.add(m.EventoOrden(orden_id=orden_id, recibido=ahora,
                                    ts_agente=str(e.get("ts") or "")[:40] or None,
                                    evento=str(e.get("evento") or "?")[:60],
                                    datos=e.get("datos") or {}))
            return {"cancelar_rollback": orden.cancelar_rollback}

    def control(self, agente_id, orden_id):
        with self.sesion() as s:
            orden = self._orden_del_agente(s, agente_id, orden_id)
            return {"estado": orden.estado, "cancelar_rollback": orden.cancelar_rollback}

    def cerrar_orden(self, agente_id, orden_id, resultado, detalle=None, resumen=None):
        if resultado not in RESULTADOS:
            raise Rechazado(f"resultado desconocido: {resultado!r}")
        with self.sesion() as s:
            orden = self._orden_del_agente(s, agente_id, orden_id)
            if orden.estado == m.TERMINADA:
                # reintento del buzón del agente: el primero ya llegó
                return {"estado": orden.estado, "repetido": True}
            if orden.estado not in (m.ENTREGADA, m.EN_CURSO):
                raise Conflicto(f"{orden_id} está {orden.estado}")
            orden.estado = m.TERMINADA
            orden.resultado = resultado
            orden.detalle = detalle
            orden.resumen = resumen
            orden.terminada = self.reloj()
            return {"estado": orden.estado, "repetido": False}

    # ------------------------------------------------------------------ #
    # auxiliares
    # ------------------------------------------------------------------ #

    def _tenant(self, s, tenant_id):
        t = s.get(m.Tenant, tenant_id)
        if t is None:
            raise NoEncontrado(f"no existe el cliente {tenant_id!r}")
        return t

    def _agente(self, s, agente_id):
        a = s.get(m.Agente, agente_id)
        if a is None:
            raise NoEncontrado(f"no existe el agente {agente_id!r}")
        return a

    def _orden(self, s, orden_id):
        o = s.get(m.Orden, orden_id)
        if o is None:
            raise NoEncontrado(f"no existe la orden {orden_id!r}")
        return o

    def _orden_del_agente(self, s, agente_id, orden_id):
        o = s.get(m.Orden, orden_id)
        # la orden de otro agente no existe para este: ni siquiera se confirma el id
        if o is None or o.agente_id != agente_id:
            raise NoEncontrado(f"no existe la orden {orden_id!r}")
        return o

    @staticmethod
    def _orden_a_dict(o):
        return {
            "id": o.id, "tenant": o.tenant_id, "agente": o.agente_id,
            "instalacion": o.instalacion, "tipo": o.tipo, "producto": o.producto,
            "release": o.release, "estado": o.estado, "resultado": o.resultado,
            "detalle": o.detalle, "resumen": o.resumen, "pedida_por": o.pedida_por,
            "cancelar_rollback": o.cancelar_rollback, "creada": _iso(o.creada),
            "entregada": _iso(o.entregada), "terminada": _iso(o.terminada),
        }

