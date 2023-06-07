import datetime
import json

from freezegun import freeze_time
from pytz import utc

from common.schemas import NewTestRun
from db import add_cached_item, expired_cached_items_iter, get_cached_item
from jobs import prune_cache
from settings import settings
from ws import handle_websocket_message


@freeze_time('2022-04-03 14:10:00Z')
async def test_check_add_cached_item(redis):
    await add_cached_item('node-snap-absd234weefw', 10)
    cachestr = redis.get(f'cache:node-snap-absd234weefw')

    assert json.loads(cachestr) == {'name': 'node-snap-absd234weefw',
                                    'ttl': 108000,
                                    'storage_size': 10,
                                    'specs': None,
                                    'expires': '2022-04-04T20:10:00+00:00'
                                    }


async def test_expired_cache_iterator(redis, mocker, testrun: NewTestRun):
    mocker.patch('db.utcnow', return_value=datetime.datetime(2022, 1, 28, 10, 0, 0, tzinfo=utc))
    await add_cached_item('key1', 10)
    items = []
    mocker.patch('db.utcnow', return_value=datetime.datetime(2022, 4, 28, 10, 0, 0, tzinfo=utc))
    async for item in expired_cached_items_iter():
        items.append(item)
    assert len(items) == 1


async def test_node_cache_expiry_update(redis, mocker):
    mocker.patch('db.utcnow', return_value=datetime.datetime(2022, 1, 28, 10, 0, 0, tzinfo=utc))
    await add_cached_item('key1', 10)
    now = datetime.datetime(2022, 4, 28, 10, 0, 0, tzinfo=utc)
    mocker.patch('db.utcnow', return_value=now)
    # fetch it
    item = await get_cached_item('key1')
    assert item.expires == now + datetime.timedelta(seconds=settings.NODE_DISTRIBUTION_CACHE_TTL)


async def test_prune_cache(redis, mocker, k8_custom_api_mock):
    mocker.patch('db.utcnow', return_value=datetime.datetime(2022, 1, 28, 10, 0, 0, tzinfo=utc))
    await add_cached_item('key1', 10)
    mocker.patch('db.utcnow', return_value=datetime.datetime(2022, 3, 28, 10, 0, 0, tzinfo=utc))
    delete_snapshot = k8_custom_api_mock.delete_namespaced_custom_object = mocker.AsyncMock()

    await prune_cache()

    delete_snapshot.assert_called_once()
    assert delete_snapshot.call_args.kwargs == {'group': 'snapshot.storage.k8s.io',
                                                'version': 'v1beta1',
                                                'namespace': 'cykubed',
                                                'plural': 'volumesnapshots',
                                                'name': 'key1'}


async def test_clear_cache(redis, mocker, k8_custom_api_mock):
    await add_cached_item('key1', 10)
    delete_snapshot = k8_custom_api_mock.delete_namespaced_custom_object = mocker.AsyncMock()
    await handle_websocket_message({'command': 'clear_cache', 'payload': ''})
    delete_snapshot.assert_called_once()
    assert delete_snapshot.call_args.kwargs == {'group': 'snapshot.storage.k8s.io',
                                                'version': 'v1beta1',
                                                'namespace': 'cykubed',
                                                'plural': 'volumesnapshots',
                                                'name': 'key1'}
