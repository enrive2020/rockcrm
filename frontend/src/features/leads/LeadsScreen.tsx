import { useCallback, useEffect, useState } from 'react';
import {
  ApiError,
  FLAG_IS_BAD,
  FLAG_LABELS,
  LOST_REASONS,
  LOST_REASON_LABELS,
  SOURCES,
  SOURCE_LABELS,
  STAGE_LABELS,
  api,
  type BoardLead,
  type LeadSource,
  type LeadStage,
  type LeadsBoard,
  type LostReason,
} from '../../api';
import { useAsync } from '../../lib/useAsync';
import { plural, wallTime } from '../../lib/format';
import { TODAY } from '../../lib/today';
import { EmptyState, ErrorState, ListSkeleton } from '../../components/States';
import type { ToastMessage } from '../../components/Toasts';
import { LeadPanel } from './LeadPanel';
import { NewLeadDialog } from './NewLeadDialog';
import { FunnelReportScreen } from './FunnelReportScreen';

type Toast = Omit<ToastMessage, 'id'>;

/**
 * Экран «Заявки»: доска воронки и отчёт по ней.
 *
 * Колонка — стадия, карточка — заявка. Внутри колонки сверху то, что ждёт
 * дольше: доска показывает очередь работы, а не просто список. Перетаскивание
 * между колонками применяется сразу, а при отказе сервера откатывается —
 * иначе администратор увидит стадию, которой в базе нет.
 */
