import json
import logging
import os
import shutil
import tempfile

import aiohttp
import requests

import schemas
from schemas import TestRun
from settings import settings
from utils import create_file_path

cykube_headers = {'Authorization': f'Bearer {settings.API_TOKEN}'}


async def post_logs(trid: int, log: str):
    async with aiohttp.ClientSession(headers=cykube_headers) as session:
        r = await session.post(f'{settings.CYKUBE_APP_URL}/hub/logs/{trid}', data=log)
        if r.status != 200:
            logging.error("Failed to contact cykube app")


async def notify_status(testrun: TestRun):
    async with aiohttp.ClientSession(headers=cykube_headers) as session:
        payload = schemas.TestRunUpdate(started=testrun.started,
                                        finished=testrun.finished,
                                        status=testrun.status)
        r = await session.put(f'{settings.CYKUBE_APP_URL}/hub/testrun/{testrun.id}', data=payload.json())
        if r.status != 200:
            logging.error("Failed to contact cykube app")


def notify_run_completed(testrun: TestRun):
    """
    Merge results, create a tar file of screenshots and notify cykube app
    Then clean up
    :param testrun:
    :return:
    """

    stats = merge_results(testrun)

    rootdir = os.path.join(settings.RESULTS_DIR, testrun.sha)
    # tar the results directory
    f = tempfile.NamedTemporaryFile(suffix='.tar')
    shutil.make_archive(f.name, 'tar',
                        root_dir=rootdir)

    requests.post(f'{settings.CYKUBE_APP_URL}/hub/results/{testrun.sha}',
                  data=stats.json(),
                  headers=cykube_headers,
                  files=[f])

    f.close()
    shutil.rmtree(rootdir)


def merge_results(testrun: TestRun) -> schemas.Results:
    """
    Merge the results and screenshots into a single directory
    """
    sha = testrun.sha
    results_root = os.path.join(settings.RESULTS_DIR, sha)
    json_root = os.path.join(results_root, 'json')

    results = schemas.Results(testrun=testrun, specs=[])

    for subd in os.listdir(json_root):
        with open(os.path.join(json_root, subd, 'result.json')) as f:
            report = json.loads(f.read())
            run = report['runs'][-1]
            spec_result = schemas.SpecResult(file=run['spec']['name'], results=[])
            results.specs.append(spec_result)
            for test in run['tests']:
                results.total += 1
                attempt = test['attempts'][-1]

                if test['state'] == 'pending':
                    results.skipped += 1
                    started_at = None
                    duration = None
                else:
                    started_at = test['attempts'][0]['startedAt']
                    duration = attempt['duration']
                test_result = schemas.TestResult(title=test['title'][1],
                                                 failed=(test['state'] == 'failed'),
                                                 body=test['body'],
                                                 display_error=test['displayError'],
                                                 duration=duration,
                                                 num_attempts=len(test['attempts']),
                                                 started_at=started_at)
                spec_result.results.append(test_result)

                if not test_result.failed:
                    results.passes += 1
                else:
                    results.failures += 1
                    err = attempt['error']
                    frame = err['codeFrame']
                    code_frame = schemas.CodeFrame(line=frame['line'],
                                                   file=frame['relativeFile'],
                                                   column=frame['column'],
                                                   frame=frame['frame'])

                    test_result.error = schemas.TestResultError(
                        name=err['name'],
                        message=err['message'],
                        stack=err['stack'],
                        screenshots=[create_file_path(sha, ss['path']) for ss in attempt.get('screenshots', [])],
                        videos=[create_file_path(sha, ss['path']) for ss in attempt.get('videos', [])],
                        code_frame=code_frame)
        if results.failures:
            testrun.status = schemas.Status.failed
        else:
            testrun.status = schemas.Status.passed

    with open(os.path.join(json_root, 'results.json'), 'w') as f:
        f.write(results.json(indent=4))

    return results
