import logging
import re

import requests

from settings import settings

JIRA_HEADERS = {'Content-Type': 'application/json',
                'Accept': 'application/json; charset=utf8'}


def get_jira_auth():
    return (settings.JIRA_USER, settings.JIRA_TOKEN)


def get_slack_headers():
    return {'Authorization': f'Bearer {settings.SLACK_TOKEN}',
     'Content-Type': 'application/json; charset=utf8'}


def get_slack_user_id(email):
    resp = requests.get("https://slack.com/api/users.lookupByEmail",
                        headers=get_slack_headers(),
                 params={'email': email}).json()
    if not resp['ok']:
        print(resp)
        logging.warning(f"Unknown slack user with {email} - returning email")
        return email
    return resp['user']['id']


def create_user_notification(userid):
    return f"<@{userid}>"


def get_bitbucket_info(repos: str, sha: str):
    resp = requests.get(f'https://api.bitbucket.org/2.0/repositories/{repos}/commit/{sha}',
                        auth=(settings.BITBUCKET_USERNAME, settings.BITBUCKET_APP_PASSWORD))
    if resp.status_code != 200:
        raise Exception(f"Failed to fetch information from Bitbucket: {resp.status_code}: {resp.json()}")
    return resp.json()


def get_jira_user_details(account_id):
    resp = requests.get('https://kisanhub.atlassian.net/rest/api/3/user', {'accountId': account_id},
                        auth=get_jira_auth())
    if resp.status_code != 200:
        raise Exception(f"Failed to fetch information from JIRA: {resp.status_code}: {resp.json()}")
    return resp.json()


def get_commit_info(repos: str, sha: str):
    commit = get_bitbucket_info(repos, sha)
    user = get_jira_user_details(commit['author']['user']['account_id'])
    ret = {
        'commit_summary': commit['summary']['raw'],
        'commit_link': commit['links']['html']['href'],
        'avatar': user['avatarUrls']['48x48'],
        'author': user['displayName']
    }
    for issue_key in set(re.findall(r'([A-Z]{2,5}-[0-9]{1,5})', commit['message'])):
        r = requests.get(f'https://kisanhub.atlassian.net/rest/api/3/issue/{issue_key}', auth=get_jira_auth())
        if r.status_code == 200:
            ret['jira_ticket'] = f'https://kisanhub.atlassian.net/browse/{issue_key}'
            break
    return ret


if __name__ == '__main__':
    print(get_commit_info('kisanhubcore/kisanhub-webapp', '3ab8eb7eda07a8dcf8f21e65367b66bd32e58768'))
