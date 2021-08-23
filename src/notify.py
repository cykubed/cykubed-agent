import json
import json
import logging

import requests
from sqlalchemy.orm import Session

import crud
from integration import create_user_notification, get_slack_headers
from schemas import Results
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


def notify_fixed(results: Results):
    testrun = results.testrun
    repos, sha, branch = testrun.repos, testrun.sha, testrun.branch

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": PASSED.format(user=create_user_notification(testrun.author_slack_id),
                                      sha=sha, branch=branch, artifacts_url=settings.ARTIFACTS_URL)
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

    text = BUILD_FAIL.format(failures=results.failures, sha=sha,
                             user=create_user_notification(testrun.author_slack_id),
                             short_sha=sha[:8],
                             branch=testrun.branch,
                             artifacts_url=settings.ARTIFACTS_URL,
                             commit_url=testrun.commit_link)
    # get specs with fails
    # TODO replace with Handlebars
    # if failed_tests:
    #     for failed_test in failed_tests[:SPEC_FILE_SLACK_LIMIT]:
    #         text += f"\n * {failed_test['file']}: \"{failed_test['test']}\""
    #
    # if len(failed_tests) > SPEC_FILE_SLACK_LIMIT:
    #     text += f"\n + {len(failed_tests) - SPEC_FILE_SLACK_LIMIT} others..."

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
#     parser = argparse.ArgumentParser()
#     parser.add_argument('sha')
#     parser.add_argument('branch')
#     options = parser.parse_args()
#     tr = TestRun(sha=options.sha, repos='kisanhubcore/kisanhub-webapp', branch=options.branch)
#     notify_failed(tr, 1, ['spec1.ts', 'spec2.ts'])
#     notify_fixed(tr)
