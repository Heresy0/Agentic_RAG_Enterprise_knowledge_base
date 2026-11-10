"""Create private credentials for an isolated OpenMES environment; no deployment."""
import argparse
import base64
import json
from pathlib import Path
import re
import secrets

ROOT = Path(__file__).resolve().parents[1]


def prepare(output: Path, *, port: int, image: str, upstream_image: str, upstream_revision: str):
    output = output.resolve()
    if not output.is_relative_to(ROOT / 'tmp') or not 1024 <= port <= 65535 or port in (8000,8001,8002,8003,8080,18080):
        raise ValueError('Use a new directory under tmp and an independent port.')
    if not re.fullmatch(r'[a-z0-9][a-z0-9./:_-]{1,200}', image) or not re.fullmatch(r'ghcr.io/mes-open/openmes@sha256:[a-f0-9]{64}', upstream_image):
        raise ValueError('Pin the upstream image digest and use a local integration image.')
    if not re.fullmatch(r'[a-f0-9]{40}', upstream_revision):
        raise ValueError('Pin the upstream source revision.')
    values = dict(OPENMES_PORT=str(port), OPENMES_IMAGE=image,
        OPENMES_APP_KEY='base64:'+base64.b64encode(secrets.token_bytes(32)).decode(),
        **{name:secrets.token_urlsafe(40) for name in ('OPENMES_DB_PASSWORD','OPENMES_ADMIN_PASSWORD','OPENMES_REVERB_KEY','OPENMES_REVERB_SECRET')})
    output.mkdir(parents=True, exist_ok=False)
    with (output/'deployment.env').open('x',encoding='utf-8',newline='\n') as f:
        f.write(''.join(k+'='+v+'\n' for k,v in values.items()))
    state=dict(project='manufacturing-openmes',port=port,image=image,upstream_image=upstream_image,
               upstream_revision=upstream_revision,model_calls=0,production_factory=False)
    with (output/'state.json').open('x',encoding='utf-8') as f:json.dump(state,f,indent=2)
    return state


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--port',type=int,default=18081)
    parser.add_argument('--image',required=True)
    parser.add_argument('--upstream-image',required=True)
    parser.add_argument('--upstream-revision',required=True)
    args=parser.parse_args()
    print(json.dumps(prepare(args.output_dir,port=args.port,image=args.image,
                           upstream_image=args.upstream_image,upstream_revision=args.upstream_revision)))


if __name__=='__main__':main()
