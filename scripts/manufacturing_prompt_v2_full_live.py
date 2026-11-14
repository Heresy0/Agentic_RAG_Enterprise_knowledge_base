"""Frozen full100 prompt-v2 evaluation; production prompts and workbenches stay unchanged."""
from contextlib import contextmanager
from scripts import manufacturing_gold_v4_grounded_live as transport

app = transport.app
read = transport.read
_base_plan = transport.make_plan


def make_plan():
    plan = _base_plan()
    plan['user_authorization'] = '用户明确要求第二版独立提示词以现有Qwen3.7-Flash真实模型完整100题；既有百炼端点、同范围中文公开/合成资料与演示字段；独立800调用/500万预算Token/9000秒，每题一次，不补跑、不拼分、不复用关闭批次额度；保留应用内现有修复和重排最多一次有界重试，索引0，原工作台不更新'
    plan['candidate'] = dict(version='v2', path='evals/manufacturing_prompt_candidate_v2/candidate-system.txt', model='Qwen3.7-Flash', production_prompt_replaced=False)
    return plan


@contextmanager
def source_change(args):
    """Wrap a REAL read, then PATCH the actual new OpenMES database before re-read.

    No source payload/authorization is replaced. This controller credential is
    private and separate from the platform's strictly read-only adapter token.
    """
    import httpx
    cfg = read(args.control)
    if cfg['upstream_container'] != 'gold100-v4-grounded-upstream-mes-20261114-r18':
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
    from backend.app.knowledge import answer_service
    from pathlib import Path
    candidate = Path(__file__).resolve().parents[1] / 'evals/manufacturing_prompt_candidate_v2/candidate-system.txt'
    answer_service.ANSWER_SYSTEM_PROMPT = candidate.read_text(encoding='utf-8')
    transport.source_change = source_change
    transport.make_plan = make_plan
    transport.main()


if __name__ == '__main__':
    main()
