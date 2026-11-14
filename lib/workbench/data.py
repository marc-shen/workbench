"""Read declarative TOML; JSON is reserved for runtime records and wire formats."""
from pathlib import Path
import tomllib

REPO = Path(__file__).resolve().parents[2]



def read(path):
    try:
        with Path(path).open('rb') as stream:
            return tomllib.load(stream)
    except tomllib.TOMLDecodeError as error:
        raise ValueError(str(path) + ': ' + str(error)) from error


CATEGORIES = read(REPO / 'catalog/categories.toml')

def specifications(repo=REPO):
    return {path.parent.name: read(path) for path in sorted((repo / 'components').glob('*/component.toml'))}


def install_specs(kind, repo=REPO):
    return {name: dict(spec['install']) for name, spec in specifications(repo).items()
            if spec.get('install', {}).get('kind') == kind}


def tool_specs(repo=REPO):
    return {name: dict(spec, backend=spec['provider']) for name, spec in install_specs('package', repo).items()}


def language_specs(repo=REPO):
    return {spec['storage_name']: dict(spec, backend=spec['provider'])
            for spec in install_specs('language-server', repo).values()}


def plugin_sources(repo=REPO):
    return {name: spec['repository'] for name, spec in install_specs('plugin', repo).items()}


def network_sources(repo=REPO):
    specs = install_specs('network', repo)
    return {'deck-tailscale': specs['tailscale']['source']}
