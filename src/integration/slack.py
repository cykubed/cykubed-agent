import logging
from datetime import timedelta

import requests

import crud
from exceptions import AuthException
from models import PlatformEnum, sessionmaker
from settings import settings
from utils import now


def slack_request(path, method='GET', **kwargs):
    with sessionmaker.context_session() as db:
        auth = crud.get_oauth(db, PlatformEnum.SLACK)
        if not auth:
            raise AuthException("Slack auth failed")

        if auth.expiry < now() + timedelta(minutes=settings.OAUTH_EXPIRY_BUFFER_MINUTES):
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


def get_slack_user_id(email):
    resp = slack_request('users.lookupByEmail', params={'email': email}).json()
    if not resp['ok']:
        logging.warning(f"Unknown slack user with {email} - returning email")
        return email
    return resp['user']['id']
