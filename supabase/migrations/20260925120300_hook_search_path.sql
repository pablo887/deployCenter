-- El hook corre con el search_path de quien lo llama. Supabase Auth lo llama
-- como supabase_auth_admin, que tiene search_path=auth: sin esto, usuarios_tenant
-- no se encuentra y el login falla con 500 ("Error running hook URI").

alter function custom_access_token_hook(jsonb) set search_path = public, pg_temp;
