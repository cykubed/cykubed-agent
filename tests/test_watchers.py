import datetime

from freezegun import freeze_time
from httpx import Response

from common import schemas
from watchers import handle_pod_event


async def test_handle_post_event(respx_mock, mocker):
    store_duration = \
        respx_mock.post('https://api.cykubed.com/agent/testrun/20/pod-duration') \
            .mock(return_value=Response(200))

    pod = mocker.Mock()
    pod.status.phase = 'Succeeded'
    pod.status.start_time = datetime.datetime(2023, 6, 10, 10, 0, 0, tzinfo=datetime.timezone.utc)
    pod.metadata = mocker.Mock()
    pod.metadata.name = 'pod-deadbeef0101'
    pod.metadata.annotations = {}
    pod.metadata.labels = {'testrun_id': 20,
                           'cykubed_job': 'runner'}

    with freeze_time("2023-06-10 10:02:30Z"):
        await handle_pod_event(pod)

        assert store_duration.called

        st = schemas.PodDuration.parse_raw(store_duration.calls[0].request.content.decode())
        assert st.duration == 150
        assert not st.is_spot
        assert st.job_type == 'runner'
