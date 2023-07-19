import datetime
import json

from freezegun import freeze_time
from pytz import utc

from cache import prune_cache, get_cached_item, add_cached_item, expired_cached_items_iter
from common.schemas import NewTestRun
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


async def test_expired_cache_iterator(redis, testrun: NewTestRun):
    with freeze_time("2022-01-28 10:00:00Z"):
        await add_cached_item('key1', 10)
    items = []
    with freeze_time("2022-04-28 10:00:00Z"):
        async for item in expired_cached_items_iter():
            items.append(item)
    assert len(items) == 1


async def test_node_cache_expiry_update(redis):
    with freeze_time("2022-01-28 10:00:00Z"):
        await add_cached_item('key1', 10)

    now = datetime.datetime(2022, 4, 28, 10, 0, 0, tzinfo=utc)
    with freeze_time(now):
        # fetch it
        item = await get_cached_item('key1')
        assert item.expires == now + datetime.timedelta(seconds=settings.NODE_DISTRIBUTION_CACHE_TTL)


async def test_prune_cache(redis, mocker, k8_custom_api_mock):
    with freeze_time("2022-01-28 10:00:00Z"):
        await add_cached_item('key1', 10)
    with freeze_time("2022-03-28 10:00:00Z"):
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
