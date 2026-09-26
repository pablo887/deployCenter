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
- **Quién puede qué.** Cada operación de la web recibe el perfil de la persona
  y lo chequea acá. En Postgres, además, la transacción corre con su identidad
  (rol `authenticated` y sus claims), así que la RLS de la base es una segunda
  barrera independiente: si este código se equivocara, la base igual rechaza.
  Sin perfil (`perfil=None`) opera el sistema: el canal de los agentes y la
  consola `dc-hub`, que corren dentro del perímetro de Accusys.
"""

import datetime
import hashlib
import json
import secrets
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field

from sqlalchemy import create_engine, event, select, text
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


@dataclass(frozen=True)
class Perfil:
    """La persona detrás de un pedido de la web, con el rol que dicen las tablas."""

    usuario_id: str
    rol: str
    tenant: str | None = None
    nombre: str | None = None
    email: str | None = None
    claims: dict = field(default_factory=dict, compare=False, repr=False)

    @property
    def es_accusys(self):
        return self.rol in m.ROLES_ACCUSYS

    @property
    def etiqueta(self):
        return self.nombre or self.email or self.usuario_id

    def a_dict(self):
        return {"usuario_id": self.usuario_id, "rol": self.rol, "tenant": self.tenant,
                "nombre": self.nombre, "email": self.email, "accusys": self.es_accusys}


CONSOLA = "dc-hub (consola)"
MAX_HORAS_HABILITACION = 72


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
        """En SQLite crea las tablas. En Postgres no: el esquema, con su RLS, lo
        crean las migraciones (`dc-hub migrar` o `supabase db push`)."""
        hub = cls(conectar(url_db), raiz_catalogo, **kw)
        if hub.es_sqlite:
            hub.crear_tablas()
        return hub

    @property
    def es_sqlite(self):
        return self.engine.dialect.name == "sqlite"

    def crear_tablas(self):
        m.Base.metadata.create_all(self.engine)

    @contextmanager
    def sesion(self, perfil=None):
        """Una transacción. Con perfil, en Postgres, corre con la identidad de la
        persona y la RLS aplica; sin perfil, con la credencial del hub."""
        with self._sesiones() as s, s.begin():
            if perfil is not None and not self.es_sqlite:
                claims = perfil.claims or {"sub": perfil.usuario_id, "aal": "aal2",
                                           "role": "authenticated"}
                s.execute(text("select set_config('request.jwt.claims', :c, true)"),
                          {"c": json.dumps(claims)})
                s.execute(text("set local role authenticated"))
            yield s

    # ------------------------------------------------------------------ #
    # identidad y permisos
    # ------------------------------------------------------------------ #

    def perfil(self, claims):
        """Resuelve el perfil a partir de un token ya validado. El rol sale de las
        tablas, no del token: quitarlo tiene efecto en el próximo pedido."""
        usuario_id = str(claims.get("sub") or "")
        try:
            usuario_id = str(uuid.UUID(usuario_id))
        except ValueError:
            raise NoAutorizado("el token no identifica a un usuario") from None
        with self.sesion() as s:
            ut = s.get(m.UsuarioTenant, usuario_id)
            if ut is not None:
                return Perfil(usuario_id, ut.rol, ut.tenant_id, ut.nombre,
                              ut.email or claims.get("email"), dict(claims))
            ua = s.get(m.UsuarioAccusys, usuario_id)
            if ua is not None:
                return Perfil(usuario_id, ua.rol, None, ua.nombre,
                              ua.email or claims.get("email"), dict(claims))
        raise Prohibido("tu usuario no tiene alta en deployCenter; pedísela al Aprobador "
                        "de tu organización")

    @staticmethod
    def _exigir(perfil, *roles):
        if perfil is not None and perfil.rol not in roles:
            raise Prohibido(f"tu rol ({perfil.rol}) no permite esta operación")

    @staticmethod
    def _ve(perfil, tenant_id):
        return perfil is None or perfil.es_accusys or perfil.tenant == tenant_id

    def _exigir_operar(self, s, perfil, tenant_id):
        """Ordena el Operador del propio cliente, o Soporte de Accusys con una
        habilitación vigente de ese cliente. Nadie más."""
        if perfil is None:
            return
        if perfil.rol == "operador" and perfil.tenant == tenant_id:
            return
        if perfil.rol == "soporte":
            if self._habilitacion_vigente(s, tenant_id) is None:
                raise Prohibido(
                    f"Accusys no opera sobre {tenant_id} sin una habilitación vigente del "
                    f"cliente; la otorga su Aprobador")
            return
        raise Prohibido(f"tu rol ({perfil.rol}) no puede ordenar sobre esta instalación")

    def _habilitacion_vigente(self, s, tenant_id):
        return s.scalar(select(m.Habilitacion).where(
            m.Habilitacion.tenant_id == tenant_id, m.Habilitacion.revocada.is_(None),
            m.Habilitacion.vence > self.reloj()).order_by(m.Habilitacion.vence.desc()).limit(1))

    def _auditar(self, s, perfil, accion, tenant_id=None, **detalle):
        s.add(m.Auditoria(
            fecha=self.reloj(), usuario_id=perfil.usuario_id if perfil else None,
            usuario=perfil.etiqueta if perfil else CONSOLA, tenant_id=tenant_id,
            accion=accion, detalle=detalle or None))

    # ------------------------------------------------------------------ #
    # parametría (la carga Accusys)
    # ------------------------------------------------------------------ #

    def crear_tenant(self, tenant_id, nombre, perfil=None):
        self._exigir(perfil, "comercial")
        with self.sesion(perfil) as s:
            if s.get(m.Tenant, tenant_id):
                raise Conflicto(f"ya existe el cliente {tenant_id!r}")
            s.add(m.Tenant(id=tenant_id, nombre=nombre, estado="activo"))
            s.flush()
            self._auditar(s, perfil, "cliente_alta", tenant_id, nombre=nombre)
        return {"id": tenant_id, "nombre": nombre, "estado": "activo"}

    def cambiar_estado_tenant(self, tenant_id, estado, perfil=None):
        if estado not in ("activo", "suspendido"):
            raise Rechazado("el estado de un cliente es activo o suspendido")
        self._exigir(perfil, "comercial")
        with self.sesion(perfil) as s:
            t = self._tenant(s, tenant_id)
            t.estado = estado
            self._auditar(s, perfil, "cliente_estado", tenant_id, estado=estado)

    def adquirir(self, tenant_id, producto, mantenimiento_hasta, autoservicio=True,
                 canal="estable", perfil=None):
        self._exigir(perfil, "comercial")
        if canal not in ("estable", "anticipado"):
            raise Rechazado("el canal es estable o anticipado")
        if isinstance(mantenimiento_hasta, str):
            mantenimiento_hasta = datetime.date.fromisoformat(mantenimiento_hasta)
        try:
            self.catalogo.datos_producto(producto)
        except ErrorArchivo as e:
            raise NoEncontrado(str(e)) from e
        with self.sesion(perfil) as s:
            self._tenant(s, tenant_id)
            tp = s.get(m.TenantProducto, (tenant_id, producto))
            if tp is None:
                tp = m.TenantProducto(tenant_id=tenant_id, producto=producto)
                s.add(tp)
            tp.mantenimiento_hasta = mantenimiento_hasta
            tp.autoservicio = autoservicio
            tp.canal = canal
            self._auditar(s, perfil, "parametria", tenant_id, producto=producto,
                          mantenimiento_hasta=mantenimiento_hasta.isoformat()
                          if mantenimiento_hasta else None,
                          autoservicio=autoservicio, canal=canal)

    def parametria(self, tenant_id, perfil=None):
        if not self._ve(perfil, tenant_id):
            raise NoEncontrado(f"no existe el cliente {tenant_id!r}")
        with self.sesion(perfil) as s:
            t = self._tenant(s, tenant_id)
            productos = s.scalars(select(m.TenantProducto).where(
                m.TenantProducto.tenant_id == tenant_id).order_by(m.TenantProducto.producto))
            return {"id": t.id, "nombre": t.nombre, "estado": t.estado, "productos": [
                {"producto": p.producto, "canal": p.canal, "autoservicio": p.autoservicio,
                 "mantenimiento_hasta": p.mantenimiento_hasta.isoformat()
                 if p.mantenimiento_hasta else None}
                for p in productos]}

    def tenants(self, perfil=None):
        """Los clientes con su parametría. Un cliente se ve solo a sí mismo."""
        with self.sesion(perfil) as s:
            consulta = select(m.Tenant).order_by(m.Tenant.nombre)
            if perfil is not None and not perfil.es_accusys:
                consulta = consulta.where(m.Tenant.id == perfil.tenant)
            ids = [t.id for t in s.scalars(consulta)]
        return [self.parametria(t, perfil=perfil) for t in ids]

    def catalogo_web(self, perfil=None):
        """Productos y releases publicados, para mostrar en la web. Un cliente ve
        solo los productos que adquirió; qué puede ordenar lo decide
        `crear_orden` al encolar, no esta lista."""
        adquiridos = None
        if perfil is not None and not perfil.es_accusys:
            adquiridos = {p["producto"] for p in self.parametria(perfil.tenant,
                                                                   perfil=perfil)["productos"]}
        salida = []
        for codigo in self.catalogo.productos():
            if adquiridos is not None and codigo not in adquiridos:
                continue
            datos = self.catalogo.datos_producto(codigo)
            salida.append({
                "id": codigo, "nombre": datos.get("nombre", codigo),
                "descripcion": datos.get("descripcion", ""),
                "releases": [{"manifiesto": r,
                              "changelog": self.catalogo.novedades(codigo, r["release"])}
                             for r in self.catalogo.releases(codigo)],
            })
        return salida

    # ------------------------------------------------------------------ #
    # enrolamiento
    # ------------------------------------------------------------------ #

    def emitir_codigo(self, tenant_id, host=None, vigencia_h=VIGENCIA_CODIGO_H, perfil=None):
        """Devuelve el código en claro. Es la única vez que se ve: acá queda el hash."""
        self._exigir(perfil, "soporte")
        cuerpo = "".join(secrets.choice(ALFABETO_CODIGO) for _ in range(8))
        codigo = f"DC-{cuerpo[:4]}-{cuerpo[4:]}"
        creado = self.reloj()
        expira = creado + datetime.timedelta(hours=vigencia_h)
        with self.sesion(perfil) as s:
            self._tenant(s, tenant_id)
            s.add(m.CodigoEnrolamiento(
                hash=hash_secreto(normalizar_codigo(codigo)), tenant_id=tenant_id,
                host=host, creado=creado, expira=expira))
            self._auditar(s, perfil, "codigo_enrolamiento", tenant_id, host=host)
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

    def revocar_agente(self, agente_id, perfil=None):
        """Baja desde el hub, sin tocar el servidor: el token deja de servir y las
        órdenes que no llegó a tomar se cancelan."""
        self._exigir(perfil, "soporte")
        with self.sesion(perfil) as s:
            agente = self._agente(s, agente_id)
            agente.estado = "revocado"
            for orden in s.scalars(select(m.Orden).where(
                    m.Orden.agente_id == agente_id, m.Orden.estado == m.PENDIENTE)):
                orden.estado = m.CANCELADA
                orden.detalle = "agente revocado"
            self._auditar(s, perfil, "agente_revocado", agente.tenant_id,
                          agente=agente_id, host=agente.host)

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

    def crear_orden(self, agente_id, instalacion, tipo, release=None, pedida_por=CONSOLA,
                    perfil=None):
        if tipo not in m.TIPOS:
            raise Rechazado(f"tipo de orden desconocido: {tipo!r}")
        with self.sesion(perfil) as s:
            agente = self._agente(s, agente_id)
            if not self._ve(perfil, agente.tenant_id):
                raise NoEncontrado(f"no existe el agente {agente_id!r}")
            tenant = self._tenant(s, agente.tenant_id)
            self._exigir_operar(s, perfil, tenant.id)
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
                estado=m.PENDIENTE, pedida_por=perfil.etiqueta if perfil else pedida_por,
                pedida_por_id=perfil.usuario_id if perfil else None, creada=self.reloj())
            s.add(orden)
            try:
                s.flush()
            except IntegrityError:
                raise Conflicto(
                    f"ya hay una orden abierta sobre {instalacion}") from None
            self._auditar(s, perfil, "orden", tenant.id, orden=orden.id, tipo=tipo,
                          instalacion=instalacion, release=release)
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

    def cancelar_orden(self, orden_id, perfil=None):
        with self.sesion(perfil) as s:
            orden = self._orden_visible(s, perfil, orden_id)
            self._exigir_operar(s, perfil, orden.tenant_id)
            if orden.estado != m.PENDIENTE:
                raise Conflicto(
                    f"{orden_id} ya la tomó el agente ({orden.estado}); no se puede retirar")
            orden.estado = m.CANCELADA
            orden.terminada = self.reloj()
            self._auditar(s, perfil, "orden_retirada", orden.tenant_id, orden=orden_id)

    def pedir_cancelacion_rollback(self, orden_id, perfil=None):
        """El operador frena la vuelta atrás desde la web. Viaja al agente en la
        respuesta a su próximo envío de eventos o consulta de control."""
        with self.sesion(perfil) as s:
            orden = self._orden_visible(s, perfil, orden_id)
            self._exigir_operar(s, perfil, orden.tenant_id)
            if orden.tipo != m.DESPLEGAR or orden.estado not in (m.ENTREGADA, m.EN_CURSO):
                raise Conflicto(f"{orden_id} no es un despliegue en curso")
            orden.cancelar_rollback = True
            self._auditar(s, perfil, "rollback_cancelado", orden.tenant_id, orden=orden_id)

    def orden(self, orden_id, perfil=None):
        with self.sesion(perfil) as s:
            orden = self._orden_visible(s, perfil, orden_id)
            datos = self._orden_a_dict(orden)
            datos["eventos"] = [
                {"ts": e.ts_agente or _iso(e.recibido), "evento": e.evento, "datos": e.datos}
                for e in s.scalars(select(m.EventoOrden).where(
                    m.EventoOrden.orden_id == orden_id).order_by(m.EventoOrden.id))
            ]
            return datos

    def ordenes(self, perfil=None, tenant_id=None, instalacion=None, limite=50):
        if perfil is not None and not perfil.es_accusys:
            tenant_id = perfil.tenant
        with self.sesion(perfil) as s:
            consulta = select(m.Orden).order_by(m.Orden.creada.desc(), m.Orden.id.desc())
            if tenant_id:
                consulta = consulta.where(m.Orden.tenant_id == tenant_id)
            if instalacion:
                consulta = consulta.where(m.Orden.instalacion == instalacion)
            return [self._orden_a_dict(o) for o in s.scalars(consulta.limit(min(limite, 500)))]

    def parque(self, tenant_id=None, perfil=None):
        if perfil is not None and not perfil.es_accusys:
            tenant_id = perfil.tenant
        with self.sesion(perfil) as s:
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
    # habilitaciones: el cliente autoriza a Accusys
    # ------------------------------------------------------------------ #

    def habilitar_asistencia(self, perfil, horas, motivo=None):
        """La otorga el Aprobador del cliente. No hay versión de consola: una
        habilitación que se da Accusys a sí misma no es una habilitación."""
        if perfil is None or perfil.rol != "aprobador":
            raise Prohibido("la habilitación la otorga el Aprobador del cliente")
        if not 0 < horas <= MAX_HORAS_HABILITACION:
            raise Rechazado(f"la habilitación dura entre 1 y {MAX_HORAS_HABILITACION} horas")
        otorgada = self.reloj()
        with self.sesion(perfil) as s:
            h = m.Habilitacion(tenant_id=perfil.tenant, otorgada_por=perfil.usuario_id,
                               otorgada=otorgada,
                               vence=otorgada + datetime.timedelta(hours=horas), motivo=motivo)
            s.add(h)
            s.flush()
            self._auditar(s, perfil, "habilitacion_otorgada", perfil.tenant,
                          habilitacion=h.id, horas=horas, motivo=motivo)
            return self._habilitacion_a_dict(h)

    def revocar_habilitacion(self, perfil, habilitacion_id):
        if perfil is None or perfil.rol != "aprobador":
            raise Prohibido("la habilitación la revoca el Aprobador del cliente")
        with self.sesion(perfil) as s:
            h = s.get(m.Habilitacion, habilitacion_id)
            if h is None or h.tenant_id != perfil.tenant:
                raise NoEncontrado(f"no existe la habilitación {habilitacion_id}")
            if h.revocada is None:
                h.revocada = self.reloj()
                self._auditar(s, perfil, "habilitacion_revocada", h.tenant_id,
                              habilitacion=h.id)
            return self._habilitacion_a_dict(h)

    def habilitaciones(self, perfil=None, tenant_id=None):
        if perfil is not None and not perfil.es_accusys:
            tenant_id = perfil.tenant
        with self.sesion(perfil) as s:
            consulta = select(m.Habilitacion).order_by(m.Habilitacion.otorgada.desc())
            if tenant_id:
                consulta = consulta.where(m.Habilitacion.tenant_id == tenant_id)
            return [self._habilitacion_a_dict(h) for h in s.scalars(consulta.limit(100))]

    def _habilitacion_a_dict(self, h):
        vigente = h.revocada is None and h.vence > self.reloj()
        return {"id": h.id, "tenant": h.tenant_id, "otorgada_por": h.otorgada_por,
                "otorgada": _iso(h.otorgada), "vence": _iso(h.vence), "motivo": h.motivo,
                "revocada": _iso(h.revocada), "vigente": vigente}

    # ------------------------------------------------------------------ #
    # usuarios
    # ------------------------------------------------------------------ #

    def asignar_usuario(self, usuario_id, rol, tenant_id=None, nombre=None, email=None,
                        perfil=None):
        """Da de alta o cambia el rol de una persona.

        Los usuarios de un cliente los administra su Aprobador (salvo el propio) o
        Comercial de Accusys. Los de Accusys, solo la consola: no hay forma de que
        alguien se dé permisos sobre todo el parque desde la web."""
        try:
            usuario_id = str(uuid.UUID(str(usuario_id)))
        except ValueError:
            raise Rechazado(f"{usuario_id!r} no es un id de usuario válido") from None
        self._validar_asignacion(rol, tenant_id, perfil, usuario_id)

        with self.sesion(perfil) as s:
            if rol in m.ROLES_ACCUSYS:
                if s.get(m.UsuarioTenant, usuario_id):
                    raise Conflicto("ese usuario ya pertenece a un cliente")
                u = s.get(m.UsuarioAccusys, usuario_id) or m.UsuarioAccusys(usuario_id=usuario_id)
            else:
                self._tenant(s, tenant_id)
                if s.get(m.UsuarioAccusys, usuario_id):
                    raise Conflicto("ese usuario ya es de Accusys")
                u = s.get(m.UsuarioTenant, usuario_id)
                if u is not None and u.tenant_id != tenant_id:
                    raise Conflicto("ese usuario ya pertenece a otro cliente")
                u = u or m.UsuarioTenant(usuario_id=usuario_id, tenant_id=tenant_id)
            u.rol = rol
            u.nombre = nombre or u.nombre
            u.email = email or u.email
            s.add(u)
            self._auditar(s, perfil, "usuario_rol", tenant_id, usuario=usuario_id, rol=rol)
        return {"usuario_id": usuario_id, "rol": rol, "tenant": tenant_id,
                "nombre": nombre, "email": email}

    @staticmethod
    def _validar_asignacion(rol, tenant_id, perfil, usuario_id=None):
        """Quién puede dar qué rol. Se chequea antes de tocar nada, también antes de
        crear a alguien en el proveedor de identidad."""
        if rol in m.ROLES_ACCUSYS:
            if perfil is not None:
                raise Prohibido("los usuarios de Accusys se dan de alta desde la consola")
            if tenant_id:
                raise Rechazado("un usuario de Accusys no pertenece a un cliente")
        elif rol in m.ROLES_CLIENTE:
            if not tenant_id:
                raise Rechazado("un usuario de cliente necesita el cliente")
            if perfil is not None:
                if perfil.rol == "aprobador":
                    if perfil.tenant != tenant_id:
                        raise Prohibido("solo administrás usuarios de tu organización")
                    if usuario_id and perfil.usuario_id == usuario_id:
                        raise Prohibido("no podés cambiar tu propio rol")
                elif perfil.rol != "comercial":
                    raise Prohibido(f"tu rol ({perfil.rol}) no administra usuarios")
        else:
            raise Rechazado(f"rol desconocido: {rol!r}")

    # ------------------------------------------------------------------ #
    # invitaciones (necesitan la API de administración del proveedor)
    # ------------------------------------------------------------------ #

    def invitar(self, admin, email, rol, redirigir_a, tenant_id=None, nombre=None,
                perfil=None):
        """Crea a la persona en el proveedor de identidad (si no existía), le da el
        rol y devuelve el link para que entre y elija su contraseña."""
        if admin is None:
            raise Rechazado("las invitaciones necesitan SUPABASE_SECRET_KEY en el hub; "
                            "mientras tanto, alta por id")
        self._validar_asignacion(rol, tenant_id, perfil)
        if tenant_id:
            with self.sesion(perfil) as s:
                self._tenant(s, tenant_id)
        try:
            usuario_id, link, nueva = admin.invitar(email, redirigir_a, nombre=nombre)
        except ErrorDeployCenter as e:
            raise Rechazado(str(e)) from e
        datos = self.asignar_usuario(usuario_id, rol, tenant_id=tenant_id, nombre=nombre,
                                     email=email.strip().lower(), perfil=perfil)
        with self.sesion(perfil) as s:
            self._auditar(s, perfil, "invitacion", tenant_id, usuario=usuario_id,
                          email=datos["email"], rol=rol, nueva=nueva)
        return dict(datos, link=link, nueva=nueva)

    def link_de_acceso(self, admin, usuario_id, redirigir_a, perfil=None):
        """Un link nuevo para alguien que ya tiene alta: perdió la contraseña o no
        llegó a usar la invitación. Lo pide quien administra a esa persona."""
        if admin is None:
            raise Rechazado("los links de acceso necesitan SUPABASE_SECRET_KEY en el hub")
        with self.sesion(perfil) as s:
            u = s.get(m.UsuarioTenant, str(usuario_id))
            if u is None or not self._ve(perfil, u.tenant_id):
                raise NoEncontrado(f"no existe el usuario {usuario_id}")
            tenant_id, email = u.tenant_id, u.email
        self._validar_asignacion("lector", tenant_id, perfil, str(usuario_id))
        if not email:
            raise Rechazado("ese usuario no tiene email cargado: sin email no hay link")
        try:
            _id, link = admin.link_de_acceso(email, redirigir_a)
        except ErrorDeployCenter as e:
            raise Rechazado(str(e)) from e
        with self.sesion(perfil) as s:
            self._auditar(s, perfil, "link_de_acceso", tenant_id, usuario=str(usuario_id))
        return {"usuario_id": str(usuario_id), "email": email, "link": link}

    def quitar_usuario(self, usuario_id, perfil=None):
        try:
            usuario_id = str(uuid.UUID(str(usuario_id)))
        except ValueError:
            raise Rechazado(f"{usuario_id!r} no es un id de usuario válido") from None
        with self.sesion(perfil) as s:
            u = s.get(m.UsuarioTenant, usuario_id)
            if u is None:
                if perfil is None and (ua := s.get(m.UsuarioAccusys, usuario_id)):
                    s.delete(ua)
                    self._auditar(s, perfil, "usuario_baja", None, usuario=usuario_id)
                    return
                raise NoEncontrado(f"no existe el usuario {usuario_id}")
            if perfil is not None:
                if perfil.rol == "aprobador" and perfil.tenant == u.tenant_id:
                    if perfil.usuario_id == usuario_id:
                        raise Prohibido("no podés darte de baja a vos mismo")
                elif perfil.rol != "comercial":
                    raise Prohibido(f"tu rol ({perfil.rol}) no administra usuarios")
            s.delete(u)
            self._auditar(s, perfil, "usuario_baja", u.tenant_id, usuario=usuario_id)

    def usuarios(self, perfil=None, tenant_id=None):
        if perfil is not None and not perfil.es_accusys:
            tenant_id = perfil.tenant
        with self.sesion(perfil) as s:
            consulta = select(m.UsuarioTenant).order_by(m.UsuarioTenant.tenant_id,
                                                         m.UsuarioTenant.nombre)
            if tenant_id:
                consulta = consulta.where(m.UsuarioTenant.tenant_id == tenant_id)
            return [{"usuario_id": u.usuario_id, "tenant": u.tenant_id, "rol": u.rol,
                     "nombre": u.nombre, "email": u.email} for u in s.scalars(consulta)]

    def auditoria(self, perfil=None, tenant_id=None, limite=100):
        if perfil is not None and not perfil.es_accusys:
            tenant_id = perfil.tenant
        with self.sesion(perfil) as s:
            consulta = select(m.Auditoria).order_by(m.Auditoria.id.desc())
            if tenant_id:
                consulta = consulta.where(m.Auditoria.tenant_id == tenant_id)
            return [{"fecha": _iso(a.fecha), "usuario": a.usuario, "tenant": a.tenant_id,
                     "accion": a.accion, "detalle": a.detalle}
                    for a in s.scalars(consulta.limit(min(limite, 500)))]

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

    def _orden_visible(self, s, perfil, orden_id):
        o = s.get(m.Orden, orden_id)
        # la orden de otro cliente no existe para esta persona
        if o is None or not self._ve(perfil, o.tenant_id):
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

