import os

import click
import requests
from settings import settings


@click.command()
@click.option('--repos', default='kisanhubcore/kisanhub-webapp')
@click.option('--branch', default='master')
@click.option('--url', default=settings.HUB_URL)
@click.argument('sha')
def build(sha, repos, branch, url):
    token = os.environ['AUTH0_TOKEN']
    headers = {'authorization': f'Bearer {token}'}
    print(headers)
    r = requests.post(f'{url}/api/start', headers=headers, json=dict(repos=repos, sha=sha, branch=branch))
    if r.status_code != 200:
        print(f"Failed: {r.text}")
    else:
        print("Build started")


if __name__ == '__main__':
    build()
