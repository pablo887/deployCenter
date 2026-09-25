-- Identidad y aislamiento por cliente (RLS).
--
-- Quién es quién sale del `sub` del JWT, y el rol sale de las tablas
-- usuarios_tenant y usuarios_accusys, no de lo que diga el token. Así, quitarle
-- un rol a alguien corta el acceso en ese momento, no cuando vence su token.
--
-- Los claims llegan en `request.jwt.claims`: en Supabase los pone PostgREST, y el
-- hub los pone él mismo al abrir cada transacción de un usuario (set_config
-- local + set local role authenticated). Las mismas políticas sirven en los dos
-- casos, y en cualquier Postgres.
--
-- El hub, para el canal de los agentes, usa el rol dueño de las tablas, que no
-- pasa por RLS. Esa credencial no sale del perímetro de Accusys.

-- ---------------------------------------------------------------------------
-- roles (en Supabase ya existen; en otro Postgres se crean)
-- ---------------------------------------------------------------------------

do $$
begin
    if not exists (select from pg_roles where rolname = 'authenticated') then
        create role authenticated nologin;
    end if;
    if not exists (select from pg_roles where rolname = 'anon') then
        create role anon nologin;
    end if;
end
$$;

-- ---------------------------------------------------------------------------
-- funciones de identidad
-- ---------------------------------------------------------------------------

create or replace function dc_claims() returns jsonb
    language sql stable
as $$
    select coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb
$$;

create or replace function dc_uid() returns uuid
    language sql stable
as $$
    select nullif(dc_claims() ->> 'sub', '')::uuid
$$;

-- Segundo factor: el token tiene que venir de una sesión con TOTP verificado.
create or replace function dc_mfa() returns boolean
    language sql stable
as $$
    select coalesce(dc_claims() ->> 'aal', '') = 'aal2'
$$;

-- security definer: leen las tablas de usuarios sin pasar por sus propias
-- políticas, que a su vez usan estas funciones.
create or replace function dc_tenant() returns varchar
    language sql stable security definer set search_path = public, pg_temp
as $$
    select tenant_id from usuarios_tenant where usuario_id = dc_uid()
$$;

create or replace function dc_rol() returns varchar
    language sql stable security definer set search_path = public, pg_temp
as $$
    select coalesce(
        (select rol from usuarios_tenant where usuario_id = dc_uid()),
        (select rol from usuarios_accusys where usuario_id = dc_uid()))
$$;

create or replace function dc_es_accusys() returns boolean
    language sql stable security definer set search_path = public, pg_temp
as $$
    select exists (select 1 from usuarios_accusys where usuario_id = dc_uid())
$$;

-- Accusys ve todo el parque; un cliente, solo lo suyo.
create or replace function dc_ve_tenant(t varchar) returns boolean
    language sql stable
as $$
    select dc_es_accusys() or (t is not null and t = dc_tenant())
$$;

-- ¿El cliente habilitó a Accusys a operar ahora?
create or replace function dc_asistencia_habilitada(t varchar) returns boolean
    language sql stable security definer set search_path = public, pg_temp
as $$
    select exists (
        select 1 from habilitaciones
        where tenant_id = t and revocada is null
          and vence > (now() at time zone 'utc'))
$$;

-- Quién puede ordenar sobre un cliente: su Operador, o Soporte de Accusys con
-- una habilitación vigente de ese cliente.
create or replace function dc_puede_operar(t varchar) returns boolean
    language sql stable
as $$
    select (t = dc_tenant() and dc_rol() = 'operador')
        or (dc_rol() = 'soporte' and dc_asistencia_habilitada(t))
$$;

revoke all on function dc_tenant(), dc_rol(), dc_es_accusys(),
    dc_asistencia_habilitada(varchar) from public;
grant execute on function dc_claims(), dc_uid(), dc_mfa(), dc_tenant(), dc_rol(),
    dc_es_accusys(), dc_ve_tenant(varchar), dc_asistencia_habilitada(varchar),
    dc_puede_operar(varchar) to authenticated;

-- ---------------------------------------------------------------------------
-- permisos por tabla y columna
-- ---------------------------------------------------------------------------
-- Supabase le da todo a anon y authenticated por defecto. Acá se empieza de cero.

revoke all on all tables in schema public from anon, authenticated;
revoke all on all sequences in schema public from anon, authenticated;
grant usage on schema public to authenticated;

grant select, insert, update on tenants to authenticated;
grant select, insert, update, delete on tenant_productos to authenticated;
-- el hash del código no se lee nunca desde la web
grant select (id, tenant_id, host, creado, expira, usado, agente_id)
    on codigos_enrolamiento to authenticated;
grant insert on codigos_enrolamiento to authenticated;
-- el hash del token del agente, tampoco
grant select (id, tenant_id, host, version, estado, enrolado, ultimo_contacto)
    on agentes to authenticated;
grant update (estado) on agentes to authenticated;
grant select on instalaciones to authenticated;
grant select, insert on ordenes to authenticated;
grant update (estado, detalle, terminada, cancelar_rollback) on ordenes to authenticated;
grant select on eventos_orden to authenticated;
grant select, insert, delete on usuarios_tenant to authenticated;
grant update (rol, nombre, email) on usuarios_tenant to authenticated;
grant select on usuarios_accusys to authenticated;
grant select, insert on habilitaciones to authenticated;
grant update (revocada) on habilitaciones to authenticated;
-- auditoría: se lee y se agrega; no se edita ni se borra
grant select, insert on auditoria to authenticated;
grant usage on all sequences in schema public to authenticated;

