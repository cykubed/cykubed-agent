import os
import subprocess
from string import Template

import click

MAIN_BRANCH = 'master'

ROOT_DIR = os.path.dirname(__file__)
TEMPLATE_DIR = os.path.join(ROOT_DIR, 'templates')


def cmd(args: str, silent=False) -> str:
    p = subprocess.run(args, capture_output=True, text=True, shell=True)
    if p.returncode != 0:
        raise click.ClickException(f'{args} failed with return code {p.returncode} and output {p.stdout} (error={p.stderr})')
    if not silent:
        print(p.stdout)
    return p.stdout.strip()


@click.command(help='Generate a new release of the runner')
@click.option('-b', '--bump', type=click.Choice(['major', 'minor', 'patch']),
              default='minor', help='Type of version bump')
@click.option('-g', '--generate_only', is_flag=True, help='Generate only')
def generate(bump: str, generate_only: bool):

    if cmd('git branch --show-current') != MAIN_BRANCH:
        raise click.BadParameter('Not on master branch')

    # run the tests first as a sanity check
    if not generate_only:
        cmd('py.test', True)
        # bump and get the tag
        tag = cmd(f"poetry version {bump} -s")
    else:
        tag = cmd(f"poetry version -s")

    template_file = os.path.join(TEMPLATE_DIR, 'cloudbuild.yaml')
    with open(template_file, 'r') as f:
        rendered = Template(f.read()).substitute(dict(VERSION=tag))
    with open(f'{ROOT_DIR}/cloudbuild.yaml', 'w') as f:
        f.write(rendered)

    if not generate_only:
        # all done: commit and tag
        cmd(f'git add cloudbuild.yaml')
        cmd(f'git add pyproject.toml')
        cmd(f'git commit -m "New release {tag}"')
        cmd(f'git tag -a {tag} -m "New release:\n{tag}"')
        cmd(f'git push origin {tag}')


if __name__ == '__main__':
    generate()
