import json
import logging
from logging.config import dictConfig
from pprint import pprint

from utils import CustomJSONEncoder


class HCFilter(logging.Filter):
    def filter(self, record):
        return "/hc" not in record.getMessage()


def init():
    dictConfig({
        'version': 1,
        "formatters": {
            "text": {
                "format": '%(asctime)s: %(message)s'
            }
        },
        'handlers': {
            'default': {
                'class': 'logging.StreamHandler',
                'formatter': 'text'
            }
        },
        'celery': {
            'level': 'WARNING',
        },
        'root': {
            'level': 'INFO',
            'handlers': ['default'],
            'propagate': False
        },
    })


def json_pprint(data):
    """
    Useful for debugging - pretty print after running it through json
    :param ser:
    """
    pprint(json.loads(json.dumps(data, sort_keys=True, cls=CustomJSONEncoder)), indent=4)
