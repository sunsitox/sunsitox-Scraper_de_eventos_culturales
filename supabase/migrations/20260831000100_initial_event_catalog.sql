begin;

create extension if not exists pgcrypto;

create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create table if not exists public.sources (
  id uuid primary key,
  name text not null,
  website_url text,
  is_active boolean not null default true,
  last_seen_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create unique index if not exists sources_name_lower_key on public.sources (lower(name));

create table if not exists public.organizers (
  id uuid primary key,
  name text not null,
  entity_type text,
  last_seen_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create unique index if not exists organizers_name_lower_key on public.organizers (lower(name));

create table if not exists public.communes (
  id uuid primary key,
  name text not null,
  region text,
  country text not null default 'Chile',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create unique index if not exists communes_identity_lower_key
  on public.communes (lower(country), lower(coalesce(region, '')), lower(name));

create table if not exists public.categories (
  id uuid primary key,
  name text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create unique index if not exists categories_name_lower_key on public.categories (lower(name));

create table if not exists public.scrape_runs (
  id uuid primary key default gen_random_uuid(),
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  status text not null check (status in ('running', 'succeeded', 'failed')),
  events_count integer not null default 0 check (events_count >= 0),
  error_message text,
  metadata jsonb not null default '{}'::jsonb
);

create table if not exists public.events (
  id uuid primary key,
  external_key text not null unique,
  source_id uuid not null references public.sources(id) on delete restrict,
  organizer_id uuid references public.organizers(id) on delete set null,
  commune_id uuid references public.communes(id) on delete set null,
  title text not null,
  start_at timestamptz,
  end_at timestamptz,
  venue text,
  address text,
  city text,
  region text,
  country text not null default 'Chile',
  status text not null default 'published'
    check (status in ('published', 'cancelled', 'draft', 'archived')),
  audience text,
  is_free boolean,
  description text,
  price_text text,
  currency text not null default 'CLP',
  image_url text,
  source_url text,
  official_url text,
  extraction_method text,
  scraped_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists events_start_at_idx on public.events (start_at);
create index if not exists events_region_start_idx on public.events (region, start_at);
create index if not exists events_status_start_idx on public.events (status, start_at);
create index if not exists events_source_id_idx on public.events (source_id);
create index if not exists events_commune_id_idx on public.events (commune_id);

create table if not exists public.event_categories (
  event_id uuid not null references public.events(id) on delete cascade,
  category_id uuid not null references public.categories(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (event_id, category_id)
);
create index if not exists event_categories_category_idx on public.event_categories (category_id);

create table if not exists public.media_assets (
  id uuid primary key,
  event_id uuid not null references public.events(id) on delete cascade,
  url text not null,
  media_type text not null default 'image'
    check (media_type in ('image', 'video', 'audio', 'document')),
  is_primary boolean not null default false,
  created_at timestamptz not null default now(),
  unique (event_id, url)
);
create index if not exists media_assets_event_idx on public.media_assets (event_id);

-- Entidades futuras de la aplicación; no participan en la ingesta.
create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  display_name text,
  role text not null default 'visitor' check (role in ('visitor', 'editor', 'admin')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.favorites (
  user_id uuid not null references auth.users(id) on delete cascade,
  event_id uuid not null references public.events(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (user_id, event_id)
);

create table if not exists public.reports (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  event_id uuid not null references public.events(id) on delete cascade,
  reason text not null,
  status text not null default 'pending' check (status in ('pending', 'reviewed', 'resolved')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists reports_status_idx on public.reports (status, created_at);

drop trigger if exists sources_set_updated_at on public.sources;
create trigger sources_set_updated_at before update on public.sources
for each row execute function public.set_updated_at();
drop trigger if exists organizers_set_updated_at on public.organizers;
create trigger organizers_set_updated_at before update on public.organizers
for each row execute function public.set_updated_at();
drop trigger if exists communes_set_updated_at on public.communes;
create trigger communes_set_updated_at before update on public.communes
for each row execute function public.set_updated_at();
drop trigger if exists categories_set_updated_at on public.categories;
create trigger categories_set_updated_at before update on public.categories
for each row execute function public.set_updated_at();
drop trigger if exists events_set_updated_at on public.events;
create trigger events_set_updated_at before update on public.events
for each row execute function public.set_updated_at();
drop trigger if exists profiles_set_updated_at on public.profiles;
create trigger profiles_set_updated_at before update on public.profiles
for each row execute function public.set_updated_at();
drop trigger if exists reports_set_updated_at on public.reports;
create trigger reports_set_updated_at before update on public.reports
for each row execute function public.set_updated_at();

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.profiles (id, display_name)
  values (new.id, coalesce(new.raw_user_meta_data ->> 'display_name', new.raw_user_meta_data ->> 'full_name'))
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users
for each row execute function public.handle_new_user();

alter table public.sources enable row level security;
alter table public.organizers enable row level security;
alter table public.communes enable row level security;
alter table public.categories enable row level security;
alter table public.scrape_runs enable row level security;
alter table public.events enable row level security;
alter table public.event_categories enable row level security;
alter table public.media_assets enable row level security;
alter table public.profiles enable row level security;
alter table public.favorites enable row level security;
alter table public.reports enable row level security;

revoke all on table public.sources, public.organizers, public.communes, public.categories,
  public.scrape_runs, public.events, public.event_categories, public.media_assets,
  public.profiles, public.favorites, public.reports from anon, authenticated;
grant select on table public.sources, public.organizers, public.communes, public.categories,
  public.events, public.event_categories, public.media_assets to anon, authenticated;
grant select on table public.profiles to authenticated;
grant update (display_name) on table public.profiles to authenticated;
grant select, insert, delete on table public.favorites to authenticated;
grant select, insert on table public.reports to authenticated;
grant all on table public.sources, public.organizers, public.communes, public.categories,
  public.scrape_runs, public.events, public.event_categories, public.media_assets,
  public.profiles, public.favorites, public.reports to service_role;

drop policy if exists "Public sources are readable" on public.sources;
create policy "Public sources are readable" on public.sources
for select to anon, authenticated using (is_active);
drop policy if exists "Public organizers are readable" on public.organizers;
create policy "Public organizers are readable" on public.organizers
for select to anon, authenticated using (true);
drop policy if exists "Public communes are readable" on public.communes;
create policy "Public communes are readable" on public.communes
for select to anon, authenticated using (true);
drop policy if exists "Public categories are readable" on public.categories;
create policy "Public categories are readable" on public.categories
for select to anon, authenticated using (true);
drop policy if exists "Published events are readable" on public.events;
create policy "Published events are readable" on public.events
for select to anon, authenticated using (status = 'published');
drop policy if exists "Published event categories are readable" on public.event_categories;
create policy "Published event categories are readable" on public.event_categories
for select to anon, authenticated using (
  exists (select 1 from public.events
    where events.id = event_categories.event_id and events.status = 'published')
);
drop policy if exists "Published media are readable" on public.media_assets;
create policy "Published media are readable" on public.media_assets
for select to anon, authenticated using (
  exists (select 1 from public.events
    where events.id = media_assets.event_id and events.status = 'published')
);
drop policy if exists "Users read own profile" on public.profiles;
create policy "Users read own profile" on public.profiles
for select to authenticated using ((select auth.uid()) = id);
drop policy if exists "Users update own profile" on public.profiles;
create policy "Users update own profile" on public.profiles
for update to authenticated using ((select auth.uid()) = id)
with check ((select auth.uid()) = id);
drop policy if exists "Users read own favorites" on public.favorites;
create policy "Users read own favorites" on public.favorites
for select to authenticated using ((select auth.uid()) = user_id);
drop policy if exists "Users create own favorites" on public.favorites;
create policy "Users create own favorites" on public.favorites
for insert to authenticated with check ((select auth.uid()) = user_id);
drop policy if exists "Users delete own favorites" on public.favorites;
create policy "Users delete own favorites" on public.favorites
for delete to authenticated using ((select auth.uid()) = user_id);
drop policy if exists "Users read own reports" on public.reports;
create policy "Users read own reports" on public.reports
for select to authenticated using ((select auth.uid()) = user_id);
drop policy if exists "Users create own reports" on public.reports;
create policy "Users create own reports" on public.reports
for insert to authenticated with check ((select auth.uid()) = user_id);

revoke all on function public.set_updated_at() from public;
revoke all on function public.handle_new_user() from public;

commit;
