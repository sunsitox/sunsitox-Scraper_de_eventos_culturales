begin;

create index if not exists events_source_last_seen_idx
  on public.events (source_id, last_seen_at);

-- La existencia de event_provenance no implica que NVIDIA haya logrado redactar.
-- Se expone el resultado real para reintentar únicamente filas pendientes o cambiadas.
drop function if exists public.match_existing_events(jsonb);
create function public.match_existing_events(candidates jsonb)
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
    p.event_id is not null,
    coalesce(p.is_rewritten, false)
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

commit;