-- ---------------------------------------------------------------------------
-- políticas
-- ---------------------------------------------------------------------------

alter table tenants              enable row level security;
alter table tenant_productos     enable row level security;
alter table codigos_enrolamiento enable row level security;
alter table agentes              enable row level security;
alter table instalaciones        enable row level security;
alter table ordenes              enable row level security;
alter table eventos_orden        enable row level security;
alter table usuarios_tenant      enable row level security;
alter table usuarios_accusys     enable row level security;
alter table habilitaciones       enable row level security;
alter table auditoria            enable row level security;

-- Sin segundo factor no se ve nada, en ninguna tabla. Es restrictiva: se suma
-- (AND) a las políticas de cada tabla en vez de reemplazarlas.
do $$
declare
    tabla text;
begin
    foreach tabla in array array['tenants', 'tenant_productos', 'codigos_enrolamiento',
        'agentes', 'instalaciones', 'ordenes', 'eventos_orden', 'usuarios_tenant',
        'usuarios_accusys', 'habilitaciones', 'auditoria']
    loop
        execute format(
            'create policy exige_mfa on %I as restrictive for all to authenticated '
            'using (dc_mfa()) with check (dc_mfa())', tabla);
    end loop;
end
$$;

-- clientes y parametría: los ve el propio cliente y Accusys; la edita Comercial
create policy ver on tenants for select to authenticated
    using (dc_ve_tenant(id));
create policy comercial_alta on tenants for insert to authenticated
    with check (dc_rol() = 'comercial');
create policy comercial_edita on tenants for update to authenticated
    using (dc_rol() = 'comercial') with check (dc_rol() = 'comercial');

create policy ver on tenant_productos for select to authenticated
    using (dc_ve_tenant(tenant_id));
create policy comercial_alta on tenant_productos for insert to authenticated
    with check (dc_rol() = 'comercial');
create policy comercial_edita on tenant_productos for update to authenticated
    using (dc_rol() = 'comercial') with check (dc_rol() = 'comercial');
create policy comercial_baja on tenant_productos for delete to authenticated
    using (dc_rol() = 'comercial');

-- enrolamiento y agentes: Soporte de Accusys da de alta y revoca
create policy ver on codigos_enrolamiento for select to authenticated
    using (dc_es_accusys());
create policy soporte_emite on codigos_enrolamiento for insert to authenticated
    with check (dc_rol() = 'soporte');

create policy ver on agentes for select to authenticated
    using (dc_ve_tenant(tenant_id));
create policy soporte_revoca on agentes for update to authenticated
    using (dc_rol() = 'soporte') with check (dc_rol() = 'soporte');

create policy ver on instalaciones for select to authenticated
    using (exists (select 1 from agentes a
                   where a.id = instalaciones.agente_id and dc_ve_tenant(a.tenant_id)));

-- órdenes: las ve el cliente y Accusys; ordena el Operador del cliente, o
-- Soporte con habilitación. Soporte además puede cancelar (al revocar un agente).
create policy ver on ordenes for select to authenticated
    using (dc_ve_tenant(tenant_id));
create policy ordena on ordenes for insert to authenticated
    with check (dc_puede_operar(tenant_id) and pedida_por_id = dc_uid()
                and estado = 'pendiente');
create policy actualiza on ordenes for update to authenticated
    using (dc_puede_operar(tenant_id) or dc_rol() = 'soporte')
    with check (dc_puede_operar(tenant_id) or dc_rol() = 'soporte');

create policy ver on eventos_orden for select to authenticated
    using (exists (select 1 from ordenes o
                   where o.id = eventos_orden.orden_id and dc_ve_tenant(o.tenant_id)));

-- usuarios: los administra el Aprobador del cliente (no su propia fila) o Comercial
create policy ver on usuarios_tenant for select to authenticated
    using (dc_ve_tenant(tenant_id));
create policy administra on usuarios_tenant for all to authenticated
    using (((tenant_id = dc_tenant() and dc_rol() = 'aprobador') or dc_rol() = 'comercial')
           and usuario_id <> dc_uid())
    with check (((tenant_id = dc_tenant() and dc_rol() = 'aprobador') or dc_rol() = 'comercial')
                and usuario_id <> dc_uid());

create policy ver on usuarios_accusys for select to authenticated
    using (dc_es_accusys());

-- habilitaciones: las otorga y revoca el Aprobador del propio cliente
create policy ver on habilitaciones for select to authenticated
    using (dc_ve_tenant(tenant_id));
create policy aprobador_otorga on habilitaciones for insert to authenticated
    with check (tenant_id = dc_tenant() and dc_rol() = 'aprobador'
                and otorgada_por = dc_uid());
create policy aprobador_revoca on habilitaciones for update to authenticated
    using (tenant_id = dc_tenant() and dc_rol() = 'aprobador')
    with check (tenant_id = dc_tenant() and dc_rol() = 'aprobador');

-- auditoría: cada uno registra lo suyo, y nadie edita ni borra
create policy ver on auditoria for select to authenticated
    using (dc_ve_tenant(tenant_id) or (tenant_id is null and dc_es_accusys()));
create policy registra on auditoria for insert to authenticated
    with check (usuario_id = dc_uid()
                and (dc_ve_tenant(tenant_id) or (tenant_id is null and dc_es_accusys())));