export function LeadsScreen({
  branchId,
  onOpenStudent,
  onToast,
}: {
  branchId: string | null;
  onOpenStudent: (studentId: string) => void;
  onToast: (toast: Toast) => void;
}) {
  const [tab, setTab] = useState<'board' | 'funnel'>('board');
  const [source, setSource] = useState<LeadSource | ''>('');
  const [selected, setSelected] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [dragging, setDragging] = useState<string | null>(null);
  /** Заявка, которую перетащили в «Отказ»: ждём причину, без неё сервер откажет. */
  const [askReason, setAskReason] = useState<{ leadId: string; name: string } | null>(null);

  const board = useAsync<LeadsBoard>(
    () => api.leads({ branch_id: branchId, ...(source ? { source } : {}) }),
    [branchId, source],
  );

  // Локальная копия доски нужна для оптимистичного переноса карточки:
  // ответа сервера ждать нельзя — перетаскивание должно выглядеть мгновенным
  const [local, setLocal] = useState<LeadsBoard | null>(null);
  useEffect(() => setLocal(board.data), [board.data]);

  const move = useCallback(
    async (leadId: string, to: LeadStage, lostReason?: LostReason) => {
      const snapshot = local;
      if (!snapshot) return;
      const lead = findLead(snapshot, leadId);
      if (!lead || currentStage(snapshot, leadId) === to) return;

      setLocal(moveInBoard(snapshot, leadId, to));
      try {
        await api.patchLead(leadId, { stage: to, ...(lostReason ? { lost_reason: lostReason } : {}) });
        board.reload();
      } catch (error) {
        // Откат: показанная стадия обязана совпадать с той, что в базе
        setLocal(snapshot);
        onToast({
          title: `Стадию сменить не удалось · ${lead.name}`,
          tone: 'bad',
          rows: [{ label: 'Сервер', value: error instanceof ApiError ? error.message : 'Непредвиденная ошибка.' }],
        });
      }
    },
    [local, board, onToast],
  );

  const onDrop = (leadId: string, to: LeadStage) => {
    setDragging(null);
    if (!local || currentStage(local, leadId) === to) return;
    if (to === 'lost') {
      const lead = findLead(local, leadId);
      setAskReason({ leadId, name: lead?.name ?? 'Заявка' });
      return;
    }
    void move(leadId, to);
  };

  const summary = local?.summary;

  return (
    <section className="screen">
      <div className="tl-head">
        <div>
          <h1 className="h1">Заявки</h1>
          <p className="sub">
            Заявки из Telegram-бота, формы сайта, WhatsApp и Instagram попадают сюда автоматически
            {summary ? ` · всего ${summary.total}` : ''}
          </p>
        </div>
        <div className="spacer" />
        <div className="seg-ctl">
          <button aria-pressed={tab === 'board'} onClick={() => setTab('board')}>
            Доска
          </button>
          <button aria-pressed={tab === 'funnel'} onClick={() => setTab('funnel')}>
            Отчёт по воронке
          </button>
        </div>
        {tab === 'board' && (
          <button className="btn pri narrow" onClick={() => setCreating(true)}>
            + Заявка
          </button>
        )}
      </div>

      {tab === 'funnel' ? (
        <FunnelReportScreen />
      ) : (
        <>
          <div className="legend">
            <label className="filter">
              Источник
              <select
                className="inp slim"
                value={source}
                onChange={(event) => setSource(event.target.value as LeadSource | '')}
              >
                <option value="">Все</option>
                {SOURCES.map((s) => (
                  <option key={s} value={s}>
                    {SOURCE_LABELS[s]}
                  </option>
                ))}
              </select>
            </label>
            {summary && (
              <>
                <span>
                  Конверсия пробный → абонемент <b className="num">{summary.conversion_trial_to_won_pct}%</b>
                </span>
                <span>
                  Средний путь до оплаты <b className="num">{summary.avg_days_to_won}</b> дн.
                </span>
                {summary.overdue > 0 && (
                  <span className="pill bad">
                    <i className="dot" />
                    {summary.overdue} {plural(summary.overdue, 'просрочена', 'просрочены', 'просрочено')}
                  </span>
                )}
              </>
            )}
            <span>Карточку можно перетащить в другую колонку · клик открывает заявку</span>
          </div>

          {board.loading && !local && (
            <div className="tl-wrap">
              <ListSkeleton rows={5} />
            </div>
          )}
          {!board.loading && board.error && (
            <ErrorState error={board.error} onRetry={board.reload} title="Воронка не загрузилась" />
          )}
          {local && local.summary.total === 0 && !board.loading && (
            <EmptyState label="Воронка пуста" title="Заявок нет">
              Ни одной заявки по выбранным условиям. Смените источник или филиал — либо заведите заявку вручную
              кнопкой «+ Заявка», когда позвонят.
            </EmptyState>
          )}
          {local && local.summary.total > 0 && (
            <div className="kb-wrap">
              <div className="kb">
                {local.columns.map((column) => (
                  <div
                    className={dragging ? 'col drop' : 'col'}
                    key={column.stage}
                    onDragOver={(event) => event.preventDefault()}
                    onDrop={(event) => {
                      event.preventDefault();
                      const id = event.dataTransfer.getData('text/plain');
                      if (id) onDrop(id, column.stage);
                    }}
                  >
                    <div className="col-h">
                      <b>{column.title}</b>
                      <span className="n num">{column.count}</span>
                    </div>
                    {column.leads.map((lead) => (
                      <LeadCardMini
                        key={lead.id}
                        lead={lead}
                        selected={lead.id === selected}
                        onOpen={() => setSelected(lead.id)}
                        onDragStart={() => setDragging(lead.id)}
                        onDragEnd={() => setDragging(null)}
                      />
                    ))}
                    {column.leads.length === 0 && <p className="col-empty">Пусто</p>}
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}

      <LeadPanel
        leadId={selected}
        onClose={() => setSelected(null)}
        onChanged={() => board.reload()}
        onOpenStudent={onOpenStudent}
        onToast={onToast}
      />

      {creating && (
        <NewLeadDialog
          branchId={branchId}
          onClose={() => setCreating(false)}
          onCreated={(lead) => {
            setCreating(false);
            onToast({
              title: 'Заявка заведена',
              rows: [
                { label: 'Кто', value: lead.name },
                { label: 'Телефон', value: lead.phone ?? '—' },
                { label: 'Стадия', value: STAGE_LABELS[lead.stage] },
              ],
            });
            board.reload();
            setSelected(lead.id);
          }}
        />
      )}

      {askReason && (
        <LostReasonDialog
          name={askReason.name}
          onClose={() => setAskReason(null)}
          onPick={(reason) => {
            const { leadId } = askReason;
            setAskReason(null);
            void move(leadId, 'lost', reason);
          }}
        />
      )}
    </section>
  );
}

function LeadCardMini({
  lead,
  selected,
  onOpen,
  onDragStart,
  onDragEnd,
}: {
  lead: BoardLead;
  selected: boolean;
  onOpen: () => void;
  onDragStart: () => void;
  onDragEnd: () => void;
}) {
  const title = lead.student_name ?? lead.name;
  const age = lead.student_age ? `${lead.student_age} ${plural(lead.student_age, 'год', 'года', 'лет')}` : null;

  return (
    <button
      className="lead"
      aria-pressed={selected}
      draggable
      onDragStart={(event) => {
        event.dataTransfer.setData('text/plain', lead.id);
        event.dataTransfer.effectAllowed = 'move';
        onDragStart();
      }}
      onDragEnd={onDragEnd}
      onClick={onOpen}
    >
      <b>{title}</b>
      <small>
        {[age, lead.discipline].filter(Boolean).join(' · ') || 'направление не указано'}
      </small>
      {lead.trial && (
        <small className="num">
          {lead.trial.teacher.split(' ')[1] ?? lead.trial.teacher} · {trialWhen(lead.trial.starts_at)}
        </small>
      )}
      <div className="row">
        <span className="src">{SOURCE_LABELS[lead.source]}</span>
        <span className="pill mute">{lead.waiting_for}</span>
        {lead.flags.map((flag) => (
          <span className={FLAG_IS_BAD[flag] ? 'pill bad' : 'pill acc'} key={flag}>
            <i className="dot" />
            {FLAG_LABELS[flag]}
            {flag === 'no_answer' && lead.contact_attempts > 0 ? ` ×${lead.contact_attempts}` : ''}
          </span>
        ))}
      </div>
    </button>
  );
}

/** Причину отказа спрашиваем до запроса: без неё сервер всё равно откажет. */
function LostReasonDialog({
  name,
  onClose,
  onPick,
}: {
  name: string;
  onClose: () => void;
  onPick: (reason: LostReason) => void;
}) {
  return (
    <div className="ovl" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="dlg narrow" role="dialog" aria-modal="true" aria-label="Причина отказа">
        <div className="insp-h">
          <div>
            <h2>Причина отказа</h2>
            <p>{name}</p>
          </div>
          <button className="x" onClick={onClose} title="Закрыть">
            ✕
          </button>
        </div>
        <div className="dlg-b">
          <p className="hint" style={{ marginTop: 0 }}>
            Без причины отказ не сохранится: именно она показывает, что чинить — цену, расписание или дозвон.
          </p>
          <div className="marks">
            {LOST_REASONS.map((reason) => (
              <button className="mark" key={reason} onClick={() => onPick(reason)}>
                {LOST_REASON_LABELS[reason]}
              </button>
            ))}
          </div>
        </div>
        <div className="insp-f">
          <button className="btn" onClick={onClose}>
            Отмена
          </button>
        </div>
      </div>
    </div>
  );
}

const trialWhen = (startsAt: string): string =>
  `${startsAt.slice(0, 10) === TODAY ? 'сегодня' : startsAt.slice(8, 10) + '.' + startsAt.slice(5, 7)} ${wallTime(startsAt)}`;

/* ---------- операции над локальной копией доски ---------- */

const findLead = (board: LeadsBoard, leadId: string): BoardLead | undefined =>
  board.columns.flatMap((c) => c.leads).find((l) => l.id === leadId);

const currentStage = (board: LeadsBoard, leadId: string): LeadStage | null =>
  board.columns.find((c) => c.leads.some((l) => l.id === leadId))?.stage ?? null;

/** Перенос карточки в другую колонку до ответа сервера. */
function moveInBoard(board: LeadsBoard, leadId: string, to: LeadStage): LeadsBoard {
  const lead = findLead(board, leadId);
  if (!lead) return board;
  return {
    ...board,
    columns: board.columns.map((column) => {
      const without = column.leads.filter((l) => l.id !== leadId);
      const leads = column.stage === to ? [...without, lead] : without;
      return { ...column, leads, count: leads.length };
    }),
  };
}
