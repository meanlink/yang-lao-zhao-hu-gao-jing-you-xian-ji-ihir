import {roleOptions, eligibility} from './coverageDispatch.js';
import {api} from './store.js';
import {button, empty, escape, heading, panel, pill, SHIFTS, time} from './shared.js';

const transfers = {applied:'已生效', voided:'未生效'};
const redispatches = {pending:'待重派', dispatched:'已重派'};
const responses = {assigned:'待接单', accepted:'处置中', completed:'已处置', cancelled:'已撤销'};

function residentOption(row) {
  return `<option value="${row.id}" data-unit="${escape(row.unit)}" data-room="${escape(row.room)}">${escape(row.room)} · ${escape(row.name)} · ${escape(row.unit)}</option>`;
}

function moveLine(move) {
  const moved = move.current_unit === move.to_unit && move.current_room === move.to_room;
  return `<li><strong>${escape(move.code)} · ${escape(move.resident_name)}</strong>
    <span>${escape(move.from_unit)} ${escape(move.from_room)} → ${escape(move.to_unit)} ${escape(move.to_room)}</span>
    ${moved ? pill('新床位已生效','good') : pill(`当前 ${move.current_room}`,'warn')}</li>`;
}

function transferPanel({residents, lead}) {
  if (!lead) return '';
  return `<section class="paper ops-create">${panel('登记调床单','选择两个翼区各一位住户，提交后两人床位一次生效。')}
    <form class="stack ops-action" data-do="createTransfer">
      <label>调床单编号<input name="ref" required pattern="[A-Z0-9][A-Z0-9\\-]{4,39}" placeholder="BT-20261124-01"></label>
      <div class="paired-fields"><label>住户甲<select name="resident_a" data-change="transfer-resident" required>
        <option value="">选择床位</option>${residents.map(residentOption).join('')}</select>
        <input type="hidden" name="a_unit" data-slot="a_unit"><input type="hidden" name="a_room" data-slot="a_room"></label>
      <label>住户乙<select name="resident_b" data-change="transfer-resident" required>
        <option value="">选择床位</option>${residents.map(residentOption).join('')}</select>
        <input type="hidden" name="b_unit" data-slot="b_unit"><input type="hidden" name="b_room" data-slot="b_room"></label></div>
      <label>调床说明<textarea name="note" rows="2" minlength="3" maxlength="600" required placeholder="说明互换原因和生效时间"></textarea></label>
      ${button('提交调床单')}</form></section>`;
}

function redispatchBlock(item, {lead, carers}) {
  const form = lead && item.status === 'pending' ? redispatchForm(item, carers) : '';
  const done = item.status === 'dispatched' ? `<p class="ops-time">新响应 #${item.new_response_id} · ${escape(item.new_assignee_name || '')}
    · ${responses[item.new_response_status] || ''} · <a class="text-link" href="#responses/${item.alert_id}">查看响应</a></p>` : '';
  return `<article class="ops-item"><div class="ops-item-head"><div><h3>${escape(item.current_room)} · ${escape(item.resident_name)}</h3>
    <p>原响应 #${item.response_id} · 原接单人 ${escape(item.assignee_name)} · ${escape(item.from_unit)} → ${escape(item.to_unit)}</p></div>
    <div class="data-line">${pill(redispatches[item.status],item.status === 'pending' ? 'warn' : 'good')}</div></div>
    ${item.status === 'pending' ? `<p class="ops-time">原响应未接单，已撤回并释放旧班名额。请在新翼区 ${escape(item.to_unit)} 重新指定照护员。</p>` : ''}
    ${done}${form}</article>`;
}

function redispatchForm(item, carers) {
  const managed = item.managed;
  const choices = managed ? item.coverage.filter(row => eligibility(row)[1] === 'good') : carers;
  const preview = managed ? `<div class="coverage-dispatch-meta"><p>${escape(item.current_unit)} · 需覆盖 ${item.sla_minutes} 分钟</p>
    ${item.coverage.filter(row => row.status === 'active').map(row => `<div class="data-line"><span>${escape(row.carer_name)} · ${row.active_count}/${row.capacity}</span>${pill(...eligibility(row))}</div>`).join('')}</div>` : '';
  if (!choices.length) {
    return `<div class="note warn dispatch-empty">${managed ? '新翼区当前没有能覆盖时限且有空余容量的照护员。' : '暂无可用照护员。'}</div>`;
  }
  const opts = choices.map(row => [managed ? `${row.carer_id}:${row.id}` : row.id,
    managed ? `${row.carer_name} · ${SHIFTS[row.shift]} · 至 ${time(row.ends_at)}` : row.name]);
  return `${preview}<form class="stack ops-action" data-do="assignRedispatch" data-id="${item.transfer_id}" data-item="${item.id}">
    <label>新翼区照护员<select name="assigned_to" required>${roleOptions(opts)}</select></label>
    <label>处置要求<textarea name="instruction" rows="2" minlength="3" maxlength="600" required placeholder="说明撤回原因与新翼区的处置要求"></textarea></label>
    ${button('重新派单')}</form>`;
}

