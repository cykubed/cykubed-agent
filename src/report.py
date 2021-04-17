import json
import os
import shutil
import subprocess
from tempfile import mkdtemp

from google.cloud import storage


def create_report(sha, branch):
    """
    Create and upload a report
    TODO screenshots not currently handled
    :param sha:
    :return:
    """
    client = storage.Client()
    bucket = client.get_bucket('kisanhub-cypress-artifacts')
    results_dir = mkdtemp()
    report_dir = mkdtemp()

    screenshots = []
    try:
        for blob in bucket.list_blobs(prefix=f'results/{sha}/'):
            dest = os.path.join(results_dir, blob.name)
            os.makedirs(os.path.split(dest)[0], exist_ok=True)
            if 'screenshots' in blob.name:
                screenshots.append(dest)
            blob.download_to_filename(dest, checksum=None)
        # generate the report
        jsondir = os.path.join(results_dir, f'results/{sha}/json')
        sshotdir = os.path.join(results_dir, f'results/{sha}/screenshots')
        subprocess.check_call(f'npx generate-mochawesome-report '
                              f'--showPassed=false '
                              f'--reportTitle "Cypress results: commit {sha[:8]}, branch {branch}"'
                              f' --jsonDir={jsondir} --screenshotsDir={sshotdir} '
                              f'-o {report_dir} -i', shell=True)
        # and upload
        blob = bucket.blob(f'reports/{sha}/index.html')
        blob.upload_from_filename(f'{report_dir}/index.html')
        total_fails = 0
        specs_with_fails = []
        for f in os.listdir(jsondir):
            with open(os.path.join(jsondir, f)) as f:
                data = json.loads(f.read())
                # each json file is from a single spec, so we can just take the first entry of results
                fails = data['stats']['failures'] + data['stats']['skipped']
                total_fails += fails
                if fails > 0:
                    specs_with_fails.append(data['results'][0]['file'])

        for sshot in screenshots:
            destname = sshot[len(os.path.join(results_dir, 'results', sha)) + 1:]
            blob = bucket.blob(f'reports/{sha}/{destname}')
            blob.upload_from_filename(sshot)

        return total_fails, specs_with_fails

    finally:
        shutil.rmtree(results_dir, ignore_errors=True)
        shutil.rmtree(report_dir, ignore_errors=True)


def get_report_url(sha):
    return f'https://storage.googleapis.com/kisanhub-cypress-artifacts/reports/{sha}/index.html'


if __name__ == '__main__':
    create_report("890d9f78a63d592ced440db77a25bac3ddb70fd2", 'cb2e2')

