from barricade.constants import LOGS_FOLDER

def _get_logs_format(name: str | None = None):
    if name:
        fmt = '[%(asctime)s][{}][%(levelname)s][%(module)s.%(funcName)s:%(lineno)s] %(message)s'.format(name)
    else:
        fmt = '[%(asctime)s][%(levelname)s][%(module)s.%(funcName)s:%(lineno)s] %(message)s'
    return fmt

import logging
logging.basicConfig(
    level=logging.INFO,
    format=_get_logs_format(name='other'),
    handlers=[
        logging.FileHandler(filename=LOGS_FOLDER / "app.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

def get_logger(community_id: int):
    logger = logging.getLogger(str(community_id))
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        name = f"community{community_id}"
        filename = f"{name}.log"

        handler = logging.FileHandler(filename=LOGS_FOLDER / filename, encoding='utf-8')
        handler.setFormatter(logging.Formatter(_get_logs_format()))
        logger.addHandler(handler)

        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_get_logs_format(name)))
        logger.addHandler(handler)
    return logger
