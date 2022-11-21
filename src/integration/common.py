from email.utils import parseaddr

from integration.bitbucket import bitbucket_request
from integration.jira import get_jira_user_details, get_jira_ticket_link
from integration.slack import get_slack_user_id


def get_commit_details(repos: str, branch: str, sha: str):

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

    ticket_link = get_jira_ticket_link(branch)
    if not ticket_link and pr_title:
        # check PR title
        ticket_link = get_jira_ticket_link(pr_title)

    if ticket_link:
        ret['jira_ticket'] = ticket_link

    return ret


OAUTH_EXPIRY_BUFFER_MINUTES = 10


def create_user_notification(userid):
    return f"<@{userid}>"
