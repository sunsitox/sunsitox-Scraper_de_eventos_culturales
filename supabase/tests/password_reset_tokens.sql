-- Base de PRUEBAS unicamente. No modifica contrasenas ni envia correos.
begin;

insert into auth.users (id)
values ('00000000-0000-4000-8000-000000000041');

do $$
declare
  v_role text;
  v_operation text;
begin
  if not (select relrowsecurity from pg_class
    where oid = 'public.password_reset_tokens'::regclass) then
    raise exception 'RLS must be enabled on password_reset_tokens';
  end if;
  if exists (select 1 from pg_policies
    where schemaname = 'public' and tablename = 'password_reset_tokens') then
    raise exception 'Token table must have no frontend policies';
  end if;
  foreach v_role in array array['anon', 'authenticated'] loop
    foreach v_operation in array array['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'] loop
      if has_table_privilege(v_role, 'public.password_reset_tokens', v_operation) then
        raise exception 'Unexpected token privilege: % %', v_role, v_operation;
      end if;
    end loop;
  end loop;
  foreach v_operation in array array['SELECT', 'INSERT', 'UPDATE', 'DELETE'] loop
    if not has_table_privilege('service_role', 'public.password_reset_tokens', v_operation) then
      raise exception 'Missing Edge Function privilege: %', v_operation;
    end if;
  end loop;
end;
$$;

-- La rol service_role real omite RLS. Aqui se ejercen sus privilegios mediante
-- la asercion anterior; los datos ficticios prueban esquema, PK y cascada.
insert into public.password_reset_tokens (jti, user_id, email, expires_at)
values ('00000000-0000-4000-8000-000000000042',
        '00000000-0000-4000-8000-000000000041',
        'qa-reset@example.test', now() + interval '30 minutes');

do $$
begin
  if not exists (select 1 from public.password_reset_tokens
    where jti = '00000000-0000-4000-8000-000000000042'
      and used_at is null and created_at = now()) then
    raise exception 'Token defaults are incompatible with Edge Functions';
  end if;
  begin
    insert into public.password_reset_tokens (jti, user_id, email, expires_at)
    values ('00000000-0000-4000-8000-000000000042',
            '00000000-0000-4000-8000-000000000041',
            'qa-reset@example.test', now() + interval '30 minutes');
    raise exception 'Duplicate jti must be rejected';
  exception when unique_violation then null;
  end;
  begin
    insert into public.password_reset_tokens (jti, user_id, email, expires_at)
    values ('00000000-0000-4000-8000-000000000043',
            '00000000-0000-4000-8000-000000000099',
            'qa-invalid@example.test', now() + interval '30 minutes');
    raise exception 'Unknown user must be rejected';
  exception when foreign_key_violation then null;
  end;
end;
$$;

update public.password_reset_tokens set used_at = now()
where jti = '00000000-0000-4000-8000-000000000042' and used_at is null;
do $$
begin
  if not exists (select 1 from public.password_reset_tokens
    where jti = '00000000-0000-4000-8000-000000000042' and used_at = now()) then
    raise exception 'Token consumption marker cannot be written';
  end if;
end;
$$;

delete from auth.users where id = '00000000-0000-4000-8000-000000000041';
do $$
begin
  if exists (select 1 from public.password_reset_tokens
    where jti = '00000000-0000-4000-8000-000000000042') then
    raise exception 'Deleting a user must cascade to their tokens';
  end if;
end;
$$;

rollback;
