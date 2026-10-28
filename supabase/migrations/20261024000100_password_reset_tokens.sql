-- Tokens temporales del flujo de recuperacion de contrasena de la aplicacion.
-- Mantiene las seis columnas y los indices del SQL recibido el 24/10/2026.
-- Aplicar esta migracion no cambia contrasenas, usuarios ni eventos.

begin;
set local lock_timeout = '5s';

create table if not exists public.password_reset_tokens (
  jti uuid primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  email text not null,
  expires_at timestamptz not null,
  used_at timestamptz,
  created_at timestamptz not null default now()
);

create index if not exists idx_password_reset_tokens_email
  on public.password_reset_tokens (email);
create index if not exists idx_password_reset_tokens_expira
  on public.password_reset_tokens (expires_at);

alter table public.password_reset_tokens enable row level security;

-- RLS no sustituye los privilegios SQL (por ejemplo, TRUNCATE no usa RLS).
-- No se crean politicas de acceso desde el navegador.
revoke all on table public.password_reset_tokens
  from public, anon, authenticated, service_role;
grant select, insert, update, delete on table public.password_reset_tokens
  to service_role;

commit;
