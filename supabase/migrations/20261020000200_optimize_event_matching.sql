-- Acelera la consulta privada de deduplicacion anterior a OCR/redaccion.
-- Conserva la firma, los campos y los criterios de coincidencia existentes.
-- Esta migracion no publica, modifica ni elimina filas del catalogo.

begin;

create index if not exists events_match_source_url_idx
  on public.events ((lower(split_part(rtrim(source_url, '/'), '#', 1))));

create index if not exists events_match_official_url_idx
  on public.events ((lower(split_part(rtrim(official_url, '/'), '#', 1))));

create or replace function public.match_existing_events(candidates jsonb)
returns table (
  local_key text,
  stored_external_key text,
  stored_description text,
  stored_source_description text,
  stored_ocr_text text,
  stored_is_suppressed boolean,
  stored_has_provenance boolean,
  stored_is_rewritten boolean
)
language sql
stable
security definer
set search_path = ''
as $$
  with candidate_rows as materialized (
    select
      candidate ->> 'local_key' as local_key,
      candidate ->> 'external_key' as external_key,
      nullif(candidate ->> 'source_url', '') as source_url
    from pg_catalog.jsonb_array_elements(
      coalesce(candidates, '[]'::jsonb)
    ) as candidate
  ), matches as (
    -- Separar las ramas permite planificar cada igualdad independientemente.
    -- La clave externa ya tiene un indice UNIQUE en el modelo inicial.
    select c.local_key, e.id, e.last_seen_at
    from candidate_rows c
    join public.events e on e.external_key = c.external_key

    union all

    select c.local_key, e.id, e.last_seen_at
    from candidate_rows c
    join public.events e
      on lower(split_part(rtrim(e.source_url, '/'), '#', 1)) = c.source_url
    where c.source_url is not null

    union all

    select c.local_key, e.id, e.last_seen_at
    from candidate_rows c
    join public.events e
      on lower(split_part(rtrim(e.official_url, '/'), '#', 1)) = c.source_url
    where c.source_url is not null
  ), selected as (
    -- Igual que antes, se elige la fila vista mas recientemente por local_key.
    -- id resuelve empates de forma estable entre ejecuciones y lotes.
    select distinct on (m.local_key) m.local_key, m.id
    from matches m
    order by m.local_key, m.last_seen_at desc, m.id
  )
  select
    s.local_key,
    e.external_key,
    e.description,
    p.source_description,
    p.ocr_text,
    e.is_suppressed,
    p.event_id is not null,
    coalesce(p.is_rewritten, false)
  from selected s
  join public.events e on e.id = s.id
  left join public.event_provenance p on p.event_id = e.id
  order by s.local_key;
$$;

revoke all on function public.match_existing_events(jsonb)
  from public, anon, authenticated;
grant execute on function public.match_existing_events(jsonb) to service_role;

commit;
