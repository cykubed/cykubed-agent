import json
import logging
import os
import shutil
import subprocess
import tempfile
from settings import settings

from kubernetes import client, config
from kubernetes.client import ApiException

from utils import runcmd

batchapi = None

RUNNER_CONFIG_DIR = os.path.join(os.path.dirname(__file__), 'k8config/cypress-runner')
CYPRESS_RUNNER_VERSION = os.environ.get('CYPRESS_RUNNER_VERSION', '1.0')
HUB_URL = os.environ.get('CYPRESSHUB_URL', 'http://cypresshub:5000')
DIST_URL = os.environ.get('CYPRESSHUB_URL', 'http://cypresshub:5001')


def connect_k8():

    global batchapi
    if os.path.exists('/var/run/secrets/kubernetes.io/serviceaccount/token'):
        # we're inside a cluster
        config.load_incluster_config()
        batchapi = client.BatchV1Api()
    else:
        config.load_kube_config()
        batchapi = client.BatchV1Api()


def get_latest_runner_tag():
    tags = set(json.loads(subprocess.check_output(
        "gcloud container images list-tags gcr.io/kisanhub-uat/cypress-runner --filter='tags=latest' --format=json",
        shell=True).decode())[0]['tags'])
    tags.remove('latest')
    return list(tags)[0]


def kube_delete_empty_pods(namespace='default', phase='Succeeded'):
    """
    Pods are never empty, just completed the lifecycle.
    As such they can be deleted.
    Pods can be without any running container in 2 states:
    Succeeded and Failed. This call doesn't terminate Failed pods by default.
    """
    # The always needed object
    deleteoptions = client.V1DeleteOptions()
    # We need the api entry point for pods
    api_pods = client.CoreV1Api()
    # List the pods
    for pod in api_pods.list_namespaced_pod(namespace, timeout_seconds=60).items:
        if pod.status.phase == phase:
            podname = pod.metadata.name
            api_pods.delete_namespaced_pod(podname, namespace)
            logging.info(f"Pod: {podname} deleted!")


def kube_cleanup_finished_jobs(namespace='default'):
    """
    Since the TTL flag (ttl_seconds_after_finished) is still in alpha (Kubernetes 1.12) jobs need to be cleanup manually
    As such this method checks for existing Finished Jobs and deletes them.
    """
    deleteoptions = client.V1DeleteOptions()
    jobs = batchapi.list_namespaced_job(namespace, timeout_seconds=60)

    # Now we have all the jobs, lets clean up
    # We are also logging the jobs we didn't clean up because they either failed or are still running
    for job in jobs.items:
        jobname = job.metadata.name
        jobstatus = job.status.conditions
        if job.status.succeeded == 1:
            # Clean up Job
            logging.info("Cleaning up Job: {}. Finished at: {}".format(jobname, job.status.completion_time))
            try:
                # What is at work here. Setting Grace Period to 0 means delete ASAP. Otherwise it defaults to
                # some value I can't find anywhere. Propagation policy makes the Garbage cleaning Async
                api_response = batchapi.delete_namespaced_job(jobname,
                                                              namespace,
                                                              deleteoptions,
                                                              grace_period_seconds=0,
                                                              propagation_policy='Background')
                logging.debug(api_response)
            except ApiException as e:
                print("Exception when calling BatchV1Api->delete_namespaced_job: %s\n" % e)
        else:
            if jobstatus is None and job.status.active == 1:
                jobstatus = 'active'
            logging.info("Job: {} not cleaned up. Current status: {}".format(jobname, jobstatus))

    # Now that we have the jobs cleaned, let's clean the pods
    kube_delete_empty_pods(namespace)


def prune_jobs(namespace):
    kube_cleanup_finished_jobs(namespace)
    kube_delete_empty_pods(namespace)


def start_job(branch, commit_sha, logfile):
    """
    Start a cypress-runner Job
    """
    namespace = os.environ.get('NAMESPACE', 'default')

    # delete any job already running
    jobs = batchapi.list_namespaced_job(namespace, label_selector=f'job=cypress-runner,branch={branch}')
    if jobs.items:
        # delete it (there should just be one, but iterate anyway)
        for job in jobs.items:
            logging.info(f"Deleting existing job {job.metadata.name}")
            runcmd(f'kubectl delete job/{job.metadata.name}', logfile=logfile)
            # the following doesn't appear to actually delete the job?
            # batchapi.delete_namespaced_job(job.metadata.name, namespace)

    # copy the k8 config
    with open(os.path.join(RUNNER_CONFIG_DIR, 'runner.yaml')) as f:
        cfg = f.read().format(SHA=commit_sha, PARALLELISM=settings.PARALLELISM,
                              BRANCH=branch, HUB_URL=settings.HUB_URL,
                              DIST_URL=settings.DIST_URL,
                              CYPRESS_RUNNER_VERSION=settings.CYPRESS_RUNNER_VERSION)
    k8cfg = tempfile.NamedTemporaryFile('w', suffix='.yaml')
    k8cfg.write(cfg)
    runcmd(f'kubectl apply -f {k8cfg}', logfile=logfile)
    k8cfg.close()


if __name__ == '__main__':
    connect_k8()
    start_job('master', '724b47ce1a25fe5393fb16a2e4f62a375e08dcec')
