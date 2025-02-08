
import logging
import logging.config

from barricade.constants import LOGS_FOLDER

def _get_logs_format(name: str | None = None):
    if name:
        fmt = '[%(asctime)s][{}][%(levelname)s][%(module)s.%(funcName)s:%(lineno)s] %(message)s'.format(name)
    else:
        fmt = '[%(asctime)s][%(levelname)s][%(module)s.%(funcName)s:%(lineno)s] %(message)s'
    return fmt

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

UVICORN_LOG_CONFIG = {
    'version': 1,
    'formatters': {
        'app': {
            'format': _get_logs_format(name="app")
        },
        'web_access': {
            'format': _get_logs_format(name="web")
        },
        'web_error': {
            'format': '[%(asctime)s][web][%(levelname)s] %(message)s'
        },
        'nameless': {
            'format': _get_logs_format()
        },
        'nameless_noloc': {
            'format': '[%(asctime)s][%(levelname)s] %(message)s'
        },
    },
    'handlers': {
        'stream_app': {
            'level': logging.INFO,
            'formatter': 'app',
            'class': 'logging.StreamHandler',
        },
        'stream_web_access': {
            'level': logging.WARNING,
            'formatter': 'web_access',
            'class': 'logging.StreamHandler',
        },
        'stream_web_error': {
            'level': logging.INFO,
            'formatter': 'web_error',
            'class': 'logging.StreamHandler',
        },
        'file_app': {
            'level': logging.INFO,
            'formatter': 'nameless',
            'class': 'logging.FileHandler',
            'filename': LOGS_FOLDER / "app.log",
            'encoding': 'utf-8'
        },
        'file_web_access': {
            'level': logging.INFO,
            'formatter': 'nameless_noloc',
            'class': 'logging.FileHandler',
            'filename': LOGS_FOLDER / "web_access.log",
            'encoding': 'utf-8'
        },
        'file_web_error': {
            'level': logging.DEBUG,
            'formatter': 'nameless',
            'class': 'logging.FileHandler',
            'filename': LOGS_FOLDER / "web_error.log",
            'encoding': 'utf-8'
        },
    },
    'loggers': {
        '': {
            'handlers': ['stream_app', 'file_app'],
            'level': logging.INFO,
            'propagate': False
        },
        'uvicorn.access': {
            'handlers': ['stream_web_access', 'file_web_access'],
            'level': logging.INFO,
            'propagate': False
        },
        'uvicorn.error': {
            'handlers': ['stream_web_error', 'file_web_error'],
            'level': logging.INFO,
            'propagate': False
        }
    }
}
UVICORN_LOG_LEVEL = logging.INFO
