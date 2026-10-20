#!/usr/bin/env bash
# PostgreSQL temporal de pruebas. No lee .env ni usa la base de produccion.
set -euo pipefail

if [[ "${PGDATABASE:-}" != "eventos_test" ]]; then
  echo "ERROR: PGDATABASE debe ser eventos_test (base temporal de pruebas)." >&2
  exit 1
fi

cd "$(dirname "$0")/.."
psql -X --set ON_ERROR_STOP=1 --file supabase/tests/fixtures/bootstrap.sql
for migration in supabase/migrations/*.sql; do
  echo "Aplicando migracion de prueba: ${migration##*/}"
  psql -X --set ON_ERROR_STOP=1 --file "$migration"
done
for test_file in supabase/tests/*.sql; do
  echo "Ejecutando prueba SQL: ${test_file##*/}"
  psql -X --set ON_ERROR_STOP=1 --file "$test_file"
done
echo "Todas las migraciones y pruebas SQL finalizaron correctamente."
