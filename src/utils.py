import subprocess
from datetime import datetime
from json.encoder import JSONEncoder
from sys import stdout

from settings import settings


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


def runcmd(cmd: str, logfile=None):
    if not logfile:
        logfile = stdout
    logfile.write(cmd+'\n')
    subprocess.check_call(cmd, shell=True, stderr=logfile, stdout=logfile)


def log(logfile, msg):
    logfile.write(msg+"\n")
    logfile.flush()


def create_file_path(sha: str, path: str) -> str:
    i = path.find('build/results/')
    return f'{settings.RESULT_URL}/{sha}/{path[i+14:]}'
