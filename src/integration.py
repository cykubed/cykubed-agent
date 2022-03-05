import logging
import re
from datetime import timedelta
from email.utils import parseaddr
from typing import List

import requests
from fastapi_utils.session import FastAPISessionMaker

import crud
from models import TestRun, PlatformEnum
from schemas import OAuthDetailsModel
from settings import settings
from utils import now

JIRA_HEADERS = {'Content-Type': 'application/json',
                'Accept': 'application/json; charset=utf8'}

OAUTH_EXPIRY_BUFFER_MINUTES = 10

sessionmaker = FastAPISessionMaker(settings.CYPRESSHUB_DATABASE_URL)


class AuthException(Exception):
    pass


def fetch_settings_from_cykube():
    resp = requests.get(f'{settings.CYKUBE_MAIN_URL}/hub-settings',
                        headers={'Authorization': f'Token {settings.API_TOKEN}'})
    if resp.status_code != 200:
        logging.error("Failed to contact cypresskube API server - cannot fetch settings")
    else:
        with sessionmaker.context_session() as db:
            for platsettings in resp.json():
                crud.update_oauth(db, platsettings['platform'],
                                  OAuthDetailsModel(**platsettings))


def bitbucket_request(url, method='GET', **kwargs):
    with sessionmaker.context_session() as db:
        auth = crud.get_oauth(db, PlatformEnum.BITBUCKET)
        if not auth:
            raise AuthException("Bitbucket auth failed")

        if auth.expiry < now() + timedelta(minutes=OAUTH_EXPIRY_BUFFER_MINUTES):
            # expires in the next 10 minutes (or already expired): refresh it
            resp = requests.post('https://bitbucket.org/site/oauth2/access_token',
                                 data={'refresh_token': auth.refresh_token,
                                       'grant_type': 'refresh_token'},
                                 auth=(settings.BITBUCKET_CLIENT_ID, settings.BITBUCKET_SECRET))
            crud.update_standard_oauth_response(db, resp, PlatformEnum.BITBUCKET)
        return requests.request(method, url, headers={
            'Accept': 'application/json',
            'Authorization': f'Bearer {auth.access_token}'}, **kwargs)


def slack_request(path, method='GET', **kwargs):
    with sessionmaker.context_session() as db:
        auth = crud.get_oauth(db, PlatformEnum.SLACK)
        if not auth:
            raise AuthException("Slack auth failed")

        if auth.expiry < now() + timedelta(minutes=OAUTH_EXPIRY_BUFFER_MINUTES):
            # expires in the next 10 minutes (or already expired): refresh it
            resp = requests.post('https://slack.com/api/oauth.v2.exchange',
                                 data={'client_id': settings.SLACK_CLIENT_ID,
                                       'client_secret': settings.SLACK_SECRET,
                                       'token': auth.refresh_token})
            crud.update_standard_oauth_response(db, resp, PlatformEnum.SLACK)

        return requests.request(method, f'https://slack.com/api/{path}',
                                headers={
                                    'Accept': 'application/json',
                                    'Authorization': f'Bearer {auth.access_token}'}, **kwargs)


def jira_request(path, method='GET', **kwargs):
    with sessionmaker.context_session() as db:
        auth = crud.get_oauth(db, PlatformEnum.JIRA)
        if not auth:
            raise AuthException("Jira auth failed")

        if not auth.cloud_id:
            raise AuthException("Jira needs a cloud_id")

        if auth.expiry < now() + timedelta(minutes=OAUTH_EXPIRY_BUFFER_MINUTES):
            # expires in the next 10 minutes (or already expired): refresh it
            resp = requests.post('https://auth.atlassian.com/oauth/token',
                                 data={'client_id': settings.JIRA_CLIENT_ID,
                                       'client_secret': settings.JIRA_SECRET,
                                       'refresh_token': auth.refresh_token})
            crud.update_standard_oauth_response(db, resp, PlatformEnum.JIRA)

        url = f'https://api.atlassian.com/ex/jira/{auth.cloud_id}/{path}'
        return requests.request(method, url,
                                headers={
                                    'Accept': 'application/json',
                                    'Authorization': f'Bearer {auth.access_token}'}, **kwargs)


def get_slack_user_id(email):
    resp = slack_request('users.lookupByEmail', params={'email': email}).json()
    if not resp['ok']:
        logging.warning(f"Unknown slack user with {email} - returning email")
        return email
    return resp['user']['id']


def create_user_notification(userid):
    return f"<@{userid}>"


def get_bitbucket_commit_info(repos: str, sha: str):
    resp = bitbucket_request(f'https://api.bitbucket.org/2.0/repositories/{repos}/commit/{sha}')
    if resp.status_code != 200:
        raise Exception(f"Failed to fetch information from Bitbucket: {resp.status_code}: {resp.json()}")
    return resp.json()


def get_jira_user_details(account_id):

    resp = jira_request('/rest/api/3/user', params={'accountId': account_id})
    if resp.status_code != 200:
        raise Exception(f"Failed to fetch information from JIRA: {resp.status_code}: {resp.json()}")
    return resp.json()


def get_jira_ticket_link(message):
    for issue_key in set(re.findall(r'([A-Z]{2,5}-[0-9]{1,5})', message)):
        r = jira_request(f'/rest/api/3/issue/{issue_key}')
        if r.status_code == 200:
            #
            return f'{jira.url}/browse/{issue_key}'


def get_bitbucket_details(repos: str, branch: str, sha: str):

    jira = jira_settings()

    resp = bitbucket_request(f'https://api.bitbucket.org/2.0/repositories/{repos}/commit/{sha}')
    if resp.status_code != 200:
        raise Exception(f"Failed to fetch information from Bitbucket: {resp.status_code}: {resp.json()}")
    commit = resp.json()

    user = get_jira_user_details(commit['author']['user']['account_id'])
    ret = {
        'commit_summary': commit['summary']['raw'],
        'commit_link': commit['links']['html']['href'],
        'avatar': user['avatarUrls']['48x48'],
        'author': user['displayName']
    }
    # get the author name, so we can find a Slack handle
    name, email = parseaddr(commit['author']['raw'])
    if email:
        ret['author_slack_id'] = get_slack_user_id(email)

    # look for open PRs
    resp = bitbucket_request(f'https://api.bitbucket.org/2.0/repositories/{repos}/pullrequests',
                             params={'q': f'source.branch.name=\"{branch}\"', 'state': 'OPEN'})
    pr_title = None
    if resp.status_code == 200:
        prdata = resp.json()
        if prdata['size']:
            ret['pull_request_link'] = prdata['values'][0]['links']['html']['href']
            pr_title = prdata['values'][0]['title']

    ticket_link = get_jira_ticket_link(branch, jira)
    if not ticket_link and pr_title:
        # check PR title
        ticket_link = get_jira_ticket_link(pr_title, jira)

    if ticket_link:
        ret['jira_ticket'] = ticket_link

    return ret


def get_matching_repositories(platform: PlatformEnum, q: str) -> List[str]:
    if platform == PlatformEnum.BITBUCKET:
        resp = bitbucket_request('https://api.bitbucket.org/2.0/repositories/', params={
            'q': f'full_name~"{q}"',
            'fields': 'values.full_name',
            'role': 'member'
        })
        return [x['full_name'] for x in resp.json()['values']]


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

