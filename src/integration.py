import logging
import re
from datetime import timedelta
from email.utils import parseaddr

import requests
from fastapi_utils.session import FastAPISessionMaker

import crud
from models import TestRun, PlatformEnum
from settings import settings
from utils import now

JIRA_HEADERS = {'Content-Type': 'application/json',
                'Accept': 'application/json; charset=utf8'}

OAUTH_EXPIRY_BUFFER_MINUTES = 10

sessionmaker = FastAPISessionMaker(settings.CYPRESSHUB_DATABASE_URL)

class AuthException(Exception):
    pass


def jira_settings():
    with sessionmaker.context_session() as db:
        return crud.get_platform_settings(db, PlatformEnum.JIRA)


def jira_auth():
    s = jira_settings()
    if s:
        return s.username, s.token


def slack_headers():
    with sessionmaker.context_session() as db:
        s = crud.get_platform_settings(db, PlatformEnum.SLACK)
        if s:
            return {'Authorization': f'Bearer {s.token}',
                    'Content-Type': 'application/json; charset=utf8'}


def bitbucket_request(url, method='GET', **kwargs):
    with sessionmaker.context_session() as db:
        auth = crud.get_platform_oauth(db, PlatformEnum.BITBUCKET)
        if not auth:
            raise AuthException("Bitbucket auth failed")
        if auth.expiry < now() + timedelta(minutes=OAUTH_EXPIRY_BUFFER_MINUTES):
            # expires in the next 10 minutes (or already expired): refresh it
            resp = requests.post('https://bitbucket.org/site/oauth2/access_token',
                                 data={'refresh_token': auth.refresh_token,
                                       'grant_type': 'refresh_token'},
                                 auth=(settings.BITBUCKET_CLIENT_ID, settings.BITBUCKET_SECRET))
            if not resp.status_code == 200:
                raise AuthException("Failed to refresh Bitbucket token")
            ret = resp.json()
            expiry = now() + timedelta(minutes=ret['expires_in'])
            auth = crud.update_oauth_token(db,
                                           PlatformEnum.BITBUCKET,
                                           access_token=ret['access_token'],
                                           refresh_token=ret['refresh_token'],
                                           expiry=expiry)
        return requests.request(method, url, headers={'Authorization': f'Bearer {auth.access_token}'}, **kwargs)


def get_slack_user_id(email, headers=None):
    if not headers:
        headers = slack_headers()
    resp = requests.get("https://slack.com/api/users.lookupByEmail",
                        headers=headers, params={'email': email}).json()
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
    jira = jira_settings()
    resp = requests.get(f'{jira.url}/rest/api/3/user',
                        {'accountId': account_id},
                        auth=(jira.username, jira.token))
    if resp.status_code != 200:
        raise Exception(f"Failed to fetch information from JIRA: {resp.status_code}: {resp.json()}")
    return resp.json()


def get_jira_ticket_link(message, jira=None):
    if not jira:
        jira = jira_settings()

    for issue_key in set(re.findall(r'([A-Z]{2,5}-[0-9]{1,5})', message)):
        r = requests.get(f'{jira.url}/rest/api/3/issue/{issue_key}', auth=(jira.username, jira.token))
        if r.status_code == 200:
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
        slack = slack_headers()
        if slack:
            ret['author_slack_id'] = get_slack_user_id(email, slack)

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

