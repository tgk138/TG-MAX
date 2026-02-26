from celery import Celery
from celery.signals import worker_process_init
from celery.schedules import schedule

from app.config import settings
from app.models.base import reset_db_engine

celery = Celery(
    "tgmax",
    broker=settings.REDIS_URL,
    include=["app.worker.tasks"],
)

celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    worker_concurrency=settings.WORKER_CONCURRENCY,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_default_queue="default",
    task_routes={
        "app.worker.tasks.import_channel": {"queue": "import"},
        "app.worker.tasks.prepare_selected_for_publish": {"queue": "import"},
        "app.worker.tasks.fail_stale_importing": {"queue": "import"},
        "app.worker.tasks.publish_to_max": {"queue": "publish"},
        "app.worker.tasks.poll_autopost_links": {"queue": "publish"},
    },
    beat_schedule={
        "fail-stale-importing-every-2-min": {
            "task": "app.worker.tasks.fail_stale_importing",
            "schedule": schedule(max(30, int(settings.IMPORT_STALE_CHECK_SECONDS))),
        },
        "poll-autopost-links-every-2-min": {
            "task": "app.worker.tasks.poll_autopost_links",
            "schedule": schedule(120),
        },
    },
)


@worker_process_init.connect
def _on_worker_process_init(**kwargs):
    # Important for asyncpg + prefork: child process must not reuse inherited pool.
    reset_db_engine()
