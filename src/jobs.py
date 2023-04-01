from asyncio import sleep

import yaml
from kubernetes import client, utils
from kubernetes.client import ApiException
from loguru import logger

from common import schemas
from common.k8common import NAMESPACE, get_core_api
from common.schemas import NewTestRun
from common.settings import settings


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


def create_job(jobtype: str, testrun: schemas.NewTestRun):
    if jobtype == 'builder':
        template = testrun.project.builder_template
    else:
        template = testrun.project.runner_template

    k8sclient = client.ApiClient()
    yamlobjects = yaml.safe_load(template)
    utils.create_from_yaml(k8sclient, yaml_objects=[yamlobjects], namespace=NAMESPACE)
    logger.info(f"Created {jobtype} job", id=testrun.id)


async def create_build_job(newrun: schemas.NewTestRun):
    if settings.K8:
        # stop existing jobs
        delete_jobs_for_branch(newrun.id, newrun.branch)
        # and create a new one
        create_job('builder', newrun)

        if newrun.project.start_runners_first:
            await sleep(10)
            create_runner_jobs(newrun)
    else:
        logger.info(f"Now run cykuberunner with options 'build {newrun.id}'",
                    tr=newrun)


def create_runner_jobs(testrun: NewTestRun):
    if settings.K8:
        try:
            create_job('runner', testrun)
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
