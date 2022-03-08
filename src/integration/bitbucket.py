from datetime import timedelta

import requests

import crud
import integration.common
from models import PlatformEnum, TestRun, sessionmaker
from settings import settings
from utils import now


def set_bitbucket_build_status(tr: TestRun):
    # and tell BB that we're running a build
    state = None
    description = None
    if tr.status == 'building':
        state = 'INPROGRESS'
        description = 'Building'
    elif tr.status == 'running':
        state = 'INPROGRESS'
        description = 'Running'
    elif tr.status == 'failed':
        state = 'FAILED'
        description = 'Tests failed'
    elif tr.status == 'cancelled':
        state = 'FAILED'
        description = 'Cancelled'

    if not state or description:
        raise ValueError("Invalid state/description")
    bitbucket_request(f'https://api.bitbucket.org/2.0/repositories/{tr.repos}/commit/{tr.sha}/statuses/build/',
                      'POST',
                      data={'key': 'Cypress tests',
                            'state': state,
                            'description': description,
                            'url': f'{settings.HUB_URL}/results/{tr.id}'})


def get_bitbucket_commit_info(repos: str, sha: str):
    resp = bitbucket_request(f'https://api.bitbucket.org/2.0/repositories/{repos}/commit/{sha}')
    if resp.status_code != 200:
        raise Exception(f"Failed to fetch information from Bitbucket: {resp.status_code}: {resp.json()}")
    return resp.json()


def bitbucket_request(url, method='GET', **kwargs):
    with sessionmaker.context_session() as db:
        auth = crud.get_oauth(db, PlatformEnum.BITBUCKET)
        if not auth:
            raise Exception("Bitbucket auth failed")

        if auth.expiry < now() + timedelta(minutes=integration.common.OAUTH_EXPIRY_BUFFER_MINUTES):
            # expires in the next 10 minutes (or already expired): refresh it
            resp = requests.post('https://bitbucket.org/site/oauth2/access_token',
                                 data={'refresh_token': auth.refresh_token,
                                       'grant_type': 'refresh_token'},
                                 auth=(settings.BITBUCKET_CLIENT_ID, settings.BITBUCKET_SECRET))
            crud.update_standard_oauth_response(db, resp, PlatformEnum.BITBUCKET)
        return requests.request(method, url, headers={
            'Accept': 'application/json',
            'Authorization': f'Bearer {auth.access_token}'}, **kwargs)
