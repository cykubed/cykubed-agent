import os.path

from common.schemas import NewTestRun
from jobs import create_deploy_job
from state import TestRunBuildState
from templatetest import compare_rendered_template_from_mock

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), 'fixtures')


async def test_deploy(redis, testrun: NewTestRun,
                      mock_create_from_dict):
    """
    New run with a node cache
    """
    testrun.project.environment_variables = [{'name': 'DEPLOY_KEY', 'value': 'fish'}]
    st = TestRunBuildState(trid=testrun.id,
                           project_id=testrun.project.id,
                           run_job_index=1,
                           build_storage=10,
                           rw_build_pvc='5-project-1-rw',
                           specs=['spec1.ts'])
    await st.save()

    await create_deploy_job(testrun)

    compare_rendered_template_from_mock(mock_create_from_dict, 'deploy-job')
