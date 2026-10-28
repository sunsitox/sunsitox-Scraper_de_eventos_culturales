-- Clasificacion cultural versionada; aplicar esta migracion NO reclasifica ni borra datos.
-- Mantiene la firma y protecciones de la publicacion atomica anterior.

begin;

alter table public.event_provenance
  add column if not exists classification jsonb not null default '{}'::jsonb
  check (jsonb_typeof(classification) = 'object');

comment on column public.event_provenance.classification is
  'Taxonomia versionada: etiquetas originales, disciplinas, formatos, publicos, programas, evidencia y decision.';

create or replace function public.publish_staged_catalog(
  p_run_id uuid,
  p_reconcile_source_ids uuid[],
  p_seen_at timestamptz,
  p_delete_expired boolean,
  p_current_time timestamptz,
  p_day_start timestamptz
)
returns table (removed_stale integer, removed_expired integer)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_rows jsonb;
  v_removed_stale integer := 0;
  v_removed_expired integer := 0;
begin
  if not exists (
    select 1 from public.catalog_staging where run_id = p_run_id
  ) then
    raise exception 'No existe staging para la ejecucion %', p_run_id;
  end if;

  -- Evita que una senal concurrente desaparezca por la conciliacion.
  -- Las mismas protecciones de interacciones se aplican a ambos borrados.
  set local lock_timeout = '5s';
  lock table public.user_event_interactions in share mode;

  select coalesce(jsonb_agg(items.value order by s.chunk_index), '[]'::jsonb)
  into v_rows
  from public.catalog_staging s
  cross join lateral jsonb_array_elements(s.payload) as items(value)
  where s.run_id = p_run_id and s.entity = 'sources';
  insert into public.sources (id, name, website_url, is_active, last_seen_at)
  select id, name, website_url, is_active, last_seen_at
  from jsonb_populate_recordset(null::public.sources, v_rows)
  on conflict (id) do update set
    name = excluded.name,
    website_url = excluded.website_url,
    is_active = excluded.is_active,
    last_seen_at = excluded.last_seen_at;

  select coalesce(jsonb_agg(items.value order by s.chunk_index), '[]'::jsonb)
  into v_rows
  from public.catalog_staging s
  cross join lateral jsonb_array_elements(s.payload) as items(value)
  where s.run_id = p_run_id and s.entity = 'organizers';
  insert into public.organizers (id, name, last_seen_at)
  select id, name, last_seen_at
  from jsonb_populate_recordset(null::public.organizers, v_rows)
  on conflict (id) do update set
    name = excluded.name,
    last_seen_at = excluded.last_seen_at;

  select coalesce(jsonb_agg(items.value order by s.chunk_index), '[]'::jsonb)
  into v_rows
  from public.catalog_staging s
  cross join lateral jsonb_array_elements(s.payload) as items(value)
  where s.run_id = p_run_id and s.entity = 'communes';
  insert into public.communes (id, name, region, country)
  select id, name, region, country
  from jsonb_populate_recordset(null::public.communes, v_rows)
  on conflict (id) do update set
    name = excluded.name,
    region = excluded.region,
    country = excluded.country;

  select coalesce(jsonb_agg(items.value order by s.chunk_index), '[]'::jsonb)
  into v_rows
  from public.catalog_staging s
  cross join lateral jsonb_array_elements(s.payload) as items(value)
  where s.run_id = p_run_id and s.entity = 'categories';
  insert into public.categories (id, name)
  select id, name
  from jsonb_populate_recordset(null::public.categories, v_rows)
  on conflict (id) do update set name = excluded.name;

  select coalesce(jsonb_agg(items.value order by s.chunk_index), '[]'::jsonb)
  into v_rows
  from public.catalog_staging s
  cross join lateral jsonb_array_elements(s.payload) as items(value)
  where s.run_id = p_run_id and s.entity = 'events';
  insert into public.events (
    id, external_key, source_id, organizer_id, commune_id, title,
    start_at, end_at, venue, address, city, region, country, postal_code,
    latitude, longitude, location, location_precision, location_source,
    status, audience, is_free, description, price_text, currency, image_url,
    source_url, official_url, extraction_method, scraped_at, last_seen_at
  )
  select
    id, external_key, source_id, organizer_id, commune_id, title,
    start_at, end_at, venue, address, city, region, country, postal_code,
    latitude, longitude, location, location_precision, location_source,
    status, audience, is_free, description, price_text, currency, image_url,
    source_url, official_url, extraction_method, scraped_at, last_seen_at
  from jsonb_populate_recordset(null::public.events, v_rows)
  on conflict (id) do update set
    external_key = excluded.external_key,
    source_id = excluded.source_id,
    organizer_id = excluded.organizer_id,
    commune_id = excluded.commune_id,
    title = excluded.title,
    start_at = excluded.start_at,
    end_at = excluded.end_at,
    venue = excluded.venue,
    address = excluded.address,
    city = excluded.city,
    region = excluded.region,
    country = excluded.country,
    postal_code = excluded.postal_code,
    latitude = excluded.latitude,
    longitude = excluded.longitude,
    location = excluded.location,
    location_precision = excluded.location_precision,
    location_source = excluded.location_source,
    status = excluded.status,
    audience = excluded.audience,
    is_free = excluded.is_free,
    description = excluded.description,
    price_text = excluded.price_text,
    currency = excluded.currency,
    image_url = excluded.image_url,
    source_url = excluded.source_url,
    official_url = excluded.official_url,
    extraction_method = excluded.extraction_method,
    scraped_at = excluded.scraped_at,
    last_seen_at = excluded.last_seen_at;

  select coalesce(jsonb_agg(items.value order by s.chunk_index), '[]'::jsonb)
  into v_rows
  from public.catalog_staging s
  cross join lateral jsonb_array_elements(s.payload) as items(value)
  where s.run_id = p_run_id and s.entity = 'event_provenance'
    and coalesce(items.value ->> 'classification_only', 'false') <> 'true';
  insert into public.event_provenance (
    event_id, original_url, source_description, ocr_text, is_rewritten, classification
  )
  select event_id, original_url, source_description, ocr_text, is_rewritten, coalesce(classification, '{}'::jsonb)
  from jsonb_populate_recordset(null::public.event_provenance, v_rows)
  on conflict (event_id) do update set
    original_url = excluded.original_url,
    source_description = excluded.source_description,
    ocr_text = excluded.ocr_text,
    is_rewritten = excluded.is_rewritten,
    classification = excluded.classification;

  delete from public.event_categories ec
  where ec.event_id in (
    select (items.value ->> 'id')::uuid
    from public.catalog_staging s
    cross join lateral jsonb_array_elements(s.payload) as items(value)
    where s.run_id = p_run_id and s.entity = 'events'
  );
  select coalesce(jsonb_agg(items.value order by s.chunk_index), '[]'::jsonb)
  into v_rows
  from public.catalog_staging s
  cross join lateral jsonb_array_elements(s.payload) as items(value)
  where s.run_id = p_run_id and s.entity = 'event_categories';
  insert into public.event_categories (event_id, category_id)
  select event_id, category_id
  from jsonb_populate_recordset(null::public.event_categories, v_rows)
  on conflict (event_id, category_id) do nothing;

  delete from public.media_assets ma
  where ma.event_id in (
    select (items.value ->> 'id')::uuid
    from public.catalog_staging s
    cross join lateral jsonb_array_elements(s.payload) as items(value)
    where s.run_id = p_run_id and s.entity = 'events'
  );
  select coalesce(jsonb_agg(items.value order by s.chunk_index), '[]'::jsonb)
  into v_rows
  from public.catalog_staging s
  cross join lateral jsonb_array_elements(s.payload) as items(value)
  where s.run_id = p_run_id and s.entity = 'media_assets';
  insert into public.media_assets (id, event_id, url, media_type, is_primary)
  select id, event_id, url, media_type, is_primary
  from jsonb_populate_recordset(null::public.media_assets, v_rows)
  on conflict (id) do update set
    event_id = excluded.event_id,
    url = excluded.url,
    media_type = excluded.media_type,
    is_primary = excluded.is_primary;

  -- Las exclusiones existentes se archivan en ESTA transaccion, incluso si
  -- tienen interacciones. No se crean nuevas filas para fichas de directorio.
  for v_rows in
    select jsonb_agg(t.value order by t.position) from (
      select items.value, row_number() over (order by s.chunk_index, items.ordinality) as position
      from public.catalog_staging s
      cross join lateral jsonb_array_elements(s.payload) with ordinality as items(value, ordinality)
      where s.run_id = p_run_id and s.entity = 'event_provenance'
        and items.value ->> 'classification_only' = 'true'
    ) t group by (t.position - 1) / 100 order by (t.position - 1) / 100
  loop
    perform * from public.apply_catalog_classifications(v_rows, '2026-10-27.1');
  end loop;

  if coalesce(array_length(p_reconcile_source_ids, 1), 0) > 0 then
    delete from public.events e
    where e.source_id = any(p_reconcile_source_ids)
      and e.last_seen_at < p_seen_at
      and not e.is_suppressed
      and not exists (
        select 1 from public.event_provenance p where p.event_id = e.id
        and p.classification ->> 'decision' = 'excluded'
      )
      -- Solo se concilian filas acreditadas como extraidas. Una publicacion
      -- manual no desaparece por faltar en el snapshot de una fuente.
      and e.extraction_method is distinct from 'manual'
      and exists (
        select 1 from public.event_provenance p where p.event_id = e.id
      )
      and not exists (
        select 1 from public.user_event_interactions i where i.event_id = e.id
      );
    get diagnostics v_removed_stale = row_count;
  end if;

  if p_delete_expired then
    -- La hora de vencimiento procede de PostgreSQL, no del reloj del runner.
    -- Los dos parametros horarios antiguos se conservan por compatibilidad.
    v_removed_expired := public.cleanup_expired_events();
  end if;

  delete from public.catalog_staging where run_id = p_run_id;
  delete from public.catalog_staging where created_at < now() - interval '7 days';

  return query select v_removed_stale, v_removed_expired;
end;
$$;

revoke all on function public.publish_staged_catalog(
  uuid, uuid[], timestamptz, boolean, timestamptz, timestamptz
) from public, anon, authenticated;
grant execute on function public.publish_staged_catalog(
  uuid, uuid[], timestamptz, boolean, timestamptz, timestamptz
) to service_role;

-- Mantenimiento del catálogo existente: solo categorías/estado y procedencia.
-- No borra filas, no toca textos, fechas, usuarios ni interacciones.
create or replace function public.apply_catalog_classifications(p_rows jsonb, p_version text)
returns table (updated integer, skipped integer)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_row jsonb;
  v_meta jsonb;
  v_event public.events%rowtype;
  v_name text;
  v_category uuid;
  v_updated integer := 0;
  v_skipped integer := 0;
  v_allowed text[] := array['Música','Teatro','Danza','Cine y audiovisual','Artes visuales',
    'Literatura','Patrimonio','Artesanía','Gastronomía','Cultura del vino','Circo','Humor',
    'Culturas populares','Diseño','Artes mediales','Divulgación y pensamiento','Artes escénicas','Interdisciplina'];
begin
  if p_version is distinct from '2026-10-27.1'
     or p_rows is null or jsonb_typeof(p_rows) <> 'array' then
    raise exception 'Version o formato de clasificacion invalido';
  end if;
  if jsonb_array_length(p_rows) > 100 then
    raise exception 'Cada lote admite como maximo 100 eventos';
  end if;
  set local lock_timeout = '5s';
  for v_row in select value from jsonb_array_elements(p_rows) loop
    v_meta := v_row -> 'classification';
    if v_meta is null or jsonb_typeof(v_meta) <> 'object'
       or v_meta ->> 'version' is distinct from p_version
       or coalesce(v_meta ->> 'decision', '') not in ('accepted','review','excluded')
       or coalesce(jsonb_typeof(v_meta -> 'categories'), '') <> 'array' then
      raise exception 'Clasificacion incompleta o invalida';
    end if;
    if exists (
      select 1 from jsonb_array_elements(v_meta -> 'categories') c(value)
      where jsonb_typeof(c.value) <> 'string' or not ((c.value #>> '{}') = any(v_allowed))
    ) then
      raise exception 'Categoria fuera de la taxonomia';
    end if;
    select e.* into v_event from public.events e
    where e.id = (v_row ->> 'id')::uuid
      and e.extraction_method is distinct from 'manual' and not e.is_suppressed
      and e.status in ('published', 'draft')
      and exists (select 1 from public.event_provenance p where p.event_id = e.id)
      and not exists (select 1 from public.organizers o where o.id = e.organizer_id and o.is_blocked)
      and exists (select 1 from public.sources s where s.id = e.source_id and s.is_active)
    for update;
    if not found then
      v_skipped := v_skipped + 1;
      continue;
    end if;

    delete from public.event_categories where event_id = v_event.id;
    for v_name in select distinct value from jsonb_array_elements_text(v_meta -> 'categories') loop
      -- El cliente usa stable_uuid('category', nombre); no requiere extensiones nuevas.
      v_category := (v_row -> 'category_ids' ->> v_name)::uuid;
      if v_category is null then
        raise exception 'Falta el identificador de categoria';
      end if;
      insert into public.categories(id, name) values (v_category, v_name)
        on conflict(id) do update set name = excluded.name;
      insert into public.event_categories(event_id, category_id) values(v_event.id, v_category)
        on conflict do nothing;
    end loop;
    update public.event_provenance set classification = v_meta where event_id = v_event.id;
    update public.events set status = case
      when v_meta ->> 'decision' = 'excluded' then 'archived'
      when v_meta ->> 'decision' = 'review' then 'draft'
      -- No publicar borradores creados/ocultados por una persona.
      else v_event.status end
    where id = v_event.id;
    v_updated := v_updated + 1;
  end loop;
  return query select v_updated, v_skipped;
end;
$$;
revoke all on function public.apply_catalog_classifications(jsonb, text) from public, anon, authenticated;
grant execute on function public.apply_catalog_classifications(jsonb, text) to service_role;

commit;
