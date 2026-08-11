import { useCallback, useState } from 'react';
import {
  ApiError,
  LOST_REASONS,
  LOST_REASON_LABELS,
  SOURCE_LABELS,
  STAGE_LABELS,
  STAGE_ORDER,
  api,
  type LeadCard,
  type LeadStage,
  type LostReason,
} from '../../api';
import { useAsync } from '../../lib/useAsync';
import { dayMonth, plural, wallTime } from '../../lib/format';
import { TODAY } from '../../lib/today';
import { ErrorState } from '../../components/States';
import type { ToastMessage } from '../../components/Toasts';
import { TrialDialog } from './TrialDialog';
import { ConvertDialog } from './ConvertDialog';

type Toast = Omit<ToastMessage, 'id'>;

/**
 * Карточка заявки. Открывается сбоку, чтобы доска оставалась перед глазами:
 * администратор ведёт разговор по телефону и параллельно смотрит, что ещё
 * висит в работе.
 *
 * Порядок блоков — порядок разговора: кто звонит и по какому номеру → что
 * с пробным → что делать дальше → как заявка сюда попала.
 */
export function LeadPanel({
  leadId,
  onClose,
  onChanged,
  onOpenStudent,
  onToast,
}: {
  leadId: string | null;
  onClose: () => void;
  onChanged: () => void;
  onOpenStudent: (studentId: string) => void;
  onToast: (toast: Toast) => void;
}) {
  const card = useAsync<LeadCard>(() => api.lead(leadId as string), [leadId], leadId !== null);
  const [dialog, setDialog] = useState<'trial' | 'convert' | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const lead = card.data;

  const patch = useCallback(
    async (payload: Parameters<typeof api.patchLead>[1], toast?: Toast) => {
      if (!leadId) return;
      setBusy(true);
      setError(null);
      try {
        await api.patchLead(leadId, payload);
        card.reload();
        onChanged();
        if (toast) onToast(toast);
      } catch (err) {
        setError(err instanceof ApiError ? err : new ApiError(0, 'unexpected', 'Не получилось сохранить изменение.'));
      } finally {
        setBusy(false);
      }
    },
    [leadId, card, onChanged, onToast],
  );

  const open = leadId !== null;

  return (
    <aside className={open ? 'insp on' : 'insp'} aria-hidden={!open} aria-label="Заявка">
      {open && card.loading && <PanelSkeleton onClose={onClose} />}
      {open && !card.loading && card.error && (
        <div className="insp-b">
          <ErrorState error={card.error} onRetry={card.reload} title="Заявка не загрузилась" />
          <button className="btn" onClick={onClose}>
            Закрыть
          </button>
        </div>
      )}

      {open && !card.loading && !card.error && lead && (
        <>
          <div className="insp-h">
            <div>
              <h2>{lead.student_name ?? lead.name}</h2>
              <p>
                {STAGE_LABELS[lead.stage]}
                {lead.lost_reason ? ` · ${LOST_REASON_LABELS[lead.lost_reason]}` : ''} · {SOURCE_LABELS[lead.source]}
              </p>
            </div>
            <button className="x" onClick={onClose} title="Закрыть (Esc)">
              ✕
            </button>
          </div>

          <div className="insp-b">
            <AgeWarning lead={lead} />

            <div className="blk">
              <span className="lbl">Контакты</span>
              <div className="kv">
                <span>Кто оставил</span>
                <b>{lead.name}</b>
              </div>
              <div className="kv">
                <span>Телефон</span>
                <b className="num">
                  {lead.phone ? (
                    <a className="link" href={`tel:${lead.phone}`}>
                      {lead.phone}
                    </a>
                  ) : (
                    '—'
                  )}
                </b>
              </div>
              {lead.student_age !== null && (
                <div className="kv">
                  <span>Ученик</span>
                  <b>
                    {lead.student_name ?? '—'}, {lead.student_age}{' '}
                    {plural(lead.student_age, 'год', 'года', 'лет')}
                  </b>
                </div>
              )}
              <div className="kv">
                <span>Направление</span>
                <b>{lead.discipline?.name ?? 'не указано'}</b>
              </div>
              <div className="kv">
                <span>Филиал</span>
                <b>{lead.branch?.name ?? '—'}</b>
              </div>
              <div className="kv">
                <span>Ответственный</span>
                <b>{lead.assigned_to?.name ?? 'не назначен'}</b>
              </div>
              {lead.comment && (
                <div className="rule" style={{ borderStyle: 'solid' }}>
                  {lead.comment}
                </div>
              )}
              {lead.phone && (
                <a
                  className="btn wide"
                  href={`https://wa.me/${lead.phone.replace(/\D/g, '')}`}
                  target="_blank"
                  rel="noreferrer"
                >
                  Написать в WhatsApp
                </a>
              )}
            </div>

            <div className="blk">
              <span className="lbl">Пробный урок</span>
              {lead.trial ? (
                <div className={lead.trial.conflicts && lead.trial.conflicts.length > 0 ? 'rule warn' : 'rule applied'}>
                  <strong>
                    {dayMonth(lead.trial.starts_at.slice(0, 10))}
                    {lead.trial.starts_at.slice(0, 10) === TODAY ? ' (сегодня)' : ''} в {wallTime(lead.trial.starts_at)}
                  </strong>
                  <ul>
                    <li>
                      <span>Преподаватель</span>
                      <em>{lead.trial.teacher}</em>
                    </li>
                    <li>
                      <span>Кабинет</span>
                      <em>{lead.trial.room}</em>
                    </li>
                  </ul>
                  {lead.trial.conflicts?.map((conflict) => (
                    <p className="warn-note" key={conflict.with_lesson_id}>
                      {conflict.message}. Занятие стоит в занятом кабинете — предупредите преподавателя.
                    </p>
                  ))}
                </div>
              ) : (
                <div className="rule">Пробный не назначен. Пока его нет, заявка не двигается дальше дозвона.</div>
              )}
              <div className="actions">
                <button className="btn" onClick={() => setDialog('trial')} disabled={busy}>
                  {lead.trial ? 'Перенести пробный' : 'Назначить пробный'}
                </button>
                <button
                  className="btn pri"
                  onClick={() => setDialog('convert')}
                  disabled={busy || Boolean(lead.converted.student_id)}
                >
                  Оформить ученика
                </button>
              </div>
              {lead.converted.student_id && (
                <button className="btn wide" onClick={() => onOpenStudent(lead.converted.student_id as string)}>
                  Открыть карточку ученика
                </button>
              )}
            </div>

            <div className="blk">
              <span className="lbl">Работа с заявкой</span>
              <div className="kv">
                <span>Попыток дозвона</span>
                <b className="num">{lead.contact_attempts}</b>
              </div>
              <div className="kv">
                <span>Напоминание</span>
                <b className="num">
                  {lead.next_action_at
                    ? `${dayMonth(lead.next_action_at.slice(0, 10))} ${wallTime(lead.next_action_at)}`
                    : 'нет'}
                </b>
              </div>
              <div className="actions">
                <button
                  className="btn"
                  disabled={busy}
                  onClick={() =>
                    patch({ contact_attempts: lead.contact_attempts + 1 }, {
                      title: 'Записана попытка дозвона',
                      rows: [{ label: 'Всего попыток', value: String(lead.contact_attempts + 1) }],
                    })
                  }
                >
                  + Не дозвонились
                </button>
                <button
                  className="btn"
                  disabled={busy}
                  onClick={() =>
                    patch({ next_action_at: `${TODAY}T18:00:00+06:00` }, {
                      title: 'Напоминание поставлено',
                      rows: [{ label: 'Перезвонить', value: 'сегодня в 18:00' }],
                    })
                  }
                >
                  Перезвонить в 18:00
                </button>
                {lead.next_action_at && (
                  <button className="btn" disabled={busy} onClick={() => patch({ next_action_at: null })}>
                    Снять напоминание
                  </button>
                )}
              </div>

              <label className="field" style={{ marginTop: 12 }}>
                <span>Стадия</span>
                <select
                  className="inp"
                  value={lead.stage}
                  disabled={busy}
                  onChange={(event) => {
                    const next = event.target.value as LeadStage;
                    if (next === 'lost') return; // причину спрашиваем отдельно, ниже
                    void patch({ stage: next });
                  }}
                >
                  {[...STAGE_ORDER, 'lost' as LeadStage].map((stage) => (
                    <option key={stage} value={stage}>
                      {STAGE_LABELS[stage]}
                    </option>
                  ))}
                </select>
              </label>

              {lead.stage !== 'lost' && lead.stage !== 'won' && (
                <label className="field" style={{ marginTop: 10 }}>
                  <span>Отказ — с причиной</span>
                  <select
                    className="inp"
                    value=""
                    disabled={busy}
                    onChange={(event) => {
                      const reason = event.target.value as LostReason;
                      if (!reason) return;
                      void patch({ stage: 'lost', lost_reason: reason }, {
                        title: 'Заявка закрыта отказом',
                        rows: [{ label: 'Причина', value: LOST_REASON_LABELS[reason] }],
                      });
                    }}
                  >
                    <option value="">Выберите причину…</option>
                    {LOST_REASONS.map((reason) => (
                      <option key={reason} value={reason}>
                        {LOST_REASON_LABELS[reason]}
                      </option>
                    ))}
                  </select>
                </label>
              )}
            </div>

            <div className="blk">
              <span className="lbl">История стадий</span>
              <ol className="hist">
                {lead.history.map((step, index) => (
                  <li key={`${step.at}-${index}`}>
                    <time className="num">
                      {dayMonth(step.at.slice(0, 10))} {wallTime(step.at)}
                    </time>
                    <b>{STAGE_LABELS[step.to]}</b>
                    <small>{step.by ?? 'система'}</small>
                  </li>
                ))}
              </ol>
              <p className="hint">
                История пишется на каждый переход: по ней считается конверсия и время простоя, а не по текущей стадии.
              </p>
            </div>

            <div className="blk">
              <span className="lbl">Откуда пришла</span>
              <div className="kv">
                <span>Источник</span>
                <b>{SOURCE_LABELS[lead.source]}</b>
              </div>
              <div className="kv">
                <span>Создана</span>
                <b className="num">
                  {dayMonth(lead.created_at.slice(0, 10))} {wallTime(lead.created_at)}
                </b>
              </div>
              {lead.promo_code && (
                <div className="kv">
                  <span>Промокод</span>
                  <b>{lead.promo_code}</b>
                </div>
              )}
              {Object.entries(lead.utm).map(([key, value]) => (
                <div className="kv" key={key}>
                  <span>{key}</span>
                  <b>{value}</b>
                </div>
              ))}
            </div>

            {error && (
              <div className="err-inline" role="alert">
                <strong>{error.message}</strong>
              </div>
            )}
          </div>
        </>
      )}

      {dialog === 'trial' && lead && (
        <TrialDialog
          lead={lead}
          onClose={() => setDialog(null)}
          onBooked={(toast) => {
            setDialog(null);
            onToast(toast);
            card.reload();
            onChanged();
          }}
        />
      )}
      {dialog === 'convert' && lead && (
        <ConvertDialog
          lead={lead}
          onClose={() => setDialog(null)}
          onConverted={(toast, studentId) => {
            setDialog(null);
            onToast(toast);
            card.reload();
            onChanged();
            onOpenStudent(studentId);
          }}
        />
      )}
    </aside>
  );
}

