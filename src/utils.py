import subprocess
from datetime import datetime
from json.encoder import JSONEncoder


class CustomJSONEncoder(JSONEncoder):
    def default(self, obj):
        try:
            if isinstance(obj, datetime):
                return obj.isoformat()
            iterable = iter(obj)
        except TypeError:
            pass
        else:
            return list(iterable)
        return JSONEncoder.default(self, obj)


def now():
    return datetime.now()


def runcmd(cmd: str, logfile):
    subprocess.check_call(cmd, shell=True, stderr=logfile, stdout=logfile)
