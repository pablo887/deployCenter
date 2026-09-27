-- Esquema del hub de deployCenter.
--
-- Lo aplica `dc-hub migrar` en cualquier Postgres, o `supabase db push` en un
-- proyecto de Supabase. Los tiempos se guardan en UTC sin zona, igual que los
-- escribe el hub.
--
-- El mapeo de SQLAlchemy (src/deploycenter/hub/modelos.py) tiene que coincidir
-- con esto: tests/test_hub_postgres.py lo compara.

create table tenants (
    id      varchar(40) primary key,
    nombre  varchar(200) not null,
    estado  varchar(20) not null default 'activo'
            check (estado in ('activo', 'suspendido'))
);

-- Qué compró cada cliente y hasta cuándo. Gobierna lo que se puede ordenar.
create table tenant_productos (
    tenant_id            varchar(40) not null references tenants (id),
    producto             varchar(40) not null,
    mantenimiento_hasta  date,
    autoservicio         boolean not null default true,
    canal                varchar(20) not null default 'estable'
                         check (canal in ('estable', 'anticipado')),
    primary key (tenant_id, producto)
);

create table codigos_enrolamiento (
    id         serial primary key,
    hash       varchar(64) not null unique,
    tenant_id  varchar(40) not null references tenants (id),
    host       varchar(200),
    creado     timestamp not null,
    expira     timestamp not null,
    usado      timestamp,
    agente_id  varchar(40)
);

create table agentes (
    id               varchar(40) primary key,
    tenant_id        varchar(40) not null references tenants (id),
    host             varchar(200) not null,
    token_hash       varchar(64) not null unique,
    version          varchar(40),
    estado           varchar(20) not null default 'activo'
                     check (estado in ('activo', 'revocado')),
    enrolado         timestamp not null,
    ultimo_contacto  timestamp
);

create table instalaciones (
    id             serial primary key,
    agente_id      varchar(40) not null references agentes (id),
    nombre         varchar(100) not null,
    producto       varchar(40),
    version        varchar(40),
    estado         varchar(20),
    bloqueada      boolean not null default false,
    punto_retorno  varchar(40),
    actualizado    timestamp not null,
    unique (agente_id, nombre)
);

create table ordenes (
    id                 varchar(40) primary key,
    tenant_id          varchar(40) not null references tenants (id),
    agente_id          varchar(40) not null references agentes (id),
    instalacion        varchar(100) not null,
    tipo               varchar(20) not null
                       check (tipo in ('preflight', 'desplegar', 'rollback')),
    producto           varchar(40),
    release            varchar(40),
    estado             varchar(20) not null default 'pendiente'
                       check (estado in ('pendiente', 'entregada', 'en_curso',
                                         'terminada', 'cancelada')),
    resultado          varchar(20),
    detalle            text,
    resumen            json,
    pedida_por         varchar(200) not null,
    pedida_por_id      uuid,
    cancelar_rollback  boolean not null default false,
    creada             timestamp not null,
    entregada          timestamp,
    terminada          timestamp
);
create index ix_ordenes_agente_id on ordenes (agente_id);
create index ix_ordenes_estado on ordenes (estado);

-- Una sola orden abierta por instalación, garantizado por la base.
create unique index una_orden_abierta_por_instalacion on ordenes (agente_id, instalacion)
    where estado in ('pendiente', 'entregada', 'en_curso');

create table eventos_orden (
    id         serial primary key,
    orden_id   varchar(40) not null references ordenes (id),
    recibido   timestamp not null,
    ts_agente  varchar(40),
    evento     varchar(60) not null,
    datos      json
);
create index ix_eventos_orden_orden_id on eventos_orden (orden_id);

-- Personas. El id es el `sub` del JWT (auth.users.id en Supabase). Un usuario de
-- cliente pertenece a un solo cliente.
create table usuarios_tenant (
    usuario_id  uuid primary key,
    tenant_id   varchar(40) not null references tenants (id),
    rol         varchar(20) not null check (rol in ('lector', 'operador', 'aprobador')),
    nombre      varchar(200),
    email       varchar(320)
);

create table usuarios_accusys (
    usuario_id  uuid primary key,
    rol         varchar(20) not null check (rol in ('soporte', 'publicador', 'comercial')),
    nombre      varchar(200),
    email       varchar(320)
);

-- El cliente autoriza a Accusys a operar, por un plazo.
create table habilitaciones (
    id            serial primary key,
    tenant_id     varchar(40) not null references tenants (id),
    otorgada_por  uuid not null,
    otorgada      timestamp not null,
    vence         timestamp not null,
    motivo        text,
    revocada      timestamp,
    check (vence > otorgada)
);
create index ix_habilitaciones_tenant_id on habilitaciones (tenant_id);

-- Quién hizo qué desde la web. Solo se inserta.
create table auditoria (
    id          serial primary key,
    fecha       timestamp not null,
    usuario_id  uuid,
    usuario     varchar(320),
    tenant_id   varchar(40),
    accion      varchar(60) not null,
    detalle     json
);
create index ix_auditoria_tenant_id on auditoria (tenant_id);
