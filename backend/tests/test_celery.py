from app.core.celery_app import celery_app


def test_celery_app_exists():
    assert celery_app is not None
    assert celery_app.conf.broker_url is not None
    assert celery_app.conf.broker_url.startswith("redis://")
