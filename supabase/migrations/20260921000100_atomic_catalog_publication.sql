begin;

create table if not exists public.catalog_staging (
  run_id uuid not null,
  entity text not null check (entity in (
    'sources', 'organizers', 'communes', 'categories', 'events',
    'event_provenance', 'event_categories', 'media_assets'
  )),
  chunk_index integer not null check (chunk_index >= 0),
  payload jsonb not null check (jsonb_typeof(payload) = 'array'),
  created_at timestamptz not null default now(),
  primary key (run_id, entity, chunk_index)
);

create index if not exists catalog_staging_created_idx
  on public.catalog_staging (created_at);

alter table public.catalog_staging enable row level security;
revoke all on table public.catalog_staging from public, anon, authenticated;
grant all on table public.catalog_staging to service_role;

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
    raise exception 'No existe staging para la ejecución %', p_run_id;
  end if;

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
  where s.run_id = p_run_id and s.entity = 'event_provenance';
  insert into public.event_provenance (
    event_id, original_url, source_description, ocr_text, is_rewritten
  )
  select event_id, original_url, source_description, ocr_text, is_rewritten
  from jsonb_populate_recordset(null::public.event_provenance, v_rows)
  on conflict (event_id) do update set
    original_url = excluded.original_url,
    source_description = excluded.source_description,
    ocr_text = excluded.ocr_text,
    is_rewritten = excluded.is_rewritten;

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

  if coalesce(array_length(p_reconcile_source_ids, 1), 0) > 0 then
    delete from public.events
    where source_id = any(p_reconcile_source_ids)
      and last_seen_at < p_seen_at
      and not is_suppressed;
    get diagnostics v_removed_stale = row_count;
  end if;

  if p_delete_expired then
    delete from public.events
    where end_at < p_current_time
       or (end_at is null and start_at < p_day_start);
    get diagnostics v_removed_expired = row_count;
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

commit;
