import os

from build import get_specs
from common import schemas
from common.enums import PlatformEnum
from jobs import encode_testrun, decode_testrun

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), 'fixtures')


def test_get_specs_defaults():
    specs = set(get_specs(os.path.join(FIXTURE_DIR, 'jsoncfg_defaults')))
    assert specs == {'cypress/integration/stuff/test1.spec.ts',
                     'cypress/integration/stuff/test2.spec.ts'}


def test_get_specs_json():
    specs = set(get_specs(os.path.join(FIXTURE_DIR, 'jsoncfg_specified')))
    assert specs == {'cypress/tests/test2.cy.ts'}


def test_get_specs_ts():
    specs = set(get_specs(os.path.join(FIXTURE_DIR, 'tscfg')))
    assert specs == {'cypress/xe2e/tests/test1.cy.js',
                     'cypress/xe2e/tests/test2.cy.ts'}


def test_b64():
    project = schemas.Project(id=1, name='test', platform=PlatformEnum.GITHUB,
                              url='https://xxx.github.com', parallelism=1,
                              build_cmd='ng build', build_cpu='2', build_memory='100mb',
                              agent_image='foo',
                              runner_image='blah', runner_cpu='1', runner_memory='1000mb')
    tr = schemas.NewTestRun(id=2, project=project, branch='master')
    x = encode_testrun(tr)
    # back the other way
    newtr = decode_testrun(x)
    assert newtr.id == tr.id
    assert newtr.project.url == tr.project.url

