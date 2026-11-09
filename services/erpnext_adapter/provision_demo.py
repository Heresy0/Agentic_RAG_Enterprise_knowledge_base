"""Run inside the dedicated ERPNext site to create demonstrative ERP documents.

Uses Frappe document insert/submit and Job Card time logs, never direct SQL
updates of the source facts. This is an administrator bootstrap, not the reader.
Input/output paths are private files supplied only to the isolated site.
"""
import json
from datetime import timedelta
from pathlib import Path


def run(input_path='/tmp/erpnext-seed-input.json', output_path='/tmp/erpnext-seed-result.json'):
    import frappe
    from frappe.utils import now_datetime

    args = json.loads(Path(input_path).read_text())
    if frappe.local.site != 'erpnext.localhost':
        raise ValueError('Demo bootstrap is restricted to the dedicated integration site.')
    frappe.set_user('Administrator')
    from erpnext.setup.setup_wizard.operations.install_fixtures import install
    install(country='China')
    frappe.db.set_single_value('System Settings', 'time_zone', 'Asia/Shanghai')
    frappe.clear_cache()

    def create(doctype, name, values):
        if frappe.db.exists(doctype, name):
            return frappe.get_doc(doctype, name)
        return frappe.get_doc(dict(doctype=doctype, **values)).insert(set_name=name)

    companies = [('Hengchuan ERPNext Lab', 'HCERP'), ('Hengchuan Other Lab', 'HCOTHER')]
    for company, abbreviation in companies:
        doc = create('Company', company, dict(company_name=company, abbr=abbreviation, country='China',
            default_currency='CNY', create_chart_of_accounts_based_on='Standard Template', chart_of_accounts='Standard'))
        if doc.abbr != abbreviation:
            raise ValueError('Unexpected existing company.')
    company = companies[0][0]
    frappe.db.set_single_value('Global Defaults', 'default_company', company)
    create('Item Group', 'HC Integration', dict(item_group_name='HC Integration', parent_item_group='All Item Groups', is_group=0))
    for code in ['HC-A17', 'HC-RAW']:
        create('Item', code, dict(item_code=code, item_name=code, item_group='HC Integration', stock_uom='Nos', is_stock_item=1))
    for operation in ['OP-20', 'OP-30']:
        create('Operation', operation, dict(name=operation))
    create('Workstation', 'HC-CNC-LAB', dict(workstation_name='HC-CNC-LAB', production_capacity=1))

    def warehouse(label, company):
        existing = frappe.db.get_value('Warehouse', dict(warehouse_name=label, company=company))
        if existing:
            return existing
        return frappe.get_doc(dict(doctype='Warehouse', warehouse_name=label, company=company, is_group=0)).insert().name

    def bom(name, company, operations):
        if frappe.db.exists('BOM', name):
            return frappe.get_doc('BOM', name)
        doc = frappe.get_doc(dict(doctype='BOM', item='HC-A17', quantity=1, company=company,
            currency='CNY', is_active=1, is_default=0, with_operations=1,
            operations=[dict(operation=operation, workstation='HC-CNC-LAB', time_in_mins=10) for operation in operations],
            items=[dict(item_code='HC-RAW', qty=1, rate=1, uom='Nos', stock_uom='Nos')])).insert(set_name=name)
        return doc.submit()

    def work_order(name, company, operations):
        if frappe.db.exists('Work Order', name):
            return frappe.get_doc('Work Order', name)
        code = 'HC-BOM-' + ('MULTI' if len(operations) > 1 else 'SINGLE') + ('-OTHER' if company != companies[0][0] else '')
        bill = bom(code, company, operations)
        doc = frappe.get_doc(dict(doctype='Work Order', production_item='HC-A17', company=company, qty=3,
            bom_no=bill.name, use_multi_level_bom=0, skip_transfer=1, from_wip_warehouse=0,
            planned_start_date='2026-10-01 08:00:00',
            source_warehouse=warehouse('HC Raw', company), wip_warehouse=warehouse('HC WIP', company),
            fg_warehouse=warehouse('HC Finished', company)))
        doc.get_items_and_operations_from_bom()
        doc.insert(set_name=name)
        return doc.submit()

    started = work_order('ERP-WO-STARTED', company, ['OP-20'])
    cards = frappe.get_all('Job Card', filters=dict(work_order=started.name, docstatus=['<', 2]), pluck='name')
    if len(cards) != 1:
        raise ValueError('Expected one demonstrative job card.')
    card = frappe.get_doc('Job Card', cards[0])
    if card.docstatus == 0:
        if not card.time_logs:
            end = now_datetime() - timedelta(minutes=5)
            card.append('time_logs', dict(from_time=end - timedelta(minutes=30), to_time=end, completed_qty=3))
        card.save()
        card.submit()
    work_order('ERP-WO-NOTSTARTED', company, ['OP-20'])
    work_order('ERP-WO-MULTI', company, ['OP-20', 'OP-30'])
    work_order('ERP-WO-OTHER', companies[1][0], ['OP-20'])

    role = create('Role', 'Knowledge Work Order Reader', dict(role_name='Knowledge Work Order Reader', desk_access=1))
    if not frappe.db.exists('Custom DocPerm', dict(parent='Work Order', role=role.name, permlevel=0)):
        frappe.get_doc(dict(doctype='Custom DocPerm', parent='Work Order', parenttype='DocType', parentfield='permissions',
            role=role.name, permlevel=0, read=1, select=1, write=0, create=0, delete=0, submit=0, cancel=0, amend=0,
            export=0, share=0)).insert()
    username = 'knowledge-reader@example.invalid'
    user = create('User', username, dict(email=username, first_name='Knowledge ERP Reader', enabled=1,
        send_welcome_email=0, user_type='System User', roles=[dict(role=role.name)]))
    if {row.role for row in user.roles} - {role.name, 'All', 'Desk User'}:
        raise ValueError('Reader has unexpected additional roles.')
    user.api_key = args['api_key']
    user.api_secret = args['api_secret']
    user.save()
    if not frappe.db.exists('User Permission', dict(user=username, allow='Company', for_value=company)):
        frappe.get_doc(dict(doctype='User Permission', user=username, allow='Company', for_value=company, apply_to_all_doctypes=1)).insert()
    frappe.db.set_single_value('System Settings', 'setup_complete', 1)
    frappe.clear_cache()
    frappe.db.commit()
    observed = frappe.get_doc('Work Order', started.name)
    result = dict(site=frappe.local.site, company=company, reader=username, job_card=card.name,
        started_work_order=started.name, actual_start_date=str(observed.actual_start_date) if observed.actual_start_date else None,
        work_orders=['ERP-WO-STARTED', 'ERP-WO-NOTSTARTED', 'ERP-WO-MULTI', 'ERP-WO-OTHER'], demo_only=True)
    with Path(output_path).open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    return dict(status='provisioned', work_orders=4, demo_only=True)


