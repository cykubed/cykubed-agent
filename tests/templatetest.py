import os

import yaml


def compare_rendered_template_from_mock(mock_create_from_dict, jobtype: str, index=0):
    yamlobjects = mock_create_from_dict.call_args_list[index].args[0]
    compare_rendered_template([yamlobjects], jobtype)


def compare_rendered_template(yamlobjects, jobtype: str):
    asyaml = yaml.safe_dump_all(yamlobjects, indent=4, sort_keys=True)
    # print('\n'+asyaml)
    with open(os.path.join(FIXTURES_DIR, 'rendered-templates', f'{jobtype}.yaml'), 'r') as f:
        expected = f.read()
        assert asyaml == expected


FIXTURES_DIR = os.path.join(os.path.dirname(__file__), 'fixtures')
