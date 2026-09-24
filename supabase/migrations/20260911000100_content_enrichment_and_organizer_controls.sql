begin;

alter table public.organizers
  add column if not exists is_blocked boolean not null default false,
  add column if not exists blocked_reason text,
  add column if not exists blocked_at timestamptz;

create index if not exists organizers_blocked_idx
  on public.organizers (is_blocked) where is_blocked;

alter table public.events
  add column if not exists postal_code text,
  add column if not exists latitude double precision,
  add column if not exists longitude double precision,
  add column if not exists is_suppressed boolean not null default false,
  add column if not exists suppression_reason text,
  add column if not exists suppressed_at timestamptz;

create index if not exists events_suppressed_idx
  on public.events (is_suppressed) where is_suppressed;

alter table public.events
  drop constraint if exists events_latitude_check,
  drop constraint if exists events_longitude_check,
  add constraint events_latitude_check check (latitude is null or latitude between -90 and 90),
  add constraint events_longitude_check check (longitude is null or longitude between -180 and 180);

alter table public.events
  drop constraint if exists events_location_precision_check,
  add constraint events_location_precision_check
    check (location_precision in ('exact', 'coordinates', 'venue', 'commune', 'city', 'region', 'online', 'country'));

create table if not exists public.event_provenance (
  event_id uuid primary key references public.events(id) on delete cascade,
  original_url text,
  source_description text,
  ocr_text text,
  is_rewritten boolean not null default false,
  updated_at timestamptz not null default now()
);

drop trigger if exists event_provenance_set_updated_at on public.event_provenance;
create trigger event_provenance_set_updated_at before update on public.event_provenance
for each row execute function public.set_updated_at();

alter table public.event_provenance enable row level security;
revoke all on table public.event_provenance from anon, authenticated;
grant all on table public.event_provenance to service_role;

-- Consulta privada usada antes de OCR/redacción. local_key permite relacionar la
-- respuesta con el objeto de la ejecución aunque la coincidencia haya sido por URL.
create or replace function public.match_existing_events(candidates jsonb)
returns table (
  local_key text,
  stored_external_key text,
  stored_description text,
  stored_source_description text,
  stored_ocr_text text,
  stored_is_suppressed boolean,
  stored_has_provenance boolean
)
language sql
security definer
set search_path = public
as $$
  select distinct on (candidate ->> 'local_key')
    candidate ->> 'local_key',
    e.external_key,
    e.description,
    p.source_description,
    p.ocr_text,
    e.is_suppressed,
    p.event_id is not null
  from jsonb_array_elements(coalesce(candidates, '[]'::jsonb)) candidate
  join public.events e
    on e.external_key = candidate ->> 'external_key'
    or (
      nullif(candidate ->> 'source_url', '') is not null
      and (
        lower(split_part(rtrim(e.source_url, '/'), '#', 1)) = candidate ->> 'source_url'
        or lower(split_part(rtrim(e.official_url, '/'), '#', 1)) = candidate ->> 'source_url'
      )
    )
  left join public.event_provenance p on p.event_id = e.id
  order by candidate ->> 'local_key', e.last_seen_at desc;
$$;

revoke all on function public.match_existing_events(jsonb) from public, anon, authenticated;
grant execute on function public.match_existing_events(jsonb) to service_role;

create or replace function public.is_organizer_blocked(candidate_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select coalesce((select is_blocked from public.organizers where id = candidate_id), false);
$$;

revoke all on function public.is_organizer_blocked(uuid) from public;
grant execute on function public.is_organizer_blocked(uuid) to anon, authenticated, service_role;

-- Un organizador bloqueado y todos sus eventos dejan de ser visibles de inmediato.
drop policy if exists "Public organizers are readable" on public.organizers;
create policy "Public organizers are readable" on public.organizers
for select to anon, authenticated using (not is_blocked);

revoke select on table public.organizers from anon, authenticated;
grant select (id, name, entity_type, last_seen_at, created_at, updated_at)
  on table public.organizers to anon, authenticated;

drop policy if exists "Published events are readable" on public.events;
create policy "Published events are readable" on public.events
for select to anon, authenticated using (
  status = 'published'
  and not is_suppressed
  and not public.is_organizer_blocked(organizer_id)
);

drop policy if exists "Published event categories are readable" on public.event_categories;
create policy "Published event categories are readable" on public.event_categories
for select to anon, authenticated using (
  exists (
    select 1 from public.events e
    where e.id = event_categories.event_id
      and e.status = 'published'
      and not e.is_suppressed
      and not public.is_organizer_blocked(e.organizer_id)
  )
);

drop policy if exists "Published media are readable" on public.media_assets;
create policy "Published media are readable" on public.media_assets
for select to anon, authenticated using (
  exists (
    select 1 from public.events e
    where e.id = media_assets.event_id
      and e.status = 'published'
      and not e.is_suppressed
      and not public.is_organizer_blocked(e.organizer_id)
  )
);

drop view if exists public.public_event_catalog;
create view public.public_event_catalog
with (security_invoker = true)
as
select
  e.*,
  o.name as organizer_name,
  s.name as source_name
from public.events e
join public.sources s on s.id = e.source_id
left join public.organizers o on o.id = e.organizer_id
where e.status = 'published' and not e.is_suppressed;

revoke all on public.public_event_catalog from public;
grant select on public.public_event_catalog to anon, authenticated, service_role;

commit;
