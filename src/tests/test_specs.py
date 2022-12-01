import os

from build import get_specs

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
