-- Limpieza por vencimiento para eventos manuales y extraidos.
-- Aplicar esta migracion solo crea la funcion; no ejecuta ningun borrado.
-- La integracion posterior debe sustituir el DELETE de vencidos de
-- publish_staged_catalog, no anadir una segunda limpieza sin protecciones.

begin;

create or replace function public.cleanup_expired_events()
returns bigint
language plpgsql
security definer
set search_path = ''
set lock_timeout = '5s'
as $$
declare
  -- Se usa el reloj de PostgreSQL, nunca una fecha enviada por el cliente.
  v_now timestamptz := pg_catalog.now();
  v_day_start timestamptz := pg_catalog.date_trunc(
    'day', v_now at time zone 'America/Santiago'
  ) at time zone 'America/Santiago';
  v_deleted bigint;
begin
  -- Impide insertar senales durante la comprobacion y el borrado. El bloqueo
  -- se libera al terminar la transaccion y falla tras 5 s si hay contencion.
  lock table public.user_event_interactions in share mode;

  delete from public.events as e
  where not e.is_suppressed
    and e.start_at is not null
    and (e.end_at is null or e.end_at >= e.start_at)
    and (
      (e.end_at is not null and e.end_at < v_now)
      or (e.end_at is null and e.start_at < v_day_start)
    )
    -- Proteccion temporal hasta desacoplar las afinidades del evento.
    and not exists (
      select 1
      from public.user_event_interactions as i
      where i.event_id = e.id
    );

  get diagnostics v_deleted = row_count;
  return v_deleted;
end;
$$;

comment on function public.cleanup_expired_events() is
  'Elimina eventos vencidos usando el reloj del servidor y el dia de Santiago; '
  'conserva fechas incompletas o inconsistentes, supresiones e interacciones. '
  'No se ejecuta automaticamente al instalarse.';

revoke all on function public.cleanup_expired_events()
  from public, anon, authenticated;
grant execute on function public.cleanup_expired_events()
  to service_role;

commit;