/**
 * Возраст ниже минимального для направления — предупреждение, а не запрет:
 * решает администратор, а не система. Порог приходит с сервера; если его
 * не прислали, предупреждать не о чем.
 */
function AgeWarning({ lead }: { lead: LeadCard }) {
  const minAge = lead.discipline?.min_age;
  if (!minAge || lead.student_age === null || lead.student_age >= minAge) return null;

  return (
    <div className="err-inline" role="status" style={{ marginTop: 0, marginBottom: 16 }}>
      <strong>
        {lead.student_name ?? 'Ученику'} {lead.student_age} {plural(lead.student_age, 'год', 'года', 'лет')}, а на
        «{lead.discipline?.name}» берут с {minAge}.
      </strong>{' '}
      Это не запрет: если преподаватель согласен — назначайте пробный.
    </div>
  );
}

function PanelSkeleton({ onClose }: { onClose: () => void }) {
  return (
    <>
      <div className="insp-h">
        <div style={{ flex: 1 }}>
          <div className="skeleton" style={{ width: '60%', height: 18, marginBottom: 6 }} />
          <div className="skeleton" style={{ width: '40%', height: 12 }} />
        </div>
        <button className="x" onClick={onClose} title="Закрыть">
          ✕
        </button>
      </div>
      <div className="insp-b" aria-busy="true">
        <div className="skeleton" style={{ height: 150, marginBottom: 14 }} />
        <div className="skeleton" style={{ height: 180 }} />
      </div>
    </>
  );
}
