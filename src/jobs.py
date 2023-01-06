import os.path

from common import schemas
from common.k8common import NAMESPACE, get_batch_api, create_jobs
from common.logupload import upload_log_line

TEMPLATE_FILES = open(os.path.join(os.path.dirname(__file__), 'k8config/build-job.yaml')).read().split('---')


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
    upload_log_line(testrun.id, "Creating build job")
    create_jobs(TEMPLATE_FILES, testrun)
    # TODO (maybe) track job progress?
