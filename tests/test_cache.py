# import datetime
# import json
#
# from freezegun import freeze_time
# from pytz import utc
#
# from cache import prune_cache, get_cached_item, add_cached_item, expired_cached_items_iter
# from common.schemas import NewTestRun
# from settings import settings
# from ws import handle_websocket_message
#
#
# async def test_expired_cache_iterator(save_cached_item_mock, testrun: NewTestRun):
#     with freeze_time("2022-01-28 10:00:00Z"):
#         await add_cached_item(testrun.project.organisation_id, 'key1', 10)
#     items = []
#     with freeze_time("2022-04-28 10:00:00Z"):
#         for item in expired_cached_items_iter():
#             items.append(item)
#     assert len(items) == 1
#
#
# async def test_prune_cache(save_cached_item_mock,
#                            delete_cached_item_mock_factory,
#                            mocker, k8_custom_api_mock,
#                            testrun: NewTestRun):
#     with freeze_time("2022-01-28 10:00:00Z"):
#         await add_cached_item(testrun.project.organisation_id, 'key1', 10)
#     with freeze_time("2022-03-28 10:00:00Z"):
#         delete_snapshot = k8_custom_api_mock.delete_namespaced_custom_object = mocker.AsyncMock()
#
#         delete_mock = delete_cached_item_mock_factory('key1')
#         await prune_cache()
#
#         assert delete_mock.called
#         assert delete_snapshot.call_args.kwargs == {'group': 'snapshot.storage.k8s.io',
#                                                     'version': 'v1beta1',
#                                                     'namespace': 'cykubed',
#                                                     'plural': 'volumesnapshots',
#                                                     'name': 'key1'}
#
#
# async def test_clear_cache(delete_cached_item_mock_factory,
#                            save_cached_item_mock, mocker, k8_custom_api_mock, testrun: NewTestRun):
#     delete_item = delete_cached_item_mock_factory('key1')
#     await add_cached_item(testrun.project.organisation_id, 'key1', 10)
#     await add_cached_item(1000, 'key2', 10)
#     delete_snapshot = k8_custom_api_mock.delete_namespaced_custom_object = mocker.AsyncMock()
#     await handle_websocket_message({'command': 'clear_cache', 'payload': {'organisation_id': 5}})
#
#     delete_snapshot.assert_called_once()
#     assert delete_item.called
#     assert delete_snapshot.call_args.kwargs == {'group': 'snapshot.storage.k8s.io',
#                                                 'version': 'v1beta1',
#                                                 'namespace': 'cykubed',
#                                                 'plural': 'volumesnapshots',
#                                                 'name': 'key1'}
#     # only items for this org are removed
#     assert not await get_cached_item('key1', update_expiry=False, local_only=True)
#     assert await get_cached_item('key2', update_expiry=False, local_only=True)
