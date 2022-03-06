import re
from datetime import timedelta

import requests

import crud
import integration.common
from exceptions import AuthException
from models import PlatformEnum, sessionmaker
from settings import settings
from utils import now


def jira_request(path, method='GET', **kwargs):
    with sessionmaker.context_session() as db:
        auth = crud.get_oauth(db, PlatformEnum.JIRA)
        if not auth:
            raise AuthException("Jira auth failed")

        if not auth.cloud_id:
            raise AuthException("Jira needs a cloud_id")

        if auth.expiry < now() + timedelta(minutes=integration.common.OAUTH_EXPIRY_BUFFER_MINUTES):
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


def get_jira_user_details(account_id):

    resp = jira_request('/rest/api/3/user', params={'accountId': account_id})
    if resp.status_code != 200:
        raise Exception(f"Failed to fetch information from JIRA: {resp.status_code}: {resp.json()}")
    return resp.json()


def get_jira_ticket_link(message):
    for issue_key in set(re.findall(r'([A-Z]{2,5}-[0-9]{1,5})', message)):
        r = jira_request(f'/rest/api/3/issue/{issue_key}')
        if r.status_code == 200:
            data = r.json()
            # FIXME need to get the web site url - is this the right API?
            return issue_key
            # return f'{jira.url}/browse/{issue_key}'
