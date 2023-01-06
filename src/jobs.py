import datetime
import os.path

from kubernetes import client
from loguru import logger

from common import schemas
from common.k8common import NAMESPACE, get_batch_api, create_jobs, init
from settings import settings

TEMPLATE_FILES = open(os.path.join(os.path.dirname(__file__), 'k8config/build-job.yaml')).read().split('---')


def delete_jobs_for_branch(trid: int, branch: str):

    # delete any job already running
    api = get_batch_api()
    jobs = api.list_namespaced_job(NAMESPACE, label_selector=f'branch={branch}')
    if jobs.items:
        logger.info(f'Found {len(jobs.items)} existing Jobs - deleting them', trid=trid)
        # delete it (there should just be one, but iterate anyway)
        for job in jobs.items:
            logger.info(f"Deleting existing job {job.metadata.name}", trid=trid)
            api.delete_namespaced_job(job.metadata.name, NAMESPACE)


def cleanup_jobs(ttl=settings.JOB_TTL):
    """
    Delete terminated jobs and volumes
    """
    # delete any job already running
    batchapi = client.BatchV1Api()
    api = client.CoreV1Api()
    now = datetime.datetime.now()
    jobs = batchapi.list_namespaced_job(NAMESPACE, label_selector=f'type=cykube-runner')
    for job in jobs.items:
        if (now - job.status.completion_time).seconds > ttl:
            if job.status.active:
                # kill the job
                batchapi.delete_namespaced_job(job.metadata.name, NAMESPACE)

            # delete PVCs if they exist
            for volume in job.spec.template.spec.volumes:
                if volume.persistent_volume_claim:
                    api.delete_namespaced_persistent_volume_claim(volume.persistent_volume_claim.claim_name, NAMESPACE)


def create_build_job(testrun: schemas.NewTestRun):
    logger.info("Creating build job", trid=testrun.id)
    create_jobs(TEMPLATE_FILES, testrun)
    # TODO (maybe) track job progress?


if __name__ == "__main__":
    init()
    cleanup_jobs(0)
