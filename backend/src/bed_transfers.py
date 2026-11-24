from fastapi import HTTPException
from psycopg.errors import UniqueViolation

from . import coverage, workflow
from .db import audit
from .operations import RESPONSE_MINUTES
from .schemas import AlertStatus


def _lock_ref(db, ref):
    """Serialize submissions that reuse the same transfer reference."""
    db.execute('SELECT pg_advisory_xact_lock(1049004, hashtext(%s))', (ref,))


def _active_responses(db, resident_ids):
    return db.execute(
        '''SELECT t.*, a.resident_id, a.revision AS current_revision
           FROM care_responses t JOIN alerts a ON a.id=t.alert_id
           WHERE a.resident_id = ANY(%s) AND a.status <> %s AND t.status IN ('assigned','accepted')
           ORDER BY t.id FOR UPDATE OF t''',
        (list(resident_ids), AlertStatus.CLOSED),
    ).fetchall()


def detail(db, transfer_id):
    transfer = db.execute(
        '''SELECT t.id,t.ref,t.note,t.status,t.void_reason,t.created_at,t.applied_at,
                  s.name AS requested_name
           FROM bed_transfers t JOIN staff s ON s.id=t.requested_by WHERE t.id=%s''', (transfer_id,),
    ).fetchone()
    if transfer is None:
        raise HTTPException(404, '调床单不存在')
    transfer['moves'] = db.execute(
        '''SELECT m.resident_id,m.from_unit,m.from_room,m.to_unit,m.to_room,
                  r.code,r.name AS resident_name,r.active,r.unit AS current_unit,r.room AS current_room
           FROM bed_transfer_moves m JOIN residents r ON r.id=m.resident_id
           WHERE m.transfer_id=%s ORDER BY m.resident_id''', (transfer_id,),
    ).fetchall()
    transfer['kept_responses'] = db.execute(
        '''SELECT k.response_id,k.resident_id,r.name AS resident_name,r.room AS current_room,
                  s.name AS assignee_name,t.status AS response_status,t.due_at
           FROM bed_transfer_kept_responses k JOIN staff s ON s.id=k.assignee
           JOIN residents r ON r.id=k.resident_id JOIN care_responses t ON t.id=k.response_id
           WHERE k.transfer_id=%s ORDER BY k.response_id''', (transfer_id,),
    ).fetchall()
    transfer['redispatches'] = db.execute(
        '''SELECT d.id,d.response_id,d.alert_id,d.alert_revision,d.resident_id,d.assignee,
                  d.priority,d.from_unit,d.to_unit,d.status,d.new_response_id,d.created_at,
                  d.dispatched_at,s.name AS assignee_name,r.code,r.name AS resident_name,
                  r.unit AS current_unit,r.room AS current_room,
                  n.status AS new_response_status,x.name AS new_assignee_name
           FROM bed_transfer_redispatches d JOIN staff s ON s.id=d.assignee
           JOIN residents r ON r.id=d.resident_id
           LEFT JOIN care_responses n ON n.id=d.new_response_id
           LEFT JOIN staff x ON x.id=n.assigned_to
           WHERE d.transfer_id=%s ORDER BY d.id''', (transfer_id,),
    ).fetchall()
    return transfer


def listing(db, limit=30):
    rows = db.execute('SELECT id FROM bed_transfers ORDER BY id DESC LIMIT %s', (limit,)).fetchall()
    return [detail(db, row['id']) for row in rows]


def pending_redispatches(db):
    return db.execute(
        '''SELECT d.id,d.transfer_id,d.response_id,d.alert_id,d.alert_revision,d.resident_id,
                  d.assignee,d.priority,d.from_unit,d.to_unit,d.created_at,
                  s.name AS assignee_name,r.code,r.name AS resident_name,
                  r.unit AS current_unit,r.room AS current_room,t.ref AS transfer_ref
           FROM bed_transfer_redispatches d JOIN staff s ON s.id=d.assignee
           JOIN residents r ON r.id=d.resident_id JOIN bed_transfers t ON t.id=d.transfer_id
           WHERE d.status='pending' ORDER BY d.id''',
    ).fetchall()


