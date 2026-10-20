-- Solo para una base de PRUEBAS vacia. Nunca ejecutar en produccion.
-- Verifica la publicacion, los dos borrados protegidos y su atomicidad.
begin;

insert into public.sources (id, name)
values ('00000000-0000-4000-8000-000000000031', 'TEST publication source');
insert into auth.users (id)
values ('00000000-0000-4000-8000-000000000032');

insert into public.events (
  id, external_key, source_id, title, start_at, end_at, last_seen_at,
  location, location_precision, location_source, extraction_method, is_suppressed
)
select
  ('00000000-0000-4000-8000-' || lpad(f.n::text, 12, '0'))::uuid,
  'TEST-publication-' || f.n, '00000000-0000-4000-8000-000000000031'::uuid,
  f.title, f.start_at, f.end_at, f.seen_at,
  'Santiago', 'commune', 'source-data', f.method, f.suppressed
from (values
  (301, 'Expired manual', now() - interval '2 days', now() - interval '1 day', now() - interval '1 day', 'manual', false),
  (302, 'Expired scraped with signal', now() - interval '2 days', now() - interval '1 day', now() - interval '1 day', 'jsonld', false),
  (303, 'Future manual', now() + interval '1 day', now() + interval '2 days', now() - interval '1 day', 'manual', false),
  (304, 'Suppressed scraped', now() - interval '2 days', now() - interval '1 day', now() - interval '1 day', 'jsonld', true),
  (305, 'Invalid manual dates', now() - interval '1 day', now() - interval '2 days', now() - interval '1 day', 'manual', false),
  (306, 'Stale future scraped', now() + interval '1 day', now() + interval '2 days', now() - interval '1 day', 'jsonld', false),
  (307, 'Refreshed scraped', now() + interval '1 day', now() + interval '2 days', now() - interval '1 day', 'jsonld', false),
  (308, 'Expired current scraped', now() - interval '2 days', now() - interval '1 day', now(), 'jsonld', false)
) f(n, title, start_at, end_at, seen_at, method, suppressed);

insert into public.event_provenance (event_id, original_url)
select id, 'https://example.test/publication/' || external_key
from public.events where extraction_method = 'jsonld';
insert into public.user_event_interactions (user_id, event_id, interaction_type)
values ('00000000-0000-4000-8000-000000000032',
        '00000000-0000-4000-8000-000000000302', 'interested');

insert into public.catalog_staging (run_id, entity, chunk_index, payload)
select '00000000-0000-4000-8000-000000000033', 'events', 0,
  jsonb_build_array(to_jsonb(e) || jsonb_build_object(
    'description', 'Updated description', 'last_seen_at', now()
  ))
from public.events e where external_key = 'TEST-publication-307';

do $$
declare
  v_result record;
begin
  -- El reloj cliente deliberadamente incorrecto no impide el vencimiento real.
  select * into v_result from public.publish_staged_catalog(
    '00000000-0000-4000-8000-000000000033',
    array['00000000-0000-4000-8000-000000000031'::uuid], now(), true,
    '1900-01-01'::timestamptz, '1900-01-01'::timestamptz
  );
  if v_result.removed_stale <> 1 or v_result.removed_expired <> 2 then
    raise exception 'Unexpected publication counts: %', row_to_json(v_result);
  end if;
  if exists (select 1 from public.events where external_key in (
    'TEST-publication-301', 'TEST-publication-306', 'TEST-publication-308'
  )) then
    raise exception 'Expired/stale candidates remain';
  end if;
  if (select count(*) from public.events) <> 5
    or (select count(*) from public.user_event_interactions) <> 1 then
    raise exception 'Protected events or signals were deleted';
  end if;
  if not exists (select 1 from public.events
    where external_key = 'TEST-publication-307'
      and description = 'Updated description' and last_seen_at = now()) then
    raise exception 'Current event was not refreshed';
  end if;
  if exists (select 1 from public.catalog_staging) then
    raise exception 'Published staging was not cleared';
  end if;
  if has_function_privilege('anon',
    'public.publish_staged_catalog(uuid,uuid[],timestamptz,boolean,timestamptz,timestamptz)', 'EXECUTE')
    or has_function_privilege('authenticated',
    'public.publish_staged_catalog(uuid,uuid[],timestamptz,boolean,timestamptz,timestamptz)', 'EXECUTE')
    or not has_function_privilege('service_role',
    'public.publish_staged_catalog(uuid,uuid[],timestamptz,boolean,timestamptz,timestamptz)', 'EXECUTE') then
    raise exception 'Publication RPC permissions changed';
  end if;
end;
$$;

insert into public.events (
  id, external_key, source_id, title, start_at, end_at,
  location, location_precision, location_source, extraction_method
) values (
  '00000000-0000-4000-8000-000000000309', 'TEST-publication-309',
  '00000000-0000-4000-8000-000000000031', 'Expired but cleanup disabled',
  now() - interval '2 days', now() - interval '1 day',
  'Santiago', 'commune', 'source-data', 'manual'
);
insert into public.catalog_staging (run_id, entity, chunk_index, payload)
values ('00000000-0000-4000-8000-000000000034', 'events', 0, '[]');
do $$
declare v_result record;
begin
  select * into v_result from public.publish_staged_catalog(
    '00000000-0000-4000-8000-000000000034', '{}'::uuid[], now(), false, now(), now()
  );
  if v_result.removed_stale <> 0 or v_result.removed_expired <> 0
    or not exists (select 1 from public.events where external_key = 'TEST-publication-309') then
    raise exception 'delete_expired=false must disable expiration cleanup';
  end if;
end;
$$;

-- Fuerza un fallo DESPUES de la limpieza para comprobar el rollback completo.
create function pg_temp.fail_staging_delete() returns trigger language plpgsql as $$
begin
  raise exception 'TEST forced staging failure';
end;
$$;
create trigger test_staging_failure before delete on public.catalog_staging
for each row execute function pg_temp.fail_staging_delete();
insert into public.catalog_staging (run_id, entity, chunk_index, payload)
values ('00000000-0000-4000-8000-000000000035', 'events', 0, '[]');
do $$
begin
  begin
    perform * from public.publish_staged_catalog(
      '00000000-0000-4000-8000-000000000035', '{}'::uuid[], now(), true, now(), now()
    );
    raise exception 'TEST expected publication to fail';
  exception when raise_exception then
    if sqlerrm <> 'TEST forced staging failure' then raise; end if;
  end;
  if not exists (select 1 from public.events where external_key = 'TEST-publication-309')
    or not exists (select 1 from public.catalog_staging
      where run_id = '00000000-0000-4000-8000-000000000035') then
    raise exception 'Failed publication must roll back cleanup and preserve staging';
  end if;
end;
$$;
rollback;
