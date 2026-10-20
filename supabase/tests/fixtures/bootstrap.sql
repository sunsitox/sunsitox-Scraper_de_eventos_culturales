-- Solo para PostgreSQL temporal de CI. Nunca usar sobre Supabase real.
do $$
begin
  if current_database() <> 'eventos_test'
    or to_regclass('public.events') is not null
    or to_regclass('auth.users') is not null then
    raise exception 'Se requiere una base vacia llamada eventos_test';
  end if;
end;
$$;

create role anon nologin;
create role authenticated nologin;
create role service_role nologin bypassrls;
create schema auth;
create table auth.users (
  id uuid primary key,
  raw_user_meta_data jsonb not null default '{}'::jsonb
);
create function auth.uid() returns uuid language sql stable as $$
  select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid;
$$;
