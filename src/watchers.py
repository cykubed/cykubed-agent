from kubernetes_asyncio import watch
from kubernetes_asyncio.client import V1Job, V1JobStatus, V1ObjectMeta, V1Pod, V1PodStatus

from app import app
from common import schemas
from common.k8common import get_core_api, get_batch_api
from common.redisutils import async_redis
from common.utils import utcnow
from jobs import recreate_runner_job
from settings import settings
from state import get_build_state, check_is_spot


async def watch_pod_events():
    v1 = get_core_api()
    while app.is_running():
        async with watch.Watch().stream(v1.list_namespaced_pod,
                                        namespace=settings.NAMESPACE,
                                        label_selector=f"cykubed_job in (runner,builder)",
                                        timeout_seconds=10) as stream:
            while app.is_running():
                async for event in stream:
                    await handle_pod_event(event['object'])


async def watch_job_events():
    api = get_batch_api()
    r = async_redis()
    while app.is_running():
        async with watch.Watch().stream(api.list_namespaced_job,
                                        namespace=settings.NAMESPACE,
                                        label_selector=f"cykubed_job=runner",
                                        timeout_seconds=10) as stream:
            while app.is_running():
                async for event in stream:
                    job: V1Job = event['object']
                    status: V1JobStatus = job.status
                    metadata: V1ObjectMeta = job.metadata
                    labels = metadata.labels
                    trid = labels["testrun_id"]
                    if not status.active:
                        st = await get_build_state(trid)
                        if st.run_job and st.run_job == metadata.name and status.completion_time:
                            if utcnow() < st.runner_deadline:
                                # runner job completed under the deadline: check for specs remaining
                                specs = await r.smembers(f'testrun:{st.trid}:specs')
                                if specs:
                                    # yup - recreate the job
                                    await recreate_runner_job(st, specs)


async def handle_pod_event(pod: V1Pod):
    """
    Update the duration for a finished pod
    :param pod:
    :return:
    """

    status: V1PodStatus = pod.status
    metadata: V1ObjectMeta = pod.metadata
    if status.phase in ['Succeeded', 'Failed']:
        # assume finished
        testrun_id = metadata.labels['testrun_id']
        annotations = metadata.annotations
        r = async_redis()
        if await r.sadd(f'testrun:{testrun_id}:completed_pods', metadata.name) == 1:
            # send the duration if we haven't already
            st = schemas.PodDuration(job_type=metadata.labels['cykubed_job'],
                                     is_spot=check_is_spot(annotations),
                                     duration=int((utcnow() - status.start_time).seconds))
            await app.httpclient.post(f'/agent/testrun/{testrun_id}/pod-duration',
                                      content=st.json())
