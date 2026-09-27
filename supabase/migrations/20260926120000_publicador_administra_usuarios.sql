-- Publicador también administra usuarios de clientes.
--
-- El técnico que publica los releases acompaña la puesta en marcha del cliente y
-- le da de alta a su gente, igual que Comercial. Los usuarios de Accusys se
-- siguen dando de alta solo por consola: acá no cambia nada para ellos.

create or replace function dc_administra_clientes() returns boolean
    language sql stable
as $$
    select coalesce(dc_rol() in ('comercial', 'publicador'), false)
$$;

grant execute on function dc_administra_clientes() to authenticated;

drop policy administra on usuarios_tenant;
create policy administra on usuarios_tenant for all to authenticated
    using (((tenant_id = dc_tenant() and dc_rol() = 'aprobador') or dc_administra_clientes())
           and usuario_id <> dc_uid())
    with check (((tenant_id = dc_tenant() and dc_rol() = 'aprobador') or dc_administra_clientes())
                and usuario_id <> dc_uid());
