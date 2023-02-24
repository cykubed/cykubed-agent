import yaml
from kubernetes import client, utils
from kubernetes.client import ApiException
from loguru import logger

from common import schemas
from common.k8common import NAMESPACE
from common.schemas import NewTestRun
from common.settings import settings


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


def delete_jobs_for_project(project_id):
    api = client.BatchV1Api()
    jobs = api.list_namespaced_job(NAMESPACE, label_selector=f'project_id={project_id}')
    if jobs.items:
        logger.info(f'Found {len(jobs.items)} existing Jobs - deleting them')
        for job in jobs.items:
            api.delete_namespaced_job(job.metadata.name, NAMESPACE)


def delete_jobs(testrun_id: int):
    api = client.BatchV1Api()
    jobs = api.list_namespaced_job(NAMESPACE, label_selector=f"testrun_id={testrun_id}")
    for job in jobs.items:
        logger.info(f'Deleting job {job.metadata.name}', id=testrun_id)
        api.delete_namespaced_job(job.metadata.name, NAMESPACE)


def create_job(jobtype: str, testrun: schemas.NewTestRun, build: schemas.CompletedBuild = None):
    context = dict(project_name=testrun.project.name,
                   project_id=testrun.project.id,
                   local_id=testrun.local_id,
                   testrun_id=testrun.id,
                   branch=testrun.branch,
                   runner_image=testrun.project.runner_image,
                   token=settings.API_TOKEN)
    if jobtype == 'builder':
        template = testrun.project.builder_template
        context.update(dict(parallelism=testrun.project.parallelism,
                            cpu_request=testrun.project.build_cpu,
                            cpu_limit=testrun.project.build_cpu,
                            memory_request=testrun.project.build_memory,
                            memory_limit=testrun.project.build_memory))
    else:
        template = testrun.project.runner_template
        context.update(dict(cpu_request=testrun.project.runner_cpu,
                            cpu_limit=testrun.project.runner_cpu,
                            memory_request=testrun.project.runner_memory,
                            memory_limit=testrun.project.runner_memory,
                            parallelism=min(len(build.specs), testrun.project.parallelism)))

    jobyaml = template.format(**context)

    k8sclient = client.ApiClient()
    yamlobjects = yaml.safe_load(jobyaml)
    utils.create_from_yaml(k8sclient, yaml_objects=[yamlobjects], namespace=NAMESPACE)
    logger.info(f"Created {jobtype} job")


def create_build_job(testrun: schemas.NewTestRun):
    create_job('builder', testrun)


def create_runner_jobs(testrun: NewTestRun, build: schemas.CompletedBuild):
    create_job('runner', testrun, build)
#
# @lru_cache(maxsize=1000)
# def post_job_status(project_id: int, local_id: int, name: str, status: str, message: str = None):
#     r = httpx.post(f'{settings.MAIN_API_URL}/agent/testrun/{project_id}/{local_id}/job/status',
#                    json=TestRunJobStatus(name=name,
#                                          status=status,
#                                          message=message).dict(), headers=get_headers())
#     if r.status_code != 200:
#         logger.error(f"Failed to update cykube about job status: {r.status_code}: {r.text}")

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


def is_pod_running(podname: str):
    v1 = client.CoreV1Api()
    try:
        v1.read_namespaced_pod(podname, NAMESPACE)
        return True
    except ApiException:
        return False

# FIXME replace this will mongo
# async def fetch_job_statuses():
#     jobitems = get_batch_api().list_namespaced_job(NAMESPACE,
#                                                    label_selector=f"cykube-job in (builder,runner)").items
#     if not jobitems:
#         return
#
#     for job in jobitems:
#
#         name = job.metadata.name
#         project_id = int(job.metadata.labels['project_id'])
#         local_id = int(job.metadata.labels['local_id'])
#         jobtype = job.metadata.labels['cykube-job']
#         logged_fail = False
#
#         poditems = get_core_api().list_namespaced_pod('cykube', label_selector=f'job-name={name}').items
#         if poditems:
#             # there'll only be a single pod
#             pod = poditems[0]
#             phase = pod.status.phase.lower()
#             if phase == 'running':
#                 logger.info(f'Pod {pod.metadata.name} is running',
#                             project_id=project_id, local_id=local_id)
#             elif phase == 'failed':
#                 # check for the reason
#                 terminated = pod.status.container_statuses[0].state.terminated
#                 if terminated and terminated.reason == 'OOMKilled':
#                     post_job_status(project_id, local_id, name, f'{jobtype.capitalize()} job failed',
#                                     'Job ran out of memory - try increasing the memory limit')
#                     logged_fail = True
#
#         if job.status.failed:
#             if not logged_fail:
#                 post_job_status(project_id, local_id, name, f'{jobtype.capitalize()} job failed')
#         elif not job.status.succeeded:
#             # track the creation
#             for event in json.loads(get_events_api().list_namespaced_event(
#                 NAMESPACE,
#                 field_selector=f"regarding.kind=Job,regarding.name={name}",
#                         _preload_content=False).data.decode('utf8'))['items']:
#
#                 message = None
#                 if event.reason == 'DeadlineExceeded':
#                     message = 'Job exceeded deadline'
#                 elif event.reason == 'BackoffLimitExceeded':
#                     message = 'Backoff limit exceeded - job is probably crashing'
#
#                 if message:
#                     post_job_status(project_id, local_id, name, f'{jobtype.capitalize()} job failed',
#                                     message)
#                     logged_fail = True
#
#         if logged_fail:
#             messages.update_status(project_id, local_id, 'failed')
#
#
# async def job_status_poll():
#
#     while is_running():
#         try:
#             await asyncio.sleep(1)
#             await fetch_job_statuses()
#         except:
#             logger.exception("Failed to fetch Job statues")
#         await asyncio.sleep(settings.JOB_STATUS_POLL_PERIOD)
#
#
# if __name__ == "__main__":
#     k8common.init()
#     fetch_job_statuses()
# asyncio.run(job_status_poll())
