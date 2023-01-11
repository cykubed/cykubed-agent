from kubernetes import client
from loguru import logger

from common import schemas
from common.k8common import NAMESPACE, get_job_env, get_batch_api
from common.logupload import upload_log_line


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


def create_build_job(testrun: schemas.NewTestRun):
    """
    Create a Job to clone and build the app
    :param testrun:
    :return:
    """
    job_name = f'cykube-build-{testrun.id}'
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
    upload_log_line(testrun.id, "Creating build job")
    get_batch_api().create_namespaced_job(NAMESPACE, job)


def create_runner_jobs(build: schemas.CompletedBuild):
    """
    Create runner jobs
    :return:
    """
    testrun = build.testrun
    job_name = f'cykube-run-{testrun.id}'

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
        args=['run', str(testrun.id), build.cache_hash],
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
                              parallelism=min(len(testrun.files), testrun.project.parallelism),
                              ttl_seconds_after_finished=3600),
    )
    get_batch_api().create_namespaced_job(NAMESPACE, job)
