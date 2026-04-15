from celery import Celery
from verisend.settings import settings

celery_app = Celery(
    "verisend",
    broker=settings.rabbitmq_url.get_secret_value(),
    include=["verisend.workers.tasks"],
)

celery_app.conf.update(
    task_default_queue=settings.rabbitmq_queue_name,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
)