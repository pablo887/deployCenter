"""Modelo de datos del hub.

Es el mismo modelo del documento de arquitectura, reducido a lo que necesita el
canal con el agente. Corre sobre SQLite en desarrollo y en los tests, y sobre el
Postgres de Supabase en producción. El aislamiento por cliente con RLS se agrega
sobre estas tablas en el paso siguiente; hoy el hub las lee con su propia
credencial de servicio, que nunca sale del perímetro de Accusys.

Lo que el hub guarda: clientes, parametría, agentes, instalaciones, órdenes y
sus eventos. Lo que no guarda: valores de variables ni credenciales de los
sistemas del cliente. Los agentes se identifican con un token propio del que acá
solo queda el hash.
"""

import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# estados de una orden
PENDIENTE = "pendiente"      # encolada, el agente todavía no la pidió
ENTREGADA = "entregada"      # el agente la tomó
EN_CURSO = "en_curso"        # el agente ya mandó eventos
TERMINADA = "terminada"      # el agente mandó el resultado
CANCELADA = "cancelada"      # retirada antes de que el agente la tome

ABIERTAS = (PENDIENTE, ENTREGADA, EN_CURSO)

# tipos de orden: el catálogo cerrado de lo que el hub le puede pedir al agente
PREFLIGHT = "preflight"
DESPLEGAR = "desplegar"
ROLLBACK = "rollback"
TIPOS = (PREFLIGHT, DESPLEGAR, ROLLBACK)


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    nombre: Mapped[str] = mapped_column(String(200))
    estado: Mapped[str] = mapped_column(String(20), default="activo")


class TenantProducto(Base):
    """Qué compró cada cliente y hasta cuándo. Gobierna lo que se puede ordenar."""

    __tablename__ = "tenant_productos"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    producto: Mapped[str] = mapped_column(String(40), primary_key=True)
    mantenimiento_hasta: Mapped[datetime.date | None] = mapped_column(Date)
    autoservicio: Mapped[bool] = mapped_column(Boolean, default=True)
    canal: Mapped[str] = mapped_column(String(20), default="estable")


class CodigoEnrolamiento(Base):
    """Código de un solo uso para dar de alta un servidor. Se guarda el hash."""

    __tablename__ = "codigos_enrolamiento"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hash: Mapped[str] = mapped_column(String(64), unique=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    host: Mapped[str | None] = mapped_column(String(200))
    creado: Mapped[datetime.datetime] = mapped_column(DateTime)
    expira: Mapped[datetime.datetime] = mapped_column(DateTime)
    usado: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    agente_id: Mapped[str | None] = mapped_column(String(40))


class Agente(Base):
    __tablename__ = "agentes"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    host: Mapped[str] = mapped_column(String(200))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    version: Mapped[str | None] = mapped_column(String(40))
    estado: Mapped[str] = mapped_column(String(20), default="activo")  # activo | revocado
    enrolado: Mapped[datetime.datetime] = mapped_column(DateTime)
    ultimo_contacto: Mapped[datetime.datetime | None] = mapped_column(DateTime)


class Instalacion(Base):
    """Lo que el agente reporta en cada latido. El agente es la fuente de verdad."""

    __tablename__ = "instalaciones"
    __table_args__ = (UniqueConstraint("agente_id", "nombre"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agente_id: Mapped[str] = mapped_column(ForeignKey("agentes.id"))
    nombre: Mapped[str] = mapped_column(String(100))
    producto: Mapped[str | None] = mapped_column(String(40))
    version: Mapped[str | None] = mapped_column(String(40))
    estado: Mapped[str | None] = mapped_column(String(20))
    bloqueada: Mapped[bool] = mapped_column(Boolean, default=False)
    punto_retorno: Mapped[str | None] = mapped_column(String(40))
    actualizado: Mapped[datetime.datetime] = mapped_column(DateTime)


class Orden(Base):
    __tablename__ = "ordenes"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    agente_id: Mapped[str] = mapped_column(ForeignKey("agentes.id"), index=True)
    instalacion: Mapped[str] = mapped_column(String(100))
    tipo: Mapped[str] = mapped_column(String(20))
    producto: Mapped[str | None] = mapped_column(String(40))
    release: Mapped[str | None] = mapped_column(String(40))
    estado: Mapped[str] = mapped_column(String(20), default=PENDIENTE, index=True)
    resultado: Mapped[str | None] = mapped_column(String(20))
    detalle: Mapped[str | None] = mapped_column(Text)
    resumen: Mapped[dict | None] = mapped_column(JSON)
    pedida_por: Mapped[str] = mapped_column(String(200))
    cancelar_rollback: Mapped[bool] = mapped_column(Boolean, default=False)
    creada: Mapped[datetime.datetime] = mapped_column(DateTime)
    entregada: Mapped[datetime.datetime | None] = mapped_column(DateTime)
    terminada: Mapped[datetime.datetime | None] = mapped_column(DateTime)


# Una sola orden abierta por instalación, garantizado por la base y no solo por
# el chequeo previo: dos pedidos simultáneos no pueden pasar los dos.
_abierta = Orden.estado.in_(ABIERTAS)
Index("una_orden_abierta_por_instalacion", Orden.agente_id, Orden.instalacion,
      unique=True, sqlite_where=_abierta, postgresql_where=_abierta)


class EventoOrden(Base):
    """Lo que el agente va contando mientras ejecuta. Append-only."""

    __tablename__ = "eventos_orden"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    orden_id: Mapped[str] = mapped_column(ForeignKey("ordenes.id"), index=True)
    recibido: Mapped[datetime.datetime] = mapped_column(DateTime)
    ts_agente: Mapped[str | None] = mapped_column(String(40))
    evento: Mapped[str] = mapped_column(String(60))
    datos: Mapped[dict | None] = mapped_column(JSON)
