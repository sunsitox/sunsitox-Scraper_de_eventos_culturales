-- Ejecutar unicamente en una base de PRUEBAS con las migraciones aplicadas.
-- Todas las filas ficticias se revierten; no requiere pgTAP.

begin;

insert into public.sources (id, name)
values ('00000000-0000-4000-8000-000000000021', 'TEST matching source');

insert into public.events (
  id, external_key, source_id, title, start_at, last_seen_at,
  location, location_precision, location_source, source_url, official_url,
  description, is_suppressed
)
select
  ('00000000-0000-4000-8000-' || lpad(f.n::text, 12, '0'))::uuid,
  'TEST-match-' || f.n, '00000000-0000-4000-8000-000000000021'::uuid,
  'TEST event ' || f.n, now() + interval '1 day', f.seen_at,
  'Santiago', 'commune', 'source-data', f.source_url, f.official_url,
  'Description ' || f.n, f.suppressed
from (values
  (201, now() - interval '2 days', 'HTTPS://EXAMPLE.TEST/source/', 'HTTPS://OFFICIAL.TEST/event/', false),
  (202, now() - interval '1 day', 'https://example.test/source/', null::text, false),
  (203, now(), null::text, null::text, false),
  (204, now(), 'https://example.test/suppressed/', null::text, true),
  (205, now(), 'https://example.test/both/', 'https://example.test/both/', false),
  (206, now(), 'https://example.test/tie/', null::text, false),
  (207, now(), 'https://example.test/tie/', null::text, false)
) f(n, seen_at, source_url, official_url, suppressed);

insert into public.event_provenance (
  event_id, original_url, source_description, ocr_text, is_rewritten
) values (
  '00000000-0000-4000-8000-000000000201', 'https://example.test/source/',
  'Original source description', 'Poster OCR', true
);

do $$
declare
  v_row record;
  v_count bigint;
  v_candidates jsonb;
begin
  select * into v_row from public.match_existing_events(
    '[{"local_key":"by-key","external_key":"TEST-match-201","source_url":""}]'
  );
  if v_row.stored_external_key is distinct from 'TEST-match-201'
    or v_row.stored_description is distinct from 'Description 201'
    or v_row.stored_source_description is distinct from 'Original source description'
    or v_row.stored_ocr_text is distinct from 'Poster OCR'
    or v_row.stored_has_provenance is distinct from true
    or v_row.stored_is_rewritten is distinct from true then
    raise exception 'Key/provenance contract changed';
  end if;

  select * into v_row from public.match_existing_events(
    '[{"local_key":"by-source","external_key":"missing","source_url":"https://example.test/source"}]'
  );
  if v_row.stored_external_key is distinct from 'TEST-match-202' then
    raise exception 'Source URL must select most recently seen event';
  end if;

  select * into v_row from public.match_existing_events(
    '[{"local_key":"by-official","source_url":"https://official.test/event"}]'
  );
  if v_row.stored_external_key is distinct from 'TEST-match-201' then
    raise exception 'Official URL normalization changed';
  end if;

  select * into v_row from public.match_existing_events(
    '[{"local_key":"no-provenance","external_key":"TEST-match-203"}]'
  );
  if v_row.stored_has_provenance is distinct from false
    or v_row.stored_is_rewritten is distinct from false then
    raise exception 'Missing provenance flags changed';
  end if;

  select * into v_row from public.match_existing_events(
    '[{"local_key":"suppressed","external_key":"TEST-match-204"}]'
  );
  if v_row.stored_is_suppressed is distinct from true then
    raise exception 'Suppression information must remain available to scraper';
  end if;

  select count(*) into v_count from public.match_existing_events(
    '[{"local_key":"same","external_key":"TEST-match-205","source_url":"https://example.test/both"}]'
  );
  if v_count <> 1 then
    raise exception 'Multiple matching branches must return only one row';
  end if;

  select * into v_row from public.match_existing_events(
    '[{"local_key":"tie","source_url":"https://example.test/tie"}]'
  );
  if v_row.stored_external_key is distinct from 'TEST-match-206' then
    raise exception 'Ties must resolve deterministically by event id';
  end if;

  if exists (select 1 from public.match_existing_events(null))
    or exists (select 1 from public.match_existing_events('[]'))
    or exists (select 1 from public.match_existing_events(
      '[{"local_key":"unknown","external_key":"missing","source_url":""}]'
    )) then
    raise exception 'Empty, null or unknown candidates must not match';
  end if;

  select jsonb_agg(jsonb_build_object(
    'local_key', 'bulk-' || n, 'external_key', 'TEST-match-201', 'source_url', ''
  )) into v_candidates from generate_series(1, 1205) n;
  select count(*) into v_count from public.match_existing_events(v_candidates);
  if v_count <> 1205 then
    raise exception 'SQL function lost candidates: expected 1205, got %', v_count;
  end if;

  if has_function_privilege('anon', 'public.match_existing_events(jsonb)', 'EXECUTE')
    or has_function_privilege('authenticated', 'public.match_existing_events(jsonb)', 'EXECUTE')
    or not has_function_privilege('service_role', 'public.match_existing_events(jsonb)', 'EXECUTE') then
    raise exception 'Private RPC permissions changed';
  end if;

  select count(*) into v_count from public.events
  where source_id = '00000000-0000-4000-8000-000000000021';
  if v_count <> 7 then
    raise exception 'Matching must not modify catalog rows';
  end if;
end;
$$;

rollback;
