from fastapi import APIRouter, Depends, HTTPException

from . import bed_transfers, coverage
from .auth import current_staff, require
from .bed_transfer_schemas import NewBedTransfer, RedispatchResponse
from .db import connect
from .operations import RESPONSE_MINUTES
from .schemas import Role


router = APIRouter(tags=['bed transfers'])
lead = require(Role.LEAD)


def _with_choices(db, pending):
    for item in pending:
        alert = db.execute('SELECT priority FROM alerts WHERE id=%s', (item['alert_id'],)).fetchone()
        minutes = RESPONSE_MINUTES.get(alert['priority'] if alert else None, 0)
        item['sla_minutes'] = minutes
        item['coverage'] = coverage.roster(db, item['current_unit'], minutes)
        item['managed'] = coverage.is_managed(db, item['current_unit'])
    return pending


@router.get('/bed-transfers')
def transfers(staff=Depends(current_staff)):
    with connect() as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        items = bed_transfers.listing(db)
        residents = db.execute(
            '''SELECT id,code,name,unit,room FROM residents
               WHERE active ORDER BY unit,room,id''',
        ).fetchall()
        pending = _with_choices(db, bed_transfers.pending_redispatches(db))
        carers = db.execute(
            """SELECT s.id,s.name FROM staff s WHERE s.role='carer' AND NOT EXISTS (
                   SELECT 1 FROM shift_coverage c WHERE c.carer_id=s.id AND c.ended_at IS NULL
                   AND c.ends_at > now()
               ) ORDER BY s.name,s.id""",
        ).fetchall() if staff['role'] == 'lead' else []
        return {'items': items, 'residents': residents, 'pending_redispatches': pending,
                'carers': carers}


@router.get('/bed-transfers/{transfer_id}')
def transfer_detail(transfer_id: int, staff=Depends(current_staff)):
    with connect() as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        return bed_transfers.detail(db, transfer_id)


@router.post('/bed-transfers', status_code=201)
def new_transfer(data: NewBedTransfer, staff=Depends(lead)):
    with connect() as db:
        return bed_transfers.create(db, data, staff['id'])


@router.post('/bed-transfers/{transfer_id}/redispatches/{redispatch_id}/assign', status_code=201)
def assign_redispatch(transfer_id: int, redispatch_id: int, data: RedispatchResponse,
                      staff=Depends(lead)):
    with connect() as db:
        row = bed_transfers.redispatch(db, transfer_id, redispatch_id, data, staff['id'])
        if row is None:
            raise HTTPException(409, '重派失败，请刷新后重试')
        return row
