import os
from asyncio import sleep

import aiofiles
import chevron
import yaml
from chevron import ChevronError
from kubernetes import client, utils as k8utils
from kubernetes.client import ApiException
from loguru import logger
from yaml import YAMLError

from common import schemas
from common.exceptions import InvalidTemplateException
from common.k8common import NAMESPACE, get_core_api
from common.schemas import NewTestRun
from common.settings import settings

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), 'k8config', 'templates')


def get_template_path(name: str) -> str:
    return os.path.join(TEMPLATES_DIR, f'{name}.mustache')


async def get_job_template(name: str) -> str:
    async with aiofiles.open(get_template_path(name), mode='r') as f:
        return await f.read()


async def create_job(jobtype: str,
                     testrun: schemas.NewTestRun,
                     platform: str,
                     msg: schemas.AgentCompletedBuildMessage = None):
    """
    Render the Kubernetes Job template into Python objects, ready for feeding into "create_from_yaml"

    :param jobtype: job type (builder or runner)
    :param platform: K8 platform
    :param testrun: test run
    :param msg: [Optional] AgentCompletedBuildMessage for runners
    :return:
    """
    name = f"cykube-{jobtype}-{testrun.project.name}-{testrun.local_id}"
    context = dict(name=name,
                   project_id=testrun.project.id,
                   local_id=testrun.local_id,
                   testrun_id=testrun.id,
                   branch=testrun.branch,
                   runner_image=testrun.project.runner_image,
                   cypress_retries=testrun.project.cypress_retries,
                   timezone=testrun.project.timezone,
                   token=settings.API_TOKEN)

    if platform == 'GKE':
        context['storage_class'] = 'premium-rwo'
    else:
        context['storage_class'] = 'standard'

    if jobtype == 'builder':
        template = await get_job_template('builder')
        context.update(dict(parallelism=testrun.project.parallelism,
                            cpu_request=testrun.project.build_cpu,
                            cpu_limit=testrun.project.build_cpu,
                            deadline=testrun.project.build_deadline,
                            storage=testrun.project.build_ephemeral_storage,
                            memory_request=testrun.project.build_memory,
                            name=name,
                            memory_limit=testrun.project.build_memory))
    else:
        template = await get_job_template('runner')
        parallelism = testrun.project.parallelism
        if msg:
            parallelism = min(parallelism, len(msg.specs))
        context.update(dict(cpu_request=testrun.project.runner_cpu,
                            cpu_limit=testrun.project.runner_cpu,
                            deadline=testrun.project.runner_deadline,
                            gke_spot_enabled=testrun.project.spot_enabled,
                            gke_spot_percentage=testrun.project.spot_percentage,
                            memory_request=testrun.project.runner_memory,
                            memory_limit=testrun.project.runner_memory,
                            storage=testrun.project.runner_ephemeral_storage,
                            parallelism=parallelism))

    try:
        jobyaml = chevron.render(template, context)
        k8sclient = client.ApiClient()
        yamlobjects = yaml.safe_load(jobyaml)
        k8utils.create_from_yaml(k8sclient, yaml_objects=[yamlobjects], namespace=NAMESPACE)
        logger.info(f'Created job {name}', id=testrun.id)
    except YAMLError as ex:
        raise InvalidTemplateException(f'Invalid YAML in {jobtype} template: {ex}')
    except ChevronError as ex:
        raise InvalidTemplateException(f'Invalid {jobtype} template: {ex}')


# async def monitor_jobs():
#     """
#
#     """
#     sent = 0
#     logger.info("Start Job monitoring")
#     redis = async_redis()
#
#     while is_running():
#         api = client.BatchV1Api()
#         # get runner Jobs
#         jobs = api.list_namespaced_job(NAMESPACE, label_selector=f'cykube-job=runner')
#
#
#         await asyncio.sleep(15)


def delete_job(job, trid: int = None):
    logger.info(f"Deleting existing job {job.metadata.name}", trid=trid)
    client.BatchV1Api().delete_namespaced_job(job.metadata.name, NAMESPACE)
    poditems = get_core_api().list_namespaced_pod(NAMESPACE,
                                                  label_selector=f"job-name={job.metadata.name}").items
    if poditems:
        for pod in poditems:
            logger.info(f'Deleting pod {pod.metadata.name}', id=trid)
            get_core_api().delete_namespaced_pod(pod.metadata.name, NAMESPACE)


def delete_jobs_for_branch(trid: int, branch: str):
    # delete any job already running
    api = client.BatchV1Api()
    jobs = api.list_namespaced_job(NAMESPACE, label_selector=f'branch={branch}')
    if jobs.items:
        logger.info(f'Found {len(jobs.items)} existing Jobs - deleting them', trid=trid)
        # delete it (there should just be one, but iterate anyway)
        for job in jobs.items:
            delete_job(job, trid)


def delete_jobs_for_project(project_id):
    api = client.BatchV1Api()
    jobs = api.list_namespaced_job(NAMESPACE, label_selector=f'project_id={project_id}')
    if jobs.items:
        logger.info(f'Found {len(jobs.items)} existing Jobs - deleting them')
        for job in jobs.items:
            delete_job(job)


def delete_jobs(testrun_id: int):
    logger.info(f"Deleting jobs for testrun {testrun_id}")
    api = client.BatchV1Api()
    jobs = api.list_namespaced_job(NAMESPACE, label_selector=f"testrun_id={testrun_id}")
    for job in jobs.items:
        delete_job(job, testrun_id)


async def create_build_job(platform: str, newrun: schemas.NewTestRun):
    if settings.K8:
        # stop existing jobs
        delete_jobs_for_branch(newrun.id, newrun.branch)
        # and create a new one
        await create_job('builder', newrun, platform)

        if newrun.project.start_runners_first:
            await sleep(10)
            await create_runner_jobs(newrun, platform)
    else:
        logger.info(f"Now run cykuberunner with options 'build {newrun.id}'**",
                    tr=newrun)


async def create_runner_jobs(testrun: NewTestRun,
                             platform: str,
                             msg: schemas.AgentCompletedBuildMessage = None):
    if settings.K8:
        try:
            await create_job('runner', testrun, platform, msg)
        except Exception:
            logger.exception(f"Failed to create runner job for testrun {testrun.id}")
    else:
        logger.info(f"Now run cykuberunner with options 'run {testrun.id}'",
                    tr=testrun)


def is_pod_running(podname: str):
    v1 = client.CoreV1Api()
    try:
        v1.read_namespaced_pod(podname, NAMESPACE)
        return True
    except ApiException:
        return False
