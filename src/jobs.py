import os

import aiofiles
import chevron
import yaml
from chevron import ChevronError
from kubernetes import client, utils as k8utils
from kubernetes.client import ApiException
from loguru import logger
from yaml import YAMLError

from app import app
from common import schemas
from common.exceptions import InvalidTemplateException
from common.k8common import NAMESPACE, get_core_api
from db import get_testrun, get_cached_item, save_testrun, add_build_cache_item, add_node_cache_item
from settings import settings

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), 'k8config', 'templates')


def get_template_path(name: str) -> str:
    return os.path.join(TEMPLATES_DIR, f'{name}.mustache')


async def get_job_template(name: str) -> str:
    async with aiofiles.open(get_template_path(name), mode='r') as f:
        return await f.read()


def common_context(jobtype: str, testrun: schemas.NewTestRun):
    name = f"cykubed-{jobtype}-{testrun.project.name}-{testrun.local_id}"
    return dict(name=name,
                jobtype=jobtype,
                sha=testrun.sha,
                namespace=settings.NAMESPACE,
                storage_class=settings.STORAGE_CLASS,
                project_id=testrun.project.id,
                local_id=testrun.local_id,
                testrun_id=testrun.id,
                testrun=testrun,
                branch=testrun.branch,
                runner_image=testrun.project.runner_image,
                timezone=testrun.project.timezone,
                token=settings.API_TOKEN,
                cpu_request=testrun.project.runner_cpu,
                cpu_limit=testrun.project.runner_cpu,
                gke_spot_enabled=testrun.project.spot_enabled,
                gke_spot_percentage=testrun.project.spot_percentage,
                deadline=testrun.project.runner_deadline,
                memory_request=testrun.project.runner_memory,
                memory_limit=testrun.project.runner_memory,
                storage=testrun.project.runner_ephemeral_storage)


async def create_job(context):
    jobtype = context['jobtype']
    try:
        template = await get_job_template(jobtype)
        jobyaml = chevron.render(template, context)
        k8sclient = client.ApiClient()
        yamlobjects = list(yaml.safe_load_all(jobyaml))
        k8utils.create_from_yaml(k8sclient, yaml_objects=yamlobjects, namespace=NAMESPACE)
        logger.info(f'Created job {context["name"]}', id=context['testrun_id'])
    except YAMLError as ex:
        raise InvalidTemplateException(f'Invalid YAML in {jobtype} template: {ex}')
    except ChevronError as ex:
        raise InvalidTemplateException(f'Invalid {jobtype} template: {ex}')


async def create_clone_job(testrun: schemas.AgentTestRun):
    # stop existing jobs
    delete_jobs_for_branch(testrun.id, testrun.branch)

    # check for an existing build for this sha
    if await get_cached_item(testrun.sha):
        # yup - go straight to runner
        await app.update_status(testrun.id, 'running')
        await create_runner_job(testrun)
    else:
        # nope - clone it
        context = common_context('clone', testrun)
        await create_job(context)


async def create_build_job(testrun_id: int):
    testrun = await get_testrun(testrun_id)
    context = common_context('build', testrun)
    # check for node dist
    cached_node_dist = await get_cached_item(f'node-{testrun.cache_key}')
    if cached_node_dist:
        context['node_snapshot_name'] = cached_node_dist.name
        testrun.node_cache_hit = True
        await save_testrun(testrun)

    context.update(dict(node_cache_key=testrun.cache_key))
    await create_job(context)


async def build_completed(testrun_id: int):
    testrun = await get_testrun(testrun_id)
    if not testrun.node_cache_hit:
        # take a snapshot of the node dist
        context = common_context('node-snapshot', testrun)
        context['node_cache_key'] = f'node-{testrun.cache_key}'
        await create_job(context)
        await add_node_cache_item(testrun)

    # next create the runner job
    await create_runner_job(testrun)


async def create_runner_job(testrun: schemas.AgentTestRun):
    context = common_context('runner', testrun)
    parallelism = min(testrun.project.parallelism, len(testrun.specs))
    context.update(dict(parallelism=parallelism))
    await create_job(context)
    await add_build_cache_item(testrun)
    await app.httpclient.post(f'/agent/testrun/{testrun.id}/status/running')
    # and delete the original build (we've already cloned it)
    get_core_api().delete_namespaced_persistent_volume_claim(f'build-{testrun.sha}-ro', settings.NAMESPACE)


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


def is_pod_running(podname: str):
    v1 = client.CoreV1Api()
    try:
        v1.read_namespaced_pod(podname, NAMESPACE)
        return True
    except ApiException:
        return False
