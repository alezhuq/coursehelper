"""Apply procrastinate's own schema (job queue tables/functions/triggers).

The worker container needs these before it can launch - without them
`procrastinate ... worker` fails at startup with "function ... does not
exist". Applying via Alembic (rather than the separate `procrastinate
schema --apply` CLI) keeps schema application idempotent and ordered with
the rest of the api's `alembic upgrade head` startup step, and the worker
now depends on the api container being healthy (see docker-compose.yml) so
this migration is guaranteed to have run before the worker starts.

The schema SQL is pulled from the installed procrastinate package at
migration-authoring time so it always matches the pinned procrastinate
version (3.9.x) rather than being hand-copied and risking drift.

Revision ID: 0002_procrastinate_schema
Revises: 0001_baseline
Create Date: 2026-07-11

"""

from collections.abc import Sequence

from alembic import op
from procrastinate.schema import SchemaManager

# revision identifiers, used by Alembic.
revision: str = "0002_procrastinate_schema"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(SchemaManager.get_schema())


def downgrade() -> None:
    op.execute("""
        DROP TRIGGER IF EXISTS procrastinate_trigger_delete_jobs_v1
            ON procrastinate_jobs;
        DROP TRIGGER IF EXISTS procrastinate_trigger_abort_requested_events_v1
            ON procrastinate_jobs;
        DROP TRIGGER IF EXISTS procrastinate_trigger_scheduled_events_v1
            ON procrastinate_jobs;
        DROP TRIGGER IF EXISTS procrastinate_trigger_status_events_insert_v1
            ON procrastinate_jobs;
        DROP TRIGGER IF EXISTS procrastinate_trigger_status_events_update_v1
            ON procrastinate_jobs;
        DROP TRIGGER IF EXISTS procrastinate_jobs_notify_queue_job_aborted_v1
            ON procrastinate_jobs;
        DROP TRIGGER IF EXISTS procrastinate_jobs_notify_queue_job_inserted_v1
            ON procrastinate_jobs;

        DROP FUNCTION IF EXISTS procrastinate_prune_stalled_workers_v1(float);
        DROP FUNCTION IF EXISTS procrastinate_update_heartbeat_v1(bigint);
        DROP FUNCTION IF EXISTS procrastinate_unregister_worker_v1(bigint);
        DROP FUNCTION IF EXISTS procrastinate_register_worker_v1();
        DROP FUNCTION IF EXISTS procrastinate_unlink_periodic_defers_v1();
        DROP FUNCTION IF EXISTS procrastinate_trigger_abort_requested_events_procedure_v1();
        DROP FUNCTION IF EXISTS procrastinate_trigger_function_scheduled_events_v1();
        DROP FUNCTION IF EXISTS procrastinate_trigger_function_status_events_update_v1();
        DROP FUNCTION IF EXISTS procrastinate_trigger_function_status_events_insert_v1();
        DROP FUNCTION IF EXISTS procrastinate_notify_queue_abort_job_v1();
        DROP FUNCTION IF EXISTS procrastinate_notify_queue_job_inserted_v1();
        DROP FUNCTION IF EXISTS procrastinate_retry_job_v2(
            bigint, timestamptz, integer, varchar, varchar
        );
        DROP FUNCTION IF EXISTS procrastinate_retry_job_v1(
            bigint, timestamptz, integer, varchar, varchar
        );
        DROP FUNCTION IF EXISTS procrastinate_cancel_job_v1(bigint, boolean, boolean);
        DROP FUNCTION IF EXISTS procrastinate_finish_job_v1(
            bigint, procrastinate_job_status, boolean
        );
        DROP FUNCTION IF EXISTS procrastinate_fetch_job_v2(varchar[], bigint);
        DROP FUNCTION IF EXISTS procrastinate_defer_periodic_job_v2(
            varchar, varchar, varchar, varchar, integer, varchar, bigint, jsonb
        );
        DROP FUNCTION IF EXISTS procrastinate_defer_jobs_v1(procrastinate_job_to_defer_v1[]);

        DROP TABLE IF EXISTS procrastinate_events;
        DROP TABLE IF EXISTS procrastinate_periodic_defers;
        DROP TABLE IF EXISTS procrastinate_jobs;
        DROP TABLE IF EXISTS procrastinate_workers;

        DROP TYPE IF EXISTS procrastinate_job_to_defer_v1;
        DROP TYPE IF EXISTS procrastinate_job_event_type;
        DROP TYPE IF EXISTS procrastinate_job_status;
    """)
