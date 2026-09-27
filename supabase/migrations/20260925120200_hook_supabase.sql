-- Hook de Supabase Auth: agrega el cliente y el rol al token al emitirlo.
--
-- Sirve para que el frontend sepa qué mostrar sin otra consulta. No decide
-- permisos: las políticas leen el rol de las tablas en cada consulta.
--
-- Se activa en el panel de Supabase: Authentication → Hooks → Custom Access
-- Token → public.custom_access_token_hook. En un Postgres sin Supabase la
-- función existe igual pero nadie la llama.

create or replace function custom_access_token_hook(event jsonb) returns jsonb
    language plpgsql stable
as $$
declare
    uid     uuid := (event ->> 'user_id')::uuid;
    tenant  varchar;
    rol     varchar;
    claims  jsonb := coalesce(event -> 'claims', '{}'::jsonb);
begin
    select ut.tenant_id, ut.rol into tenant, rol from usuarios_tenant ut where ut.usuario_id = uid;
    if rol is null then
        select ua.rol into rol from usuarios_accusys ua where ua.usuario_id = uid;
    end if;
    claims := jsonb_set(claims, '{app_metadata}',
        coalesce(claims -> 'app_metadata', '{}'::jsonb)
        || jsonb_build_object('tenant_id', tenant, 'dc_rol', rol));
    return jsonb_set(event, '{claims}', claims);
end
$$;

revoke all on function custom_access_token_hook(jsonb) from public, anon, authenticated;

-- Solo en Supabase: el servicio de Auth corre como supabase_auth_admin y
-- necesita leer las tablas de usuarios para completar el token.
do $$
begin
    if exists (select from pg_roles where rolname = 'supabase_auth_admin') then
        grant usage on schema public to supabase_auth_admin;
        grant execute on function custom_access_token_hook(jsonb) to supabase_auth_admin;
        grant select on usuarios_tenant, usuarios_accusys to supabase_auth_admin;
        execute 'create policy hook_lee on usuarios_tenant for select '
                'to supabase_auth_admin using (true)';
        execute 'create policy hook_lee on usuarios_accusys for select '
                'to supabase_auth_admin using (true)';
    end if;
end
$$;
