import json
import logging

import requests
from sqlalchemy.orm import Session

import crud
from integration import create_user_notification, slack_headers
from schemas import Results
from settings import settings

logging.basicConfig(level=logging.INFO)

# FIXME and move this into the database
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

    resp = requests.post("https://slack.com/api/chat.postMessage", headers=slack_headers(),
                         data=json.dumps(slack_msg))
    if resp.status_code != 200:
        logging.error(f"Failed to post to Slack: {resp.json()}")
    else:
        ret = resp.json()
        if not ret.get('ok'):
            logging.error(f"Failed to post to Slack: {str(ret)}")


BUILD_FAIL = """
:no_entry: <{hub_url}/results/{testrun.id}|*{results.failures}* tests failed> on branch `{testrun.branch}` @ `{short_sha}` by {user}
> {testrun.commit_summary}
"""

PASSED = """
:thumbsup: <{hub_url}/results/{testrun.id}|Tests fixed> for branch `{testrun.branch}` by {user}
"""


def notify_fixed(results: Results):
    testrun = results.testrun
    repos, sha, branch = testrun.repos, testrun.sha, testrun.branch

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": PASSED.format(results=results,
                                      hub_url=settings.RESULTS_UI_URL,
                                      testrun=results.testrun,
                                      user=create_user_notification(testrun.author_slack_id))
            },
            "accessory": {
                "type": "image",
                "image_url": SLACK_INFO[repos]['pass_icon'],
                "alt_text": SLACK_INFO[repos]['name'] + " image"
            }

        }
    ]
    send_slack_message_blocks(branch, testrun.author_slack_id, blocks, True)


def notify_failed(results: Results):
    testrun = results.testrun
    sha = testrun.sha

    text = BUILD_FAIL.format(hub_url=settings.RESULTS_UI_URL,
                             results=results,
                             user=create_user_notification(testrun.author_slack_id),
                             short_sha=sha[:8],
                             testrun=testrun,
                             branch=testrun.branch,
                             commit_url=testrun.commit_link)

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
    send_slack_message_blocks(testrun.branch, testrun.author_slack_id, blocks, settings.TEST_MODE)


def notify(results: Results, db: Session):
    if results.testrun.author_slack_id:

        if results.failures:
            notify_failed(results)
        else:
            # did the last run pass?
            last = crud.get_last_run(db, results.testrun)
            if last and last.status != 'passed':
                # nope - notify
                notify_fixed(results)

#
# if __name__ == '__main__':
#
#     tr = TestRun(branch='PH-471-force-fail', id=1,
#                  sha='46be8575c9c11c6fff6cd34cae03ba53e349f1ea',
#                  started=now(),
#                  repos="kisanhubcore/kisanhub-webapp",
#                  active=False,
#                  status='failed',
#                  author_slack_id=get_slack_user_id('nick@kisanhub.com'),
#                  commit_link='commit_link',
#                  commit_summary='Run tests on all commits, using new base image with pre-built Cypress',
#                  files=[])
#
#     results = Results(testrun=tr, specs=[], failures=2, total=5)
#     notify_failed(results)
#     notify_fixed(results)