function transferEntry(row, context) {
  const voided = row.status === 'voided';
  return `<article class="ops-item"><div class="ops-item-head"><div><h3>调床单 ${escape(row.ref)}</h3>
    <p>${escape(row.requested_name)} · ${time(row.created_at)}</p></div>
    <div class="data-line">${pill(transfers[row.status],voided ? 'warn' : 'good')}</div></div>
    <p class="ops-copy">${escape(row.note)}</p>
    ${voided ? `<div class="note warn"><strong>本单不生效</strong><p>${escape(row.void_reason)}</p></div>` : ''}
    <ul class="ops-timeline" aria-label="床位变更">${row.moves.map(moveLine).join('')}</ul>
    ${row.kept_responses.length ? `<div class="note good"><strong>已接单响应继续由原照护员办完</strong>
      <p>${row.kept_responses.map(k => `${escape(k.resident_name)} · ${escape(k.assignee_name)} · 响应 #${k.response_id}（${responses[k.response_status] || escape(k.response_status)}）`).join('<br>')}</p></div>` : ''}
    ${row.redispatches.length ? `<div class="ops-links"><strong>响应重派</strong></div>
      ${row.redispatches.map(item => redispatchBlock({...item, ...(context.pending.find(p => p.id === item.id) || {})}, context)).join('')}` : ''}
  </article>`;
}

export async function bedTransfersPage(state, transferId) {
  const data = await api('/bed-transfers');
  const items = transferId ? data.items.filter(row => row.id === transferId) : data.items;
  const shown = items.length ? items : data.items;
  const pending = data.pending_redispatches;
  const lead = state.user.role === 'lead';
  const context = {lead, carers: data.carers, pending};
  return `${heading('床位与翼区','床位调换','两个翼区各选一位住户互换床位。提交后两人同时调换；若一方已被其他调床单移走，本单不生效。')}
    <div class="ops-layout"><div class="stack">
      <section class="paper">${panel('待重派响应',`${pending.length} 条未接单响应等待新翼区派单`)}
        ${pending.map(item => `<article class="ops-item"><div class="ops-item-head"><div><h3>${escape(item.current_room)} · ${escape(item.resident_name)}</h3>
          <p>调床单 ${escape(item.transfer_ref)} · ${escape(item.from_unit)} → ${escape(item.to_unit)}</p></div>
          ${pill('待重派','warn')}</div>${lead ? redispatchForm(item, data.carers) : '<p class="ops-time">等待值班长在新翼区重新指定照护员。</p>'}</article>`).join('') || empty('暂无待重派响应。')}</section>
      <section class="paper">${panel('调床记录',`${shown.length} 份调床单`)}
        ${shown.map(row => transferEntry(row, context)).join('') || empty('暂无调床单。')}</section>
    </div>${transferPanel({residents:data.residents, lead})}</div>`;
}

function selectedMove(data, prefix) {
  return {resident_id: Number(data.get(`resident_${prefix}`)), from_unit: data.get(`${prefix}_unit`),
    from_room: data.get(`${prefix}_room`)};
}

export const bedTransferActions = {
  async createTransfer(_form, data) {
    const moves = [selectedMove(data,'a'), selectedMove(data,'b')];
    if (moves.some(move => !move.resident_id || !move.from_unit || !move.from_room)) {
      throw new Error('请选择两个翼区的住户床位');
    }
    if (moves[0].resident_id === moves[1].resident_id) throw new Error('请选择两位不同的住户');
    await api('/bed-transfers','POST',{ref:data.get('ref'), note:data.get('note'), moves});
    return '调床单已处理，请核对两人床位与待重派响应';
  },
  async assignRedispatch(form, data) {
    const [assignee, coverage] = data.get('assigned_to').split(':');
    const payload = {assigned_to:Number(assignee), instruction:data.get('instruction')};
    if (coverage) payload.coverage_id = Number(coverage);
    await api(`/bed-transfers/${form.dataset.id}/redispatches/${form.dataset.item}/assign`,'POST',payload);
    return '已按新翼区重新派单';
  },
};

export function transferResidentChange(form, select) {
  const option = select.selectedOptions[0];
  const prefix = select.name === 'resident_a' ? 'a' : 'b';
  form.elements[`${prefix}_unit`].value = option?.dataset.unit || '';
  form.elements[`${prefix}_room`].value = option?.dataset.room || '';
}