def _destinations(data):
    return {
        move.resident_id: next(other for other in data.moves if other.resident_id != move.resident_id)
        for move in data.moves
    }


def _void(db, data, actor, residents):
    """Record a submission that no longer matches the stored beds, without moving anyone."""
    transfer = db.execute(
        '''INSERT INTO bed_transfers(ref,note,requested_by,status,void_reason)
           VALUES (%s,%s,%s,'voided',%s) RETURNING id''',
        (data.ref, data.note, actor, '住户床位已被其他调床单调整，本单不生效，请刷新床位后重新登记'),
    ).fetchone()
    destinations = _destinations(data)
    for move in data.moves:
        destination = destinations[move.resident_id]
        db.execute(
            '''INSERT INTO bed_transfer_moves(transfer_id,resident_id,from_unit,from_room,to_unit,to_room)
               VALUES (%s,%s,%s,%s,%s,%s)''',
            (transfer['id'], move.resident_id, move.from_unit, move.from_room,
             destination.from_unit, destination.from_room),
        )
    audit(db, actor, 'bed_transfer_voided', 'bed_transfer', transfer['id'], ref=data.ref,
          residents=[move.resident_id for move in data.moves],
          current={row['id']: [row['unit'], row['room']] for row in residents.values()})
    return detail(db, transfer['id'])


