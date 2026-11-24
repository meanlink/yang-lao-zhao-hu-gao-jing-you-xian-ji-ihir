from src import bed_transfers, operations, workflow
from src.bed_transfer_schemas import NewBedTransfer, TransferMove
from src.operations_schemas import AssignResponse
from src.priority_model import PriorityModel
from src.schemas import Review

DEMO_REF = 'BT-DEMO-2071'
EAST_CODE = 'ZH-207'
WEST_CODE = 'ZH-309'


def seed_bed_transfer_case(db, staff):
    """Show one applied cross-wing swap whose assigned task waits for a new-wing dispatch."""
    if db.execute('SELECT 1 FROM bed_transfers WHERE ref=%s', (DEMO_REF,)).fetchone():
        return
    east = db.execute('SELECT * FROM residents WHERE code=%s', (EAST_CODE,)).fetchone()
    west = db.execute('SELECT * FROM residents WHERE code=%s', (WEST_CODE,)).fetchone()
    if east is None or west is None or east['unit'] == west['unit']:
        return
    if (east['unit'], east['room']) != ('东翼二层', '207-B'):
        return

    model = PriorityModel()
    alert = db.execute('''SELECT a.* FROM alerts a WHERE a.resident_id=%s AND a.status<>'closed' ''',
                       (east['id'],)).fetchone()
    if alert is not None and alert['status'] in ('evaluating', 'incomplete', 'review'):
        try:
            while workflow.score_one(db, model, east['id']):
                pass
            alert = db.execute('SELECT * FROM alerts WHERE id=%s', (alert['id'],)).fetchone()
            if alert['status'] == 'review':
                workflow.review(db, alert['id'], Review(
                    revision=alert['revision'], priority=2,
                    note='东翼二层重复呼叫，登记照护响应后安排跨翼调床。'), staff['lead'])
        except Exception:
            return

    alert = db.execute('''SELECT a.* FROM alerts a WHERE a.resident_id=%s AND a.status<>'closed' ''',
                       (east['id'],)).fetchone()
    response = None
    if alert is not None and alert['status'] == 'confirmed':
        response = operations.create_response(db, alert['id'], AssignResponse(
            revision=alert['revision'], assigned_to=staff['carer'],
            instruction='核对 207-B 的呼叫记录与未完成整理任务。'), staff['lead'])

    transfer = bed_transfers.create(db, NewBedTransfer(
        ref=DEMO_REF, note='东翼二层与西翼三层互换，便于夜间照护分组。',
        moves=[TransferMove(resident_id=east['id'], from_unit=east['unit'], from_room=east['room']),
               TransferMove(resident_id=west['id'], from_unit=west['unit'], from_room=west['room'])]),
        staff['lead'])
    return transfer['id'] if response else None
