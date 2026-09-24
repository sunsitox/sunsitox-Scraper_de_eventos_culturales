-- Sustituye el marcador provisional de favoritos por señales de recomendación.
-- Las señales se conservan como historial; el puntaje se calcula en el backend
-- a partir de su tipo, frecuencia y antigüedad. El cliente no envía puntajes.

begin;

create table if not exists public.user_event_interactions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  event_id uuid not null references public.events(id) on delete cascade,
  interaction_type text not null check (interaction_type in (
    'view_detail',
    'open_source',
    'open_official_site',
    'interested',
    'not_interested',
    'share',
    'report'
  )),
  occurred_at timestamptz not null default now(),
  metadata jsonb not null default '{}'::jsonb,
  constraint user_event_interactions_metadata_object
    check (jsonb_typeof(metadata) = 'object')
);

create index if not exists user_event_interactions_user_time_idx
  on public.user_event_interactions (user_id, occurred_at desc);
create index if not exists user_event_interactions_event_idx
  on public.user_event_interactions (event_id);
create index if not exists user_event_interactions_user_type_idx
  on public.user_event_interactions (user_id, interaction_type, occurred_at desc);

-- Preserva cualquier fila existente como una señal positiva antes de retirar
-- el modelo provisional. En instalaciones sin favoritos esta sentencia no inserta filas.
insert into public.user_event_interactions (user_id, event_id, interaction_type, occurred_at)
select user_id, event_id, 'interested', created_at
from public.favorites
on conflict do nothing;

drop table if exists public.favorites;

alter table public.user_event_interactions enable row level security;
revoke all on table public.user_event_interactions from anon, authenticated;
grant select, insert, delete on table public.user_event_interactions to authenticated;
grant all on table public.user_event_interactions to service_role;

create policy "Users read own recommendation interactions"
on public.user_event_interactions
for select to authenticated
using ((select auth.uid()) = user_id);

create policy "Users record own recommendation interactions"
on public.user_event_interactions
for insert to authenticated
with check (
  (select auth.uid()) = user_id
  and exists (
    select 1
    from public.events
    where events.id = event_id
      and events.status = 'published'
  )
);

create policy "Users delete own recommendation interactions"
on public.user_event_interactions
for delete to authenticated
using ((select auth.uid()) = user_id);

commit;