def create(db, data, actor):
    """Swap both residents in one transaction, or record why the order does not apply.

    The two resident rows are locked in id order, so a concurrent accept is serialized
    against this swap: whichever commits first decides the outcome. Re-submitting the
    same ref returns the stored result and never swaps twice.
    """
    _lock_ref(db, data.ref)
    existing = db.execute('SELECT id FROM bed_transfers WHERE ref=%s', (data.ref,)).fetchone()
    if existing:
        return detail(db, existing['id'])

    residents = {}
    for resident_id in sorted(move.resident_id for move in data.moves):
        resident = db.execute('SELECT * FROM residents WHERE id=%s FOR UPDATE', (resident_id,)).fetchone()
        if resident is None:
            raise HTTPException(404, '住户不存在')
        residents[resident_id] = resident

    inactive = [row['name'] for row in residents.values() if not row['active']]
    if inactive:
        raise HTTPException(409, f'{inactive[0]} 已暂停照护档案，不能调床')

    destinations = _destinations(data)
    stale = any(
        residents[move.resident_id]['unit'] != move.from_unit
        or residents[move.resident_id]['room'] != move.from_room
        for move in data.moves
    )
    if stale:
        return _void(db, data, actor, residents)

    if len({row['unit'] for row in residents.values()}) != 2:
        raise HTTPException(422, '请选择两个翼区的住户互换床位')

    responses = _active_responses(db, residents)
    transfer = db.execute(
        '''INSERT INTO bed_transfers(ref,note,requested_by,status,applied_at)
           VALUES (%s,%s,%s,'applied',now()) RETURNING id''',
        (data.ref, data.note, actor),
    ).fetchone()
    for move in data.moves:
        destination = destinations[move.resident_id]
        db.execute(
            '''INSERT INTO bed_transfer_moves(transfer_id,resident_id,from_unit,from_room,to_unit,to_room)
               VALUES (%s,%s,%s,%s,%s,%s)''',
            (transfer['id'], move.resident_id, move.from_unit, move.from_room,
             destination.from_unit, destination.from_room),
        )
        db.execute('UPDATE residents SET unit=%s,room=%s WHERE id=%s',
                   (destination.from_unit, destination.from_room, move.resident_id))
        db.execute('''UPDATE bed_transfer_redispatches SET to_unit=%s
                      WHERE resident_id=%s AND status='pending' ''',
                   (destination.from_unit, move.resident_id))

    for response in responses:
        if response['status'] == 'accepted':
            db.execute(
                '''INSERT INTO bed_transfer_kept_responses(transfer_id,response_id,resident_id,assignee)
                   VALUES (%s,%s,%s,%s)''',
                (transfer['id'], response['id'], response['resident_id'], response['assigned_to']),
            )
            audit(db, actor, 'care_response_kept_by_bed_transfer', 'care_response', response['id'],
                  transfer_id=transfer['id'], alert_id=response['alert_id'])
            continue
        db.execute(
            '''UPDATE care_responses SET status='cancelled',finished_at=now(),finish_note=%s
               WHERE id=%s''',
            (f'床位调换：未接单响应撤回，旧班名额已释放，由新翼区重派（调床单 {data.ref}）', response['id']),
        )
        db.execute(
            '''INSERT INTO bed_transfer_redispatches(transfer_id,response_id,alert_id,alert_revision,
                      resident_id,assignee,priority,from_unit,to_unit)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
            (transfer['id'], response['id'], response['alert_id'], response['current_revision'],
             response['resident_id'], response['assigned_to'], response['priority'],
             residents[response['resident_id']]['unit'], destinations[response['resident_id']].from_unit),
        )
        audit(db, actor, 'care_response_withdrawn_by_bed_transfer', 'care_response', response['id'],
              transfer_id=transfer['id'], alert_id=response['alert_id'])

    audit(db, actor, 'bed_transfer_applied', 'bed_transfer', transfer['id'], ref=data.ref,
          residents=[move.resident_id for move in data.moves])
    return detail(db, transfer['id'])


def redispatch(db, transfer_id, redispatch_id, data, actor):
    """Create the replacement response for a withdrawn task in the resident's new wing."""
    item = db.execute(
        'SELECT * FROM bed_transfer_redispatches WHERE id=%s AND transfer_id=%s',
        (redispatch_id, transfer_id),
    ).fetchone()
    if item is None:
        raise HTTPException(404, '待重派响应不存在')
    alert = workflow.lock_alert(db, item['alert_id'])
    item = db.execute('SELECT * FROM bed_transfer_redispatches WHERE id=%s FOR UPDATE',
                      (redispatch_id,)).fetchone()
    if item['status'] != 'pending':
        raise HTTPException(409, '该响应已经重派')
    if alert['status'] != AlertStatus.CONFIRMED:
        raise HTTPException(409, '告警当前不在已复核状态，请先完成复核再重派')
    if db.execute("SELECT 1 FROM care_responses WHERE alert_id=%s AND status IN ('assigned','accepted')",
                  (item['alert_id'],)).fetchone():
        raise HTTPException(409, '该告警已有在办响应，无需再次重派')

    resident = db.execute('SELECT unit FROM residents WHERE id=%s', (item['resident_id'],)).fetchone()
    to_unit = resident['unit']
    minutes = RESPONSE_MINUTES[alert['priority']]
    coverage.reserve(db, to_unit, data.assigned_to, data.coverage_id, minutes)
    try:
        row = db.execute(
            '''INSERT INTO care_responses(alert_id,alert_revision,priority,assigned_to,directed_by,
                      instruction,coverage_id,due_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,now() + make_interval(mins => %s)) RETURNING *''',
            (item['alert_id'], alert['revision'], alert['priority'], data.assigned_to, actor,
             data.instruction, data.coverage_id, minutes),
        ).fetchone()
    except UniqueViolation as exc:
        raise HTTPException(409, '该告警已有在办响应，请刷新后重试') from exc
    db.execute(
        '''UPDATE bed_transfer_redispatches SET status='dispatched',new_response_id=%s,
                  dispatched_at=now(),alert_revision=%s,priority=%s,to_unit=%s WHERE id=%s''',
        (row['id'], alert['revision'], alert['priority'], to_unit, redispatch_id),
    )
    audit(db, actor, 'bed_redispatch_assigned', 'care_response', row['id'], transfer_id=transfer_id,
          previous_response_id=item['response_id'], alert_id=item['alert_id'],
          from_unit=item['from_unit'], to_unit=to_unit)
    return row
