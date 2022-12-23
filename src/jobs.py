import base64
import logging
import os

from kubernetes import client, config

from common import schemas
from settings import settings

batchapi = None

NAMESPACE = 'cykube'


def get_batch_api() -> client.BatchV1Api:
    global batchapi
    if os.path.exists('/var/run/secrets/kubernetes.io'):
        # we're inside a cluster
        config.load_incluster_config()
        batchapi = client.BatchV1Api()
    else:
        # we're not
        config.load_kube_config()
        batchapi = client.BatchV1Api()
    return batchapi


def delete_jobs_for_branch(branch: str, logfile=None):
    if logfile:
        logfile.write(f'Look for existing jobs for branch {branch}\n')

    # delete any job already running
    api = get_batch_api()
    jobs = api.list_namespaced_job(NAMESPACE, label_selector=f'branch={branch}')
    if jobs.items:
        if logfile:
            logfile.write(f'Found {len(jobs.items)} existing Jobs - deleting them\n')
        # delete it (there should just be one, but iterate anyway)
        for job in jobs.items:
            logging.info(f"Deleting existing job {job.metadata.name}")
            api.delete_namespaced_job(job.metadata.name, NAMESPACE)


def get_job_env():
    return [client.V1EnvVar(name='API_TOKEN', value=settings.API_TOKEN)]


def create_build_job(testrun: schemas.NewTestRun):
    """
    Create a Job to clone and build the app
    :param testrun:
    :return:
    """
    job_name = f'cykube-run-{testrun.id}'
    container = client.V1Container(
        image=testrun.project.agent_image,
        name='cykube-builder',
        image_pull_policy='IfNotPresent',
        env=get_job_env(),
        resources=client.V1ResourceRequirements(
            limits={"cpu": testrun.project.build_cpu,
                    "memory": testrun.project.build_memory}
        ),
        command=["./clone.py", base64.b64encode(testrun.json())],
    )
    pod_template = client.V1PodTemplateSpec(
        spec=client.V1PodSpec(restart_policy="Never",
                              containers=[container]),
        metadata=client.V1ObjectMeta(name='cykube-builder')
    )
    metadata = client.V1ObjectMeta(name=job_name,
                                   labels={"cykube-job": "builder",
                                           "branch": testrun.branch})

    job = client.V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=metadata,
        spec=client.V1JobSpec(backoff_limit=0, template=pod_template,
                              ttl_seconds_after_finished=3600),
    )
    get_batch_api().create_namespaced_job(NAMESPACE, job)


def create_runner_jobs(testrun: schemas.NewTestRun):
    """
    Create runner jobs
    :param testrun:
    :return:
    """
    job_name = f'cykube-run-{testrun.id}'
    container = client.V1Container(
        image=testrun.project.runner_image,
        name='cykube-runner',
        image_pull_policy='IfNotPresent',
        env=get_job_env(),
        resources=client.V1ResourceRequirements(
            limits={"cpu": testrun.project.runner_cpu,
                    "memory": testrun.project.runner_memory}
        ),
        command=[str(testrun.id)],
    )
    pod_template = client.V1PodTemplateSpec(
        spec=client.V1PodSpec(restart_policy="Never",
                              containers=[container]),
        metadata=client.V1ObjectMeta(name='cykube-runner')
    )
    metadata = client.V1ObjectMeta(name=job_name,
                                   labels={"cykube-job": "runner",
                                           "branch": testrun.branch})

    job = client.V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=metadata,
        spec=client.V1JobSpec(backoff_limit=0, template=pod_template,
                              parallelism=testrun.project.parallelism,
                              ttl_seconds_after_finished=3600),
    )
    get_batch_api().create_namespaced_job(NAMESPACE, job)
