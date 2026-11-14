"""Frozen full100 generation-focus check; business retrieval/validation stay unchanged."""
from contextlib import contextmanager
from scripts import manufacturing_gold_v4_grounded_live as transport

app = transport.app
read = transport.read
_base_plan = transport.make_plan


def make_plan():
    plan = _base_plan()
    plan['user_authorization'] = '用户明确要求当前生成提示完整100题真实模型验证，检索和校验保持不动；独立800调用/500万Token/9000秒，不重试补跑、不拼分'
    return plan


@contextmanager
def source_change(args):
    """Wrap a REAL read, then PATCH the actual new OpenMES database before re-read.

    No source payload/authorization is replaced. This controller credential is
    private and separate from the platform's strictly read-only adapter token.
    """
    import httpx
    cfg = read(args.control)
    if cfg['upstream_container'] != 'gold100-v4-grounded-upstream-mes-20261113-r8':
        raise ValueError('Fault controller must point at the independent clone.')
    upstream = httpx.Client(base_url=cfg['url'], trust_env=False, timeout=20,
                           headers={'Authorization': 'Bearer ' + cfg['token'], 'Accept': 'application/json'})
    connector = next(iter(app.state.mes_connectors.values()))
    original = connector.read_context
    changed = []; old = {}
    async def read_then_change(*a, **kw):
        context = await original(*a, **kw)
        if not changed:
            wo = context.record.work_order_id
            value = upstream.get('/api/v1/work-orders/' + wo)
            value.raise_for_status(); old.update(wo=wo, quantity=value.json()['data']['planned_qty'])
            result = upstream.patch('/api/v1/work-orders/' + wo, json=dict(planned_qty=float(old['quantity']) + 1))
            result.raise_for_status(); changed.append(True)
        return context
    connector.read_context = read_then_change
    try:
        yield changed
    finally:
        connector.read_context = original
        if changed:
            result = upstream.patch('/api/v1/work-orders/' + old['wo'], json=dict(planned_qty=old['quantity']))
            result.raise_for_status()
        upstream.close()



def main():
    # Only this new process targets the new clone; closed entries stay frozen.
    transport.source_change = source_change
    transport.make_plan = make_plan
    transport.main()


if __name__ == '__main__':
    main()
