import argparse
import json
import logging
from email.utils import parseaddr

import requests

from integration import get_slack_user_id, get_bitbucket_info, create_user_notification, get_slack_headers
from models import TestRun
from settings import settings

logging.basicConfig(level=logging.INFO)

SLACK_INFO = {
    "kisanhubcore/kisanhub-webapp": {
        "name": "Phoenix",
        "fail_icon": "https://storage.googleapis.com/kisanhub-static-assets/img/dead-parrot.png",
        "pass_icon": "https://storage.googleapis.com/kisanhub-static-assets/img/phoenix.png"
    },
    "kisanhubcore/pegasus": {
        "name": "Pegasus",
        "fail_icon": "https://storage.googleapis.com/kisanhub-static-assets/img/dead-horse.png",
        "pass_icon": "https://storage.googleapis.com/kisanhub-static-assets/img/pegasus.png"
    }
}

SPEC_FILE_SLACK_LIMIT = 5


def send_slack_message_blocks(branch, slack_id, blocks, test_mode=True):
    if branch not in ['qa', 'master']:
        channel = slack_id
    elif test_mode:
        channel = '#devops-test'
    else:
        channel = '#core-platform'
    slack_msg = dict(blocks=blocks, channel=channel)

    resp = requests.post("https://slack.com/api/chat.postMessage", headers=get_slack_headers(),
                         data=json.dumps(slack_msg))
    if resp.status_code != 200:
        logging.error(f"Failed to post to Slack: {resp.json()}")
    else:
        ret = resp.json()
        if not ret.get('ok'):
            logging.error(f"Failed to post to Slack: {str(ret)}")


BUILD_FAIL = """
:warning: *{failures}* <{artifacts_url}/reports/{sha}/index.html|Tests for branch *{branch}* failed> with commit <{commit_url}|{short_sha}> by {user}
*Failed spec files*:
"""

PASSED = """
:thumbsup: Fixed tests for <{artifacts_url}/reports/{sha}/index.html|branch *{branch}*> now pass: nice one {user}!
"""


def notify_fixed(testrun: TestRun):
    repos, sha, branch = testrun.repos, testrun.sha, testrun.branch
    commit = get_bitbucket_info(repos, sha)
    # get the author name so we can find a Slack handle
    name, email = parseaddr(commit['author']['raw'])
    if email:
        slack_id = get_slack_user_id(email)
    else:
        slack_id = email
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": PASSED.format(user=create_user_notification(slack_id), sha=sha, branch=branch, artifacts_url=settings.ARTIFACTS_URL)
            },
            "accessory": {
                "type": "image",
                "image_url": SLACK_INFO[repos]['pass_icon'],
                "alt_text": SLACK_INFO[repos]['name'] + " image"
            }

        }
    ]
    send_slack_message_blocks(branch, slack_id, blocks, True)


def notify_failed(testrun: TestRun, failures, specs_with_fails=None):
    sha = testrun.sha
    commit = get_bitbucket_info(testrun.repos, testrun.sha)
    # get the author name so we can find a Slack handle
    name, email = parseaddr(commit['author']['raw'])
    if email:
        slack_id = get_slack_user_id(email)
    else:
        slack_id = email

    text = BUILD_FAIL.format(failures=failures, sha=sha, user=create_user_notification(slack_id),
                             short_sha=sha[:8],
                             branch=testrun.branch,
                             artifacts_url=settings.ARTIFACTS_URL,
                             commit_url=commit['links']['html']['href'])
    if specs_with_fails:
        text += "\n".join([" * {}".format(f) for f in specs_with_fails[:SPEC_FILE_SLACK_LIMIT]])

    if len(specs_with_fails) > SPEC_FILE_SLACK_LIMIT:
        text += f"\n + {len(specs_with_fails) - SPEC_FILE_SLACK_LIMIT} others..."

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": text
            },
            "accessory": {
                "type": "image",
                "image_url": SLACK_INFO[testrun.repos]['fail_icon'],
                "alt_text": SLACK_INFO[testrun.repos]['name'] + " image"
            }
        }
    ]
    send_slack_message_blocks(testrun.branch, slack_id, blocks, settings.TEST_MODE)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('sha')
    parser.add_argument('branch')
    options = parser.parse_args()
    tr = TestRun(sha=options.sha, repos='kisanhubcore/kisanhub-webapp', branch=options.branch)
    notify_failed(tr, 1, ['spec1.ts', 'spec2.ts'])
    notify_fixed(tr)
