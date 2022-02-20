import logging
import re
from email.utils import parseaddr

import requests
from fastapi_utils.session import FastAPISessionMaker

import crud
from models import TestRun, PlatformEnum
from settings import settings

JIRA_HEADERS = {'Content-Type': 'application/json',
                'Accept': 'application/json; charset=utf8'}

sessionmaker = FastAPISessionMaker(settings.CYPRESSHUB_DATABASE_URL)


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


def bitbucket_auth():
    with sessionmaker.context_session() as db:
        s = crud.get_platform_settings(db, PlatformEnum.JIRA)
        if s:
            return s.username, s.token


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
    resp = requests.get(f'https://api.bitbucket.org/2.0/repositories/{repos}/commit/{sha}',
                        auth=bitbucket_auth())
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

    bbauth = bitbucket_auth()
    jira = jira_settings()

    resp = requests.get(f'https://api.bitbucket.org/2.0/repositories/{repos}/commit/{sha}', auth=bbauth)
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
    resp = requests.get(f'https://api.bitbucket.org/2.0/repositories/{repos}/pullrequests',
                        params={'q': f'source.branch.name=\"{branch}\"', 'state': 'OPEN'},
                        auth=bbauth)
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
    requests.post(f'https://api.bitbucket.org/2.0/repositories/{tr.repos}/commit/{tr.sha}/statuses/build/',
                  data={'key': 'Cypress tests',
                        'state': state,
                        'description': description,
                        'url': f'{settings.HUB_URL}/results/{tr.id}'})

