-- Solo base efimera de pruebas. La migracion no contiene COMMIT y respeta este rollback.
begin;
insert into public.sources(id,name,is_active) values
 ('00000000-0000-4000-8000-000000000801','TEST review active',true),
 ('00000000-0000-4000-8000-000000000802','TEST review disabled',false),
 ('00000000-0000-4000-8000-000000000803','Teatro del Lago',true);
insert into public.organizers(id,name,is_blocked) values
 ('00000000-0000-4000-8000-000000000804','TEST review blocked',true);
insert into public.events(id,external_key,source_id,organizer_id,title,start_at,end_at,
 status,is_suppressed,extraction_method,description,location,location_precision,location_source)
select ('00000000-0000-4000-8000-'||lpad(n::text,12,'0'))::uuid,'TEST-review-'||n,
 case when n=814 then '00000000-0000-4000-8000-000000000802'::uuid
      when n=815 then '00000000-0000-4000-8000-000000000803'::uuid
      else '00000000-0000-4000-8000-000000000801'::uuid end,
 case when n=813 then '00000000-0000-4000-8000-000000000804'::uuid end,
 'TEST review '||n,
 case when n in (811,812) then now()-interval '2 days' else now()+interval '1 day' end,
 case when n=811 then now()-interval '1 day'
      when n=816 then now()+interval '1 hour' end,
 case when n=809 then 'archived' when n=810 then 'cancelled' else 'draft' end,
 n=808,case when n=807 then 'manual' else 'jsonld' end,'Texto no modificado',
 'Santiago','commune','source-data'
from generate_series(805,822) n;
insert into public.event_provenance(event_id,source_description,ocr_text,is_rewritten,classification)
select id,'Original conservado','OCR conservado',true,jsonb_build_object(
 'version','2026-10-27.1','decision',case when external_key='TEST-review-818' then 'excluded' else 'review' end,
 'content_kind',case when external_key='TEST-review-819' then 'directory' else 'event' end,
 'publication_status',case when external_key='TEST-review-820' then 'published' else 'draft' end,
 'categories',jsonb_build_array(),'evidence',jsonb_build_object('trace','preserved'))
from public.events where external_key like 'TEST-review-%' and external_key <> 'TEST-review-821';
update public.event_provenance set classification='{}' where event_id='00000000-0000-4000-8000-000000000817';
-- Otro borrador no tiene fecha: no puede hacerse visible.
update public.events set start_at=null where external_key='TEST-review-822';
insert into auth.users(id) values ('00000000-0000-4000-8000-000000000823');
insert into public.user_event_interactions(user_id,event_id,interaction_type) values
 ('00000000-0000-4000-8000-000000000823','00000000-0000-4000-8000-000000000805','interested');

\ir ../migrations/20261028000100_publish_existing_reviews.sql
\ir ../migrations/20261028000100_publish_existing_reviews.sql

do $$ begin
 if (select count(*) from public.events where external_key like 'TEST-review-%' and status='published') <> 2
 or exists(select 1 from public.events where external_key like 'TEST-review-%'
           and status='published' and external_key not in ('TEST-review-805','TEST-review-806')) then
   raise exception 'Se publicaron borradores protegidos o no se publico la revision elegible';
 end if;
 if exists(select 1 from public.event_provenance where event_id in (
   '00000000-0000-4000-8000-000000000805','00000000-0000-4000-8000-000000000806')
   and (classification->>'decision' <> 'review' or classification->>'publication_status' <> 'published'
        or classification->>'review_action' <> 'keep' or classification->'evidence'->>'trace' <> 'preserved')) then
   raise exception 'Se perdio la clasificacion o evidencia de la revision';
 end if;
 if (select count(*) from public.user_event_interactions) <> 1
 or (select count(*) from public.events where external_key like 'TEST-review-%') <> 18
 or exists(select 1 from public.events where external_key like 'TEST-review-%' and description <> 'Texto no modificado')
 or exists(select 1 from public.event_provenance where event_id in
   (select id from public.events where external_key like 'TEST-review-%')
   and (source_description <> 'Original conservado' or ocr_text <> 'OCR conservado' or not is_rewritten)) then
   raise exception 'El backfill modifico datos ajenos a la visibilidad';
 end if;
end $$;
rollback;
