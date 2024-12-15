import click
import os
import subprocess

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
              default='patch', help='Type of version bump')
def generate(bump: str):

    # if cmd('git branch --show-current') != MAIN_BRANCH:
    #     raise click.BadParameter('Not on master branch')

    # bump and get the tag
    tag = cmd(f"poetry version {bump} -s")

    # commit and tag
    cmd(f'git add pyproject.toml')
    cmd(f'git commit -m "New release {tag}"')
    cmd(f'git tag -a {tag} -m "New release:\n{tag}"')
    cmd(f'git push origin tag {tag}')


if __name__ == '__main__':
    generate()
