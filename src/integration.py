import logging
import re
from email.utils import parseaddr

import requests

from models import TestRun
from settings import settings

JIRA_HEADERS = {'Content-Type': 'application/json',
                'Accept': 'application/json; charset=utf8'}


def get_slack_user_id(email):
    resp = requests.get("https://slack.com/api/users.lookupByEmail",
                        headers=settings.slack_headers,
                 params={'email': email}).json()
    if not resp['ok']:
        print(resp)
        logging.warning(f"Unknown slack user with {email} - returning email")
        return email
    return resp['user']['id']


def create_user_notification(userid):
    return f"<@{userid}>"


def get_bitbucket_commit_info(repos: str, sha: str):
    resp = requests.get(f'https://api.bitbucket.org/2.0/repositories/{repos}/commit/{sha}',
                        auth=settings.bitbucket_auth)
    if resp.status_code != 200:
        raise Exception(f"Failed to fetch information from Bitbucket: {resp.status_code}: {resp.json()}")
    return resp.json()


def get_jira_user_details(account_id):
    resp = requests.get(f'{settings.JIRA_URL}/rest/api/3/user',
                        {'accountId': account_id},
                        auth=settings.jira_auth)
    if resp.status_code != 200:
        raise Exception(f"Failed to fetch information from JIRA: {resp.status_code}: {resp.json()}")
    return resp.json()


def get_jira_ticket_link(message):
    for issue_key in set(re.findall(r'([A-Z]{2,5}-[0-9]{1,5})', message)):
        r = requests.get(f'{settings.JIRA_URL}/rest/api/3/issue/{issue_key}', auth=settings.jira_auth)
        if r.status_code == 200:
            return f'{settings.JIRA_URL}/browse/{issue_key}'


def get_bitbucket_details(repos: str, branch: str, sha: str):
    commit = get_bitbucket_commit_info(repos, sha)
    user = get_jira_user_details(commit['author']['user']['account_id'])
    ret = {
        'commit_summary': commit['summary']['raw'],
        'commit_link': commit['links']['html']['href'],
        'avatar': user['avatarUrls']['48x48'],
        'author': user['displayName']
    }
    # get the author name so we can find a Slack handle
    name, email = parseaddr(commit['author']['raw'])
    if email:
        ret['author_slack_id'] = get_slack_user_id(email)

    # look for open PRs
    print(branch)
    resp = requests.get(f'https://api.bitbucket.org/2.0/repositories/{repos}/pullrequests',
                        params={'q': f'source.branch.name=\"{branch}\"', 'state': 'OPEN'},
                        auth=settings.bitbucket_auth)
    pr_title = None
    if resp.status_code == 200:
        prdata = resp.json()
        if prdata['size']:
            ret['pull_request_link'] = prdata['values'][0]['links']['html']['href']
            pr_title = prdata['values'][0]['title']

    ticket_link = get_jira_ticket_link(branch)
    if not ticket_link and pr_title:
        # check PR title
        ticket_link = get_jira_ticket_link(pr_title)

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

