-- Solo base efímera de pruebas. Todas las filas se revierten al terminar.
begin;
insert into public.sources(id,name) values ('00000000-0000-4000-8000-000000000501','TEST taxonomy');
insert into public.organizers(id,name,is_blocked) values ('00000000-0000-4000-8000-000000000502','TEST blocked',true);
insert into public.events(id,external_key,source_id,title,start_at,description,extraction_method,is_suppressed,organizer_id,location,location_precision,location_source)
select ('00000000-0000-4000-8000-'||lpad(n::text,12,'0'))::uuid, 'TEST-taxonomy-'||n,
 '00000000-0000-4000-8000-000000000501'::uuid, 'TEST event '||n, now()+interval '1 day',
 'Texto original preservado', case when n=506 then 'manual' else 'jsonld' end,
 n=507, case when n=508 then '00000000-0000-4000-8000-000000000502'::uuid end,
 'Santiago','commune','source-data'
from generate_series(503,508) n;
insert into public.event_provenance(event_id,source_description,is_rewritten)
select id,'Procedencia preservada',true from public.events where external_key like 'TEST-taxonomy-%';
insert into auth.users(id) values ('00000000-0000-4000-8000-000000000509');
insert into public.user_event_interactions(user_id,event_id,interaction_type)
values ('00000000-0000-4000-8000-000000000509','00000000-0000-4000-8000-000000000505','interested');

do $$
declare
 v_rows jsonb;
 v_result record;
begin
 if has_function_privilege('anon','public.apply_catalog_classifications(jsonb,text)','EXECUTE')
 or has_function_privilege('authenticated','public.apply_catalog_classifications(jsonb,text)','EXECUTE')
 or not has_function_privilege('service_role','public.apply_catalog_classifications(jsonb,text)','EXECUTE') then
   raise exception 'Incorrect maintenance privileges';
 end if;
 select jsonb_agg(jsonb_build_object('id',e.id,'category_ids',jsonb_build_object('Música','00000000-0000-4000-8000-000000000510'),
 'classification',jsonb_build_object('version','2026-10-27.1','decision',
 case when e.external_key='TEST-taxonomy-504' then 'review' when e.external_key='TEST-taxonomy-505' then 'excluded' else 'accepted' end,
 'categories',jsonb_build_array('Música'),'raw_categories',jsonb_build_array('CONCIERTO'),'formats',jsonb_build_array('Concierto'))))
 into v_rows from public.events e where e.external_key like 'TEST-taxonomy-%';
 select * into v_result from public.apply_catalog_classifications(v_rows,'2026-10-27.1');
 if v_result.updated <> 3 or v_result.skipped <> 3 then
   raise exception 'Incorrect classification counts: %',row_to_json(v_result);
 end if;
 if (select status from public.events where external_key='TEST-taxonomy-504') <> 'draft'
 or (select status from public.events where external_key='TEST-taxonomy-505') <> 'archived' then
   raise exception 'Review/exclusion incorrectly published';
 end if;
 if (select count(*) from public.user_event_interactions) <> 1
 or exists(select 1 from public.events where external_key like 'TEST-taxonomy-%' and description <> 'Texto original preservado')
 or exists(select 1 from public.event_provenance where not is_rewritten or source_description <> 'Procedencia preservada') then
   raise exception 'Classification changed unrelated content/history';
 end if;
 -- La segunda llamada no debe resucitar ni duplicar las filas archivadas.
 perform * from public.apply_catalog_classifications(v_rows,'2026-10-27.1');
 if (select count(*) from public.event_categories) <> 3 then
   raise exception 'Category links duplicated or lost';
 end if;
 begin
   perform * from public.apply_catalog_classifications(
     jsonb_build_array(jsonb_build_object('id','00000000-0000-4000-8000-000000000503',
       'classification',jsonb_build_object('version','2026-10-27.1','decision','accepted','categories',jsonb_build_array('Deportes')))),
     '2026-10-27.1');
   raise exception 'Invalid category accepted';
 exception when raise_exception then
   if sqlerrm='Invalid category accepted' then raise; end if;
 end;
end;
$$;

-- El nuevo campo debe sobrevivir al flujo atómico normal, no solo al mantenimiento.
insert into public.catalog_staging(run_id,entity,chunk_index,payload)
select '00000000-0000-4000-8000-000000000511','events',0,jsonb_build_array(to_jsonb(e))
from public.events e where external_key='TEST-taxonomy-503';
insert into public.catalog_staging(run_id,entity,chunk_index,payload)
select '00000000-0000-4000-8000-000000000511','event_provenance',0,
jsonb_build_array(to_jsonb(p)||jsonb_build_object('classification',jsonb_build_object('version','2026-10-27.1','formats',jsonb_build_array('Taller'))))
from public.event_provenance p where event_id='00000000-0000-4000-8000-000000000503';
select * from public.publish_staged_catalog('00000000-0000-4000-8000-000000000511',array[]::uuid[],now(),false,now(),now());
do $$ begin
 if (select classification->'formats' from public.event_provenance where event_id='00000000-0000-4000-8000-000000000503') <> '["Taller"]'::jsonb then
   raise exception 'Atomic publisher lost classification';
 end if;
end $$;

-- Archivo automático desde staging; preserva las señales de recomendación.
insert into public.user_event_interactions(user_id,event_id,interaction_type)
values ('00000000-0000-4000-8000-000000000509','00000000-0000-4000-8000-000000000503','interested');
insert into public.catalog_staging(run_id,entity,chunk_index,payload)
values ('00000000-0000-4000-8000-000000000512','event_provenance',0,
 '[{"classification_only":true,"id":"00000000-0000-4000-8000-000000000503","category_ids":{},"classification":{"version":"2026-10-27.1","decision":"excluded","categories":[],"reason":"Ficha de directorio"}}]'::jsonb);
select * from public.publish_staged_catalog('00000000-0000-4000-8000-000000000512',
 array['00000000-0000-4000-8000-000000000501'::uuid],now(),false,now(),now());
do $$ begin
 if (select status from public.events where external_key='TEST-taxonomy-503') <> 'archived'
 or (select count(*) from public.user_event_interactions) <> 2
 or not exists(select 1 from public.events where external_key='TEST-taxonomy-505') then
   raise exception 'Staged classification did not archive/protect existing rows';
 end if;
end $$;
rollback;
