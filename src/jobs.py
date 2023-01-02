from kubernetes import client

from kubernetes import client

from common import schemas
from common.k8common import NAMESPACE, get_batch_api, get_job_env
from common.logupload import upload_log_line


def delete_jobs_for_branch(trid: int, branch: str):

    # delete any job already running
    api = get_batch_api()
    jobs = api.list_namespaced_job(NAMESPACE, label_selector=f'branch={branch}')
    if jobs.items:
        upload_log_line(trid, f'Found {len(jobs.items)} existing Jobs - deleting them\n')
        # delete it (there should just be one, but iterate anyway)
        for job in jobs.items:
            upload_log_line(trid, f"Deleting existing job {job.metadata.name}")
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
            limits={"cpu": testrun.project.build_cpu,
                    "memory": testrun.project.build_memory}
        ),
        command=["python", "./main.py", "build", str(testrun.id)],
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
