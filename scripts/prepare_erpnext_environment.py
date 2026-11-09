"""Prepare private credentials for the independent ERPNext Compose project."""
import argparse
import json
from pathlib import Path
import secrets

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--port', type=int, default=18080)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    output = args.output_dir.resolve()
    if not output.is_relative_to(ROOT / 'tmp') or not 1024 <= args.port <= 65535 or args.port in (8000, 8001, 8002, 8080):
        raise ValueError('Use a new directory under tmp and an independent port.')
    plan = dict(project='manufacturing-erpnext', image='frappe/erpnext:v16.54.0', port=args.port,
                site='erpnext.localhost', model_calls=0)
    if args.apply:
        output.mkdir(parents=True, exist_ok=False)
        credentials = output / 'secrets'
        credentials.mkdir()
        for name in ('db-root-password', 'admin-password'):
            with (credentials / name).open('x', encoding='utf-8') as stream:
                stream.write(secrets.token_urlsafe(40))
        with (output / 'deployment.env').open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(f'ERPNEXT_PORT={args.port}\nERPNEXT_SITE_NAME={plan["site"]}\nERPNEXT_SECRETS_DIR={credentials.as_posix()}\n')
        with (output / 'state.json').open('x', encoding='utf-8') as stream:
            json.dump(dict(**plan, credentials='secrets/admin-password', production_factory=False), stream, indent=2)
    print(json.dumps(dict(mode='prepared' if args.apply else 'dry_run', **plan)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