def change_demo_plan(restore=False):
    """Administrator-only fixture mutation for stale-source verification."""
    import frappe
    if frappe.local.site != 'erpnext.localhost':
        raise ValueError('Not the dedicated integration site.')
    frappe.set_user('Administrator')
    doc = frappe.get_doc('Work Order', 'ERP-WO-STARTED')
    doc.planned_start_date = '2026-10-01 08:00:00' if restore else '2026-10-01 08:01:00'
    doc.save()
    frappe.db.commit()
    return dict(status='restored' if restore else 'changed', demo_only=True)


def execution_demo(output_path='/tmp/erpnext-execution-result.json'):
    """Extend the dedicated lab through normal documents; no runtime write API."""
    import frappe
    from erpnext.manufacturing.doctype.work_order.work_order import create_job_card

    if frappe.local.site != 'erpnext.localhost':
        raise ValueError('Not the dedicated integration site.')
    frappe.set_user('Administrator')
    if frappe.db.exists('Work Order', 'ERP-WO-EXECUTION'):
        raise ValueError('Execution demo already exists; do not overwrite its evidence.')
    source = frappe.get_doc('Work Order', 'ERP-WO-MULTI')
    doc = frappe.get_doc(dict(doctype='Work Order', production_item=source.production_item,
        company=source.company, qty=9, bom_no=source.bom_no, use_multi_level_bom=0,
        skip_transfer=1, from_wip_warehouse=0, planned_start_date='2026-09-27 08:00:00',
        source_warehouse=source.source_warehouse, wip_warehouse=source.wip_warehouse, fg_warehouse=source.fg_warehouse))
    doc.get_items_and_operations_from_bom()
    doc.insert(set_name='ERP-WO-EXECUTION')
    doc.submit()
    first, second = doc.operations
    cards = frappe.get_all('Job Card', filters=dict(work_order=doc.name, operation_id=first.name, docstatus=0), pluck='name')
    if len(cards) != 1:
        raise ValueError('Unexpected initial job cards.')
    submitted = frappe.get_doc('Job Card', cards[0])
    submitted.for_quantity = 3
    submitted.append('time_logs', dict(from_time='2026-09-28 08:10:00', to_time='2026-09-28 08:40:00', completed_qty=3))
    submitted.save()
    submitted.submit()
    doc.reload()
    first = next(row for row in doc.operations if row.name == first.name)
    # ERPNext's factory expects a transient job_card_qty on a mapping, not a
    # persisted Work Order Operation document.
    row = frappe._dict(first.as_dict())
    row.job_card_qty = 3
    later = create_job_card(doc, row)
    later.pending_qty = 2
    later.append('time_logs', dict(from_time='2026-10-26 09:30:00', to_time='2026-10-26 10:00:00', completed_qty=1))
    later.insert()
    unknown = create_job_card(doc, row)
    unknown.insert()
    role = 'Knowledge Work Order Reader'
    if not frappe.db.exists('Custom DocPerm', dict(parent='Job Card', role=role, permlevel=0)):
        frappe.get_doc(dict(doctype='Custom DocPerm', parent='Job Card', parenttype='DocType', parentfield='permissions',
            role=role, permlevel=0, read=1, select=1, write=0, create=0, delete=0, submit=0, cancel=0, amend=0,
            export=0, share=0)).insert()
    frappe.clear_cache()
    doc.reload()
    result = dict(work_order=doc.name, operation_ids={row.operation: row.name for row in doc.operations},
        submitted_card=submitted.name, later_card=later.name, unknown_card=unknown.name,
        work_order_actual=str(doc.actual_start_date), operation_actual=str(first.actual_start_time),
        later_card_actual=str(later.actual_start_date), demo_only=True)
    with Path(output_path).open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    frappe.db.commit()
    return dict(status='provisioned', work_orders=1, operations=2, demo_only=True)


def change_execution_card(job_card_id, restore=False):
    """Administrator-only change to a draft demo time log for source checks."""
    import frappe
    if frappe.local.site != 'erpnext.localhost':
        raise ValueError('Not the dedicated integration site.')
    frappe.set_user('Administrator')
    doc = frappe.get_doc('Job Card', job_card_id)
    if doc.work_order != 'ERP-WO-EXECUTION' or doc.docstatus != 0 or len(doc.time_logs) != 1:
        raise ValueError('Only the dedicated draft time-log fixture can change.')
    doc.time_logs[0].from_time = '2026-10-26 09:30:00' if restore else '2026-10-26 09:31:00'
    doc.save()
    frappe.db.commit()
    return dict(status='restored' if restore else 'changed', demo_only=True)
