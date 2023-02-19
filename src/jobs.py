import asyncio
import json
from functools import lru_cache

import httpx
from kubernetes import client
from loguru import logger

import messages
from common import schemas, k8common
from common.k8common import NAMESPACE, get_job_env, get_batch_api, get_events_api, get_core_api
from common.schemas import TestRunJobStatus
from common.settings import settings
from common.utils import get_headers


# class JobEvent(BaseModel):
#     ts: datetime
#     reason: str
#     note: str
#
#
# class JobStatus(BaseModel):
#     name: str
#     jobtype: str
#     active: bool = False
#     failed: int = 0
#     succeeded: int = 0
#     project_id: int
#     local_id: int
#     events: list[JobEvent] = []
#
#
# active_jobs: list[JobStatus] = []


def delete_jobs_for_branch(trid: int, branch: str):

    # delete any job already running
    api = client.BatchV1Api()
    jobs = api.list_namespaced_job(NAMESPACE, label_selector=f'branch={branch}')
    if jobs.items:
        logger.info(f'Found {len(jobs.items)} existing Jobs - deleting them', trid=trid)
        # delete it (there should just be one, but iterate anyway)
        for job in jobs.items:
            logger.info(f"Deleting existing job {job.metadata.name}", trid=trid)
            api.delete_namespaced_job(job.metadata.name, NAMESPACE)


def delete_jobs(project_id, local_id):
    api = client.BatchV1Api()
    jobs = api.list_namespaced_job(NAMESPACE, label_selector=f"project_id={project_id},local_id={local_id}")
    for job in jobs.items:
        logger.info(f'Deleting job {job.metadata.name}', project_id=project_id, local_id=local_id)
        api.delete_namespaced_job(job.metadata.name, NAMESPACE)


def create_build_job(testrun: schemas.NewTestRun):
    """
    Create a Job to clone and build the app
    :param testrun:
    :return:
    """
    job_name = f'cykube-build-{testrun.project.name}-{testrun.local_id}'
    container = client.V1Container(
        image=testrun.project.runner_image,
        name='cykube-builder',
        image_pull_policy='IfNotPresent',
        env=get_job_env(),
        resources=client.V1ResourceRequirements(
            requests={"cpu": testrun.project.build_cpu,
                      "memory": testrun.project.build_memory,
                      "ephemeral-storage": "2Gi"},
            limits={"cpu": testrun.project.build_cpu,
                    "memory": testrun.project.build_memory,
                    "ephemeral-storage": "4Gi"}
        ),
        args=["build", str(testrun.id)],
    )
    pod_template = client.V1PodTemplateSpec(
        spec=client.V1PodSpec(restart_policy="Never",
                              containers=[container]),
        metadata=client.V1ObjectMeta(name='cykube-builder',
                                     labels={"job-name": job_name})
    )
    metadata = client.V1ObjectMeta(name=job_name,
                                   labels={"cykube-job": "builder",
                                           "project_id": str(testrun.project.id),
                                           "local_id": str(testrun.local_id),
                                           "branch": testrun.branch})
    jobcfg = client.V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=metadata,
        spec=client.V1JobSpec(backoff_limit=0, template=pod_template,
                              active_deadline_seconds=testrun.project.build_deadline or
                                                      settings.DEFAULT_BUILD_JOB_DEADLINE,
                              ttl_seconds_after_finished=settings.JOB_TTL),
    )
    logger.info(f"Creating build job {job_name}", tr=testrun)
    get_batch_api().create_namespaced_job(NAMESPACE, jobcfg)


def create_runner_jobs(build: schemas.CompletedBuild):
    """
    Create runner jobs
    :return:
    """
    # remove build job first
    # delete_jobs(build.testrun.id)

    # now create run jobs
    testrun = build.testrun
    job_name = f'cykube-run-{testrun.project.name}-{testrun.local_id}'

    container = client.V1Container(
        image=testrun.project.runner_image,
        name='cykube-runner',
        image_pull_policy='IfNotPresent',
        env=get_job_env(),
        resources=client.V1ResourceRequirements(
            requests={"cpu": testrun.project.runner_cpu,
                      "memory": testrun.project.runner_memory,
                      "ephemeral-storage": "2Gi"},
            limits={"cpu": testrun.project.runner_cpu,
                    "memory": testrun.project.runner_memory,
                    "ephemeral-storage": "4Gi"}
        ),
        args=['run', str(testrun.project.id), str(testrun.local_id), build.cache_hash],
    )
    pod_template = client.V1PodTemplateSpec(
        spec=client.V1PodSpec(restart_policy="Never",
                              containers=[container]),
        metadata=client.V1ObjectMeta(name='cykube-runner')
    )
    metadata = client.V1ObjectMeta(name=job_name,
                                   labels={"cykube-job": "runner",
                                           "project_id": str(testrun.project.id),
                                           "local_id": str(testrun.local_id),
                                           "branch": testrun.branch})
    jobcfg = client.V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=metadata,
        spec=client.V1JobSpec(backoff_limit=0, template=pod_template,
                              active_deadline_seconds=testrun.project.runner_deadline or
                                                      settings.DEFAULT_RUNNER_JOB_DEADLINE,
                              parallelism=min(len(testrun.files), testrun.project.parallelism),
                              ttl_seconds_after_finished=settings.JOB_TTL),
    )
    get_batch_api().create_namespaced_job(NAMESPACE, jobcfg)
    logger.info(f'Creating running job {job_name}', tr=testrun)


