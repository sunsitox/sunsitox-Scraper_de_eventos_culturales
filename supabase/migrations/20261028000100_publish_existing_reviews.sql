-- Cambio de politica solicitado: review puede ser visible, sin convertirse en accepted.
-- Backfill acotado e idempotente. NO restaura archivados ni borradores manuales.
-- Un unico bloque transaccional; no modifica esquema, RLS, categorias ni textos.
do $$
declare
  v_now timestamptz := pg_catalog.now();
  v_day_start timestamptz := pg_catalog.date_trunc(
    'day', v_now at time zone 'America/Santiago'
  ) at time zone 'America/Santiago';
  v_published integer;
begin
  set local lock_timeout = '5s';
  with candidates as materialized (
    select e.id
    from public.events e
    join public.event_provenance p on p.event_id = e.id
    join public.sources s on s.id = e.source_id
    where e.status = 'draft'
      and not e.is_suppressed
      and e.extraction_method is distinct from 'manual'
      and e.start_at is not null
      and (
        (e.end_at is null and e.start_at >= v_day_start)
        or (e.end_at is not null and e.end_at >= v_now and e.end_at >= e.start_at)
      )
      and s.is_active
      -- Proveedores retirados en esta revision: no reactivar su catalogo.
      and s.name not in ('Rancagua Cultura', 'Teatro del Lago', 'Teatro Oriente')
      and not exists (
        select 1 from public.organizers o where o.id = e.organizer_id and o.is_blocked
      )
      and p.classification ->> 'version' = '2026-10-27.1'
      and p.classification ->> 'content_kind' = 'event'
      and p.classification ->> 'decision' = 'review'
      and p.classification ->> 'publication_status' = 'draft'
    for update of e, p
  ), published as (
    update public.events e set status = 'published'
    from candidates c where e.id = c.id
    returning e.id
  )
  update public.event_provenance p
  set classification = p.classification || jsonb_build_object(
    'publication_status', 'published', 'review_action', 'keep'
  )
  from published e where p.event_id = e.id;
  get diagnostics v_published = row_count;
  raise notice 'Visibilidad de revisiones: % eventos publicados; decision review conservada', v_published;
end;
$$;
