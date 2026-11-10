#!/bin/sh
set -e

# Apply database migrations before starting the web server. Celery workers
# set RUN_MIGRATIONS=0 so only one container migrates.
if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
    python manage.py migrate --noinput
fi

exec "$@"