@lru_cache(maxsize=1000)
def post_job_status(project_id: int, local_id: int, name: str, status: str, message: str = None):
    r = httpx.post(f'{settings.MAIN_API_URL}/agent/testrun/{project_id}/{local_id}/job/status',
                   json=TestRunJobStatus(name=name,
                                         status=status,
                                         message=message).dict(), headers=get_headers())
    if r.status_code != 200:
        logger.error(f"Failed to update cykube about job status: {r.status_code}: {r.text}")

#
# async def get_job_info(job_int: JobStatus):
#     project_id, local_id = job_int.project_id, job_int.local_id
#     job_name = job_int.name
#     jobitems = get_batch_api().list_namespaced_job('cykube',
#                                                    field_selector=f"metadata.name={job_name}").items
#     failed = False
#
#     if not jobitems:
#         return True
#
#     if jobitems[0].status.failed:
#         post_job_status(project_id, local_id, jo)
#         logger.error(f"Job {job_name} has failed", project_id=project_id, local_id=local_id)
#         failed = True
#
#     poditems = get_core_api().list_namespaced_pod('cykube', label_selector=f'job-name={job_name}').items
#     if poditems:
#         pod = poditems[0]
#         phase = pod.status.phase.lower()
#         if phase == 'running':
#             logger.info(f'Pod {pod.metadata.name} is running',
#                         project_id=project_id, local_id=local_id)
#         elif phase == 'failed':
#             # check for the reason
#             terminated = pod.status.container_statuses[0].state.terminated
#             if terminated and terminated.reason == 'OOMKilled':
#                 logger.error(f"Build job run out of memory - try increasing the memory limit",
#                              project_id=project_id, local_id=local_id)
#             failed = True
#
#     # there's a bug in the deserialiser that can break if there is no event time
#     # so just get the raw JSON
#     try:
#         events = json.loads(get_events_api().list_namespaced_event(
#             NAMESPACE,
#             field_selector=f"regarding.kind=Job,regarding.name={job_int.name}",
#             _preload_content=False).data.decode('utf8'))['items']
#         if len(events) > len(job_int.events):
#             for ev in events[len(job_int.events):]:
#                 newev = JobEvent(ts=parse(ev['metadata']['creationTimestamp'], ignoretz=True),
#                                  reason=ev['reason'],
#                                  note=ev['note'])
#                 job_int.events.append(newev)
#                 # always log "BackoffLimitExceeded"
#                 logger.info(f"Job event {newev.reason} ({newev.note}) for test run {local_id} "
#                             f"in project {project_id}")
#                 if newev.reason in ['DeadlineExceeded', 'BackoffLimitExceeded']:
#                     logger.error(f"Failed to create Job: {newev.note}",
#                                  project_id=project_id, local_id=local_id)
#                     update_status(project_id, local_id, 'timeout')
#                     failed = True
#
#     except ApiException:
#         logger.exception("Failed to contact cluster to fetch Job status")
#
#     return failed


async def fetch_job_statuses():
    jobitems = get_batch_api().list_namespaced_job(NAMESPACE,
                                                   label_selector=f"cykube-job in (builder,runner)").items
    if not jobitems:
        return

    for job in jobitems:

        name = job.metadata.name
        project_id = int(job.metadata.labels['project_id'])
        local_id = int(job.metadata.labels['local_id'])
        jobtype = job.metadata.labels['cykube-job']
        logged_fail = False

        poditems = get_core_api().list_namespaced_pod('cykube', label_selector=f'job-name={name}').items
        if poditems:
            # there'll only be a single pod
            pod = poditems[0]
            phase = pod.status.phase.lower()
            if phase == 'running':
                logger.info(f'Pod {pod.metadata.name} is running',
                            project_id=project_id, local_id=local_id)
            elif phase == 'failed':
                # check for the reason
                terminated = pod.status.container_statuses[0].state.terminated
                if terminated and terminated.reason == 'OOMKilled':
                    post_job_status(project_id, local_id, name, f'{jobtype.capitalize()} job failed',
                                    'Job ran out of memory - try increasing the memory limit')
                    logged_fail = True

        if job.status.failed:
            if not logged_fail:
                post_job_status(project_id, local_id, name, f'{jobtype.capitalize()} job failed')
        elif not job.status.succeeded:
            # track the creation
            for event in json.loads(get_events_api().list_namespaced_event(
                NAMESPACE,
                field_selector=f"regarding.kind=Job,regarding.name={name}",
                        _preload_content=False).data.decode('utf8'))['items']:

                message = None
                if event.reason == 'DeadlineExceeded':
                    message = 'Job exceeded deadline'
                elif event.reason == 'BackoffLimitExceeded':
                    message = 'Backoff limit exceeded - job is probably crashing'

                if message:
                    post_job_status(project_id, local_id, name, f'{jobtype.capitalize()} job failed',
                                    message)
                    logged_fail = True

        if logged_fail:
            messages.update_status(project_id, local_id, 'failed')


async def job_status_poll():

    while status.is_running():
        try:
            await asyncio.sleep(1)
            await fetch_job_statuses()
        except:
            logger.exception("Failed to fetch Job statues")
        await asyncio.sleep(settings.JOB_STATUS_POLL_PERIOD)


if __name__ == "__main__":
    k8common.init()
    fetch_job_statuses()
    # asyncio.run(job_status_poll())
