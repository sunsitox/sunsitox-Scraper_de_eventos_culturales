-- Regresion SQL para una base de PRUEBAS vacia con las migraciones aplicadas.
-- No ejecutar sobre produccion: la funcion revisa el catalogo completo.
-- Todas las filas ficticias y los borrados se revierten al finalizar.

begin;

insert into public.sources (id, name)
values ('00000000-0000-4000-8000-000000000001', 'TEST cleanup source');

insert into auth.users (id)
values ('00000000-0000-4000-8000-000000000002');

do $$
declare
  v_now timestamptz := pg_catalog.now();
  v_day_start timestamptz := pg_catalog.date_trunc(
    'day', v_now at time zone 'America/Santiago'
  ) at time zone 'America/Santiago';
  v_deleted bigint;
  v_count bigint;
begin
  -- end_at es timestamp absoluto; un evento sin termino usa el dia chileno.
  insert into public.events (
    id, external_key, source_id, title, start_at, end_at,
    location, location_precision, location_source,
    is_suppressed, extraction_method, source_url
  )
  select
    ('00000000-0000-4000-8000-' || pg_catalog.lpad(f.n::text, 12, '0'))::uuid,
    'TEST-cleanup-' || f.n,
    '00000000-0000-4000-8000-000000000001'::uuid,
    f.title, f.start_at, f.end_at,
    'Santiago', 'commune', 'source-data',
    f.suppressed, f.method, f.url
  from (values
    (101, 'Expired scraped', v_now - interval '2 days', v_now - interval '1 day', false, 'jsonld', 'https://example.test/event'),
    (102, 'Expired manual', v_now - interval '2 days', v_now - interval '1 day', false, 'manual', null),
    (103, 'Future', v_now + interval '1 day', v_now + interval '2 days', false, 'manual', null),
    (104, 'Ongoing', v_now - interval '1 day', v_now + interval '1 day', false, 'manual', null),
    (105, 'No end yesterday', v_day_start - interval '12 hours', null::timestamptz, false, 'manual', null),
    (106, 'No end today', v_day_start, null::timestamptz, false, 'manual', null),
    (107, 'Missing start', null::timestamptz, v_now - interval '1 day', false, 'manual', null),
    (108, 'Missing both dates', null::timestamptz, null::timestamptz, false, 'manual', null),
    (109, 'Invalid chronology', v_now - interval '1 day', v_now - interval '2 days', false, 'manual', null),
    (110, 'Suppressed expired', v_now - interval '2 days', v_now - interval '1 day', true, 'manual', null),
    (111, 'Expired with interest', v_now - interval '2 days', v_now - interval '1 day', false, 'manual', null),
    (112, 'Expired with negative signal', v_now - interval '2 days', v_now - interval '1 day', false, 'manual', null),
    (113, 'Ends exactly now', v_now - interval '1 hour', v_now, false, 'manual', null)
  ) as f(n, title, start_at, end_at, suppressed, method, url);

  insert into public.user_event_interactions (
    user_id, event_id, interaction_type
  ) values
    ('00000000-0000-4000-8000-000000000002', '00000000-0000-4000-8000-000000000111', 'interested'),
    ('00000000-0000-4000-8000-000000000002', '00000000-0000-4000-8000-000000000112', 'not_interested');

  insert into public.categories (id, name)
  values ('00000000-0000-4000-8000-000000000003', 'TEST cleanup category');
  insert into public.event_categories (event_id, category_id)
  values ('00000000-0000-4000-8000-000000000101', '00000000-0000-4000-8000-000000000003');
  insert into public.media_assets (id, event_id, url)
  values ('00000000-0000-4000-8000-000000000004', '00000000-0000-4000-8000-000000000101', 'https://example.test/image.jpg');
  insert into public.event_provenance (event_id, original_url)
  values ('00000000-0000-4000-8000-000000000101', 'https://example.test/event');

  v_deleted := public.cleanup_expired_events();
  if v_deleted <> 3 then
    raise exception 'Expected 3 deletions, got %', v_deleted;
  end if;
  if exists (
    select 1 from public.events
    where external_key in ('TEST-cleanup-101', 'TEST-cleanup-102', 'TEST-cleanup-105')
  ) then
    raise exception 'Expired candidates were not deleted';
  end if;
  select pg_catalog.count(*) into v_count from public.events
  where external_key like 'TEST-cleanup-%';
  if v_count <> 10 then
    raise exception 'Expected 10 protected/current events, got %', v_count;
  end if;
  if exists (
    select 1 from public.event_categories
    where event_id = '00000000-0000-4000-8000-000000000101'
  ) or exists (
    select 1 from public.media_assets
    where event_id = '00000000-0000-4000-8000-000000000101'
  ) or exists (
    select 1 from public.event_provenance
    where event_id = '00000000-0000-4000-8000-000000000101'
  ) then
    raise exception 'Dependent catalog rows were not cascaded';
  end if;
  select pg_catalog.count(*) into v_count
  from public.user_event_interactions
  where user_id = '00000000-0000-4000-8000-000000000002';
  if v_count <> 2 then
    raise exception 'User signals were lost';
  end if;
  if public.cleanup_expired_events() <> 0 then
    raise exception 'Cleanup must be idempotent';
  end if;

  if pg_catalog.has_function_privilege('anon', 'public.cleanup_expired_events()', 'EXECUTE')
    or pg_catalog.has_function_privilege('authenticated', 'public.cleanup_expired_events()', 'EXECUTE') then
    raise exception 'Frontend roles must not execute cleanup';
  end if;
  if not pg_catalog.has_function_privilege('service_role', 'public.cleanup_expired_events()', 'EXECUTE') then
    raise exception 'service_role must execute cleanup';
  end if;
end;
$$;

-- Ejecutar como service_role demuestra la elevacion de la funcion, sin
-- conceder acceso directo a las tablas como parte de esta migracion.
set local role service_role;
select public.cleanup_expired_events() as expected_zero;
reset role;

rollback;
