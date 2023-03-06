import os
from datetime import datetime

import aiofiles

import mongo
from common.settings import settings
from common.utils import utcnow


def get_app_distro_filename(tr: dict):
    return os.path.join(settings.CYKUBE_CACHE_DIR, f'{tr["sha"]}.tar.lz4')


async def cleanup():
    """
    Remove stale cached distributions
    """
    # remove inactive testruns
    testruns = await mongo.get_stale_testruns()
    if testruns:
        # delete stale distributions
        for tr in testruns:
            path = get_app_distro_filename(tr)
            if await aiofiles.os.path.exists(path):
                await aiofiles.os.remove(path)
        # and remove them from the local mongo: it's only need to retain state while a testrun is active
        await mongo.remove_testruns([x['id'] for x in testruns])

    # finally just remove any files (i.e dist caches) that haven't been read in a while
    today = utcnow()
    for name in await aiofiles.os.listdir(settings.CYKUBE_CACHE_DIR):
        path = os.path.join(settings.CYKUBE_CACHE_DIR, name)
        st = await aiofiles.os.stat(path)
        last_read_estimate = datetime.fromtimestamp(st.st_atime)
        if (today - last_read_estimate).days > settings.DIST_CACHE_STATENESS_WINDOW_DAYS:
            await aiofiles.os.remove(path)
