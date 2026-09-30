-- Enrolamiento al revés: el agente se presenta y la web lo acepta.
--
-- Soporte genera, para cada cliente, una llave que se configura en el agente
-- junto con el cliente y el ambiente. Con eso el agente pide conectarse; el
-- pedido queda pendiente hasta que alguien de Soporte lo acepta, y recién ahí
-- el agente recibe su token. La llave sola no conecta nada: sirve para que el
-- pedido llegue a la cola del cliente correcto.
--
-- El canal de los agentes usa el rol dueño de las tablas (no pasa por RLS): el
-- pedido lo crea y lo cierra el hub. La web solo ve y resuelve.

create table llaves_enrolamiento (
    id         serial primary key,
    tenant_id  varchar(40) not null references tenants (id),
    nombre     varchar(100) not null,
    prefijo    varchar(12) not null,
    hash       varchar(64) not null unique,
    creada     timestamp not null,
    revocada   timestamp
);

create table solicitudes_agente (
    id            varchar(40) primary key,
    tenant_id     varchar(40) not null references tenants (id),
    llave_id      integer not null references llaves_enrolamiento (id),
    host          varchar(200) not null,
    ambiente      varchar(30) not null,
    version       varchar(40),
    ip            varchar(64),
    secreto_hash  varchar(64) not null unique,
    estado        varchar(20) not null default 'pendiente'
                  check (estado in ('pendiente', 'aceptada', 'rechazada', 'conectada',
                                    'vencida', 'reemplazada')),
    creada        timestamp not null,
    resuelta      timestamp,
    resuelta_por  varchar(200),
    agente_id     varchar(40)
);

alter table agentes add column ambiente varchar(30);

-- ---------------------------------------------------------------------------
-- permisos: los hashes no se leen nunca desde la web
-- ---------------------------------------------------------------------------

grant select (id, tenant_id, nombre, prefijo, creada, revocada)
    on llaves_enrolamiento to authenticated;
grant insert on llaves_enrolamiento to authenticated;
grant update (revocada) on llaves_enrolamiento to authenticated;
grant usage on sequence llaves_enrolamiento_id_seq to authenticated;

grant select (id, tenant_id, llave_id, host, ambiente, version, ip, estado, creada,
              resuelta, resuelta_por, agente_id)
    on solicitudes_agente to authenticated;
grant update (estado, resuelta, resuelta_por) on solicitudes_agente to authenticated;

grant select (ambiente) on agentes to authenticated;

-- ---------------------------------------------------------------------------
-- políticas
-- ---------------------------------------------------------------------------

alter table llaves_enrolamiento enable row level security;
alter table solicitudes_agente  enable row level security;

create policy exige_mfa on llaves_enrolamiento as restrictive for all to authenticated
    using (dc_mfa()) with check (dc_mfa());
create policy exige_mfa on solicitudes_agente as restrictive for all to authenticated
    using (dc_mfa()) with check (dc_mfa());

-- las llaves son de Accusys: las ve y las administra Soporte
create policy ver on llaves_enrolamiento for select to authenticated
    using (dc_es_accusys());
create policy soporte_genera on llaves_enrolamiento for insert to authenticated
    with check (dc_rol() = 'soporte');
create policy soporte_revoca on llaves_enrolamiento for update to authenticated
    using (dc_rol() = 'soporte') with check (dc_rol() = 'soporte');

-- los pedidos los ve también el cliente (qué servidores quieren entrar); los
-- resuelve Soporte, y solo mientras están pendientes
create policy ver on solicitudes_agente for select to authenticated
    using (dc_ve_tenant(tenant_id));
create policy soporte_resuelve on solicitudes_agente for update to authenticated
    using (dc_rol() = 'soporte' and estado = 'pendiente')
    with check (dc_rol() = 'soporte' and estado in ('aceptada', 'rechazada'));
