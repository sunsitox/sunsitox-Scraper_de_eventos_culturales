begin;

alter table public.events
  add column if not exists location text,
  add column if not exists location_precision text,
  add column if not exists location_source text;

update public.events
set
  location = coalesce(
    nullif(trim(concat_ws(', ', venue, address, city, region)), ''),
    nullif(trim(country), ''),
    'Chile'
  ),
  location_precision = case
    when lower(coalesce(venue, '') || ' ' || coalesce(address, '')) ~ '(online|en línea|zoom|streaming|virtual)'
      and nullif(trim(address), '') is null then 'online'
    when nullif(trim(address), '') is not null then 'exact'
    when nullif(trim(venue), '') is not null then 'venue'
    when nullif(trim(city), '') is not null then 'city'
    when nullif(trim(region), '') is not null then 'region'
    else 'country'
  end,
  location_source = 'geographic-fallback'
where location is null or location_precision is null or location_source is null;

alter table public.events
  drop constraint if exists events_location_precision_check,
  drop constraint if exists events_location_source_check;

alter table public.events
  alter column location set not null,
  alter column location_precision set not null,
  alter column location_source set not null,
  add constraint events_location_precision_check
    check (location_precision in ('exact', 'venue', 'commune', 'city', 'region', 'online', 'country')),
  add constraint events_location_source_check
    check (location_source in ('source-data', 'source-default', 'deterministic-inference', 'nvidia-nim', 'geographic-fallback'));

create index if not exists events_location_precision_idx
  on public.events (location_precision);

commit;
