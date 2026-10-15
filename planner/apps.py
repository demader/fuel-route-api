import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class PlannerConfig(AppConfig):
    name = 'planner'


def warm_up():
    """Load the gazetteer and station index into memory (called once from wsgi.py).

    Kept out of AppConfig.ready() because it reads the database. Failure (e.g. before
    migrations have run) is harmless: both load lazily on first use instead.
    """
    from .services.gazetteer import get_gazetteer
    from .services.stations import get_station_index

    try:
        get_gazetteer()
        logger.info("Loaded %d stations into memory", len(get_station_index()))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Warm-up skipped: %s", exc)
