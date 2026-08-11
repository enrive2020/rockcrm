import { useState, type CSSProperties } from 'react';
import {
  api,
  CHURN_LABELS,
  type ChurnRisk,
  type Family,
  type LedgerEntry,
  type MakeupCredit,
  type StudentCard,
  type StudentNote,
  type StudentSubscription,
  type SubscriptionRules,
} from '../../api';
import { useAsync } from '../../lib/useAsync';
import { dateGen, dayMonth, daysWord, initials, lessonsWord, money, plural } from '../../lib/format';
import { CardSkeleton, ErrorState } from '../../components/States';
import type { ToastMessage } from '../../components/Toasts';
import { SellDialog } from './SellDialog';
import { FreezeDialog } from './FreezeDialog';

type Toast = Omit<ToastMessage, 'id'>;

/**
 * Карточка ученика по мотивам экрана «Ученик» из прототипа.
 *
 * Порядок блоков повторяет порядок вопросов на ресепшене: сколько занятий
 * осталось → что говорил преподаватель → куда делись занятия. Справа то,
 * что нужно для разговора с родителем: кто платит, по каким правилам
 * и насколько велик риск, что семья не продлит.
 */
export function StudentCardScreen({
  studentId,
  onBack,
  onOpenStudent,
  onToast,
  canSell = true,
}: {
  studentId: string;
  onBack: () => void;
  onOpenStudent: (id: string) => void;
  onToast: (toast: Toast) => void;
  /**
   * Продажа, продление и заморозка требуют роли администратора
   * (`require_admin`). Преподавателю и родителю кнопок здесь быть не должно:
   * они дадут 403 после заполнения формы — то есть после того, как сумма
   * уже названа родителю вслух.
   */
  canSell?: boolean;
}) {
  const card = useAsync<StudentCard>(() => api.student(studentId), [studentId]);
  const [dialog, setDialog] = useState<'sell' | 'freeze' | null>(null);
  const student = card.data;

  return (
    <section className="screen">
      <div className="tl-head">
        <button className="btn back" onClick={onBack}>
          ← Назад
        </button>
      </div>

      {card.loading && <CardSkeleton />}
      {!card.loading && card.error && (
        <ErrorState error={card.error} onRetry={card.reload} title="Карточка ученика не загрузилась" />
      )}

      {!card.loading && !card.error && student && (
        <div className="pad">
          <header className="stu-head">
            <div className="chip big" style={{ '--ch': 'var(--ch-clay)' } as CSSProperties}>
              {initials(student.name)}
            </div>
            <div>
              <h1 className="h1">{student.name}</h1>
              <p className="sub">
                {student.age} {plural(student.age, 'год', 'года', 'лет')} · {student.discipline} · {student.teacher} ·{' '}
                {student.branch} · с {dateGen(student.started_on)} {student.started_on.slice(0, 4)}
              </p>
            </div>
            <div className="spacer" />
            <span className={student.status === 'active' ? 'pill ok' : 'pill mute'}>
              <i className="dot" />
              {student.status === 'active' ? 'Занимается' : student.status}
            </span>
          </header>

          <div className="grid g-side">
            <div className="col-stack">
              <SubscriptionCard
                subscription={student.subscription}
                canSell={canSell}
                onSell={() => setDialog('sell')}
                onFreeze={() => setDialog('freeze')}
                onReleaseHold={async (holdId) => {
                  if (!student.subscription) return;
                  try {
                    const result = await api.releaseHold(student.subscription.id, holdId);
                    onToast({
                      title: 'Заморозка снята',
                      rows: [
                        { label: 'Действует до', value: result.valid_until_after ? dateGen(result.valid_until_after) : '—' },
                        {
                          label: 'Занятия',
                          value: `${result.lessons_cancelled ?? 0} отменённых не восстановлены — поставьте заново`,
                        },
                      ],
                    });
                    card.reload();
                  } catch (error) {
                    onToast({
                      title: 'Заморозку снять не удалось',
                      tone: 'bad',
                      rows: [{ label: 'Ошибка', value: (error as Error).message }],
                    });
                  }
                }}
              />
              <NotesCard notes={student.notes} />
              <LedgerCard ledger={student.ledger} />
            </div>

            <div className="col-stack">
              <FamilyCard family={student.family} currentId={student.id} onOpenStudent={onOpenStudent} />
              {student.subscription && <RulesCard subscription={student.subscription} />}
              <MakeupsCard makeups={student.makeups} />
              <ChurnCard risk={student.churn_risk} />
            </div>
          </div>
        </div>
      )}

      {dialog === 'sell' && student && (
        <SellDialog
          student={student}
          onClose={() => setDialog(null)}
          onSold={(toast) => {
            setDialog(null);
            onToast(toast);
            card.reload();
          }}
        />
      )}
      {dialog === 'freeze' && student && student.subscription && (
        <FreezeDialog
          studentName={student.name}
          subscription={student.subscription}
          onClose={() => setDialog(null)}
          onFrozen={(toast) => {
            setDialog(null);
            onToast(toast);
            card.reload();
          }}
        />
      )}
    </section>
  );
}

/* ---------- абонемент ---------- */

function SubscriptionCard({
  subscription,
  canSell,
  onSell,
  onFreeze,
  onReleaseHold,
}: {
  subscription: StudentSubscription | null;
  canSell: boolean;
  onSell: () => void;
  onFreeze: () => void;
  onReleaseHold: (holdId: string) => void;
}) {
  if (!subscription) {
    return (
      <div className="card">
        <span className="lbl">Абонемент</span>
        <div className="stat">Нет</div>
        <p className="trend">
          Действующего абонемента нет — занятия идут разовой оплатой, и остаток списывать неоткуда.
        </p>
        {canSell && (
          <button className="btn pri" style={{ flex: 'none', marginTop: 12 }} onClick={onSell}>
            Продать абонемент
          </button>
        )}
      </div>
    );
  }

  const s = subscription;
  return (
    <div className="card">
      <span className="lbl">Абонемент · {s.plan_name}</span>
      <div className="stu-abon">
        <span className="stat">{s.lessons_balance}</span>
        <span className="of">из {s.lessons_total} занятий осталось</span>
        <div className="spacer" />
        {s.makeups_balance > 0 && (
          <span className="pill acc">
            <i className="dot" />+{s.makeups_balance} {plural(s.makeups_balance, 'отработка', 'отработки', 'отработок')}
          </span>
        )}
        {s.status !== 'active' && <span className="pill mute">{s.status}</span>}
      </div>

      {/* Сегментированный счётчик — тот же приём, что в панели отметки:
          остаток читается, а не оценивается на глаз */}
      <div className="meter big" aria-hidden="true">
        {Array.from({ length: s.lessons_total }, (_, i) => (
          <i key={i} className={i < s.lessons_balance ? 'on' : ''} />
        ))}
      </div>

      <div className="stu-abon-foot">
        <span>
          {/* «Куплен», а не «оплачен»: абонемент могли оформить в долг —
              факт оплаты живёт в блоке семьи, а не здесь */}
          Куплен {dayMonth(s.valid_from)} · {money(s.price)} · действует до {dateGen(s.valid_until)}
        </span>
        <span className="num">{money(s.lesson_price)} / занятие</span>
      </div>

      {s.holds.length > 0 && (
        <div className="holds">
          <span className="lbl">Заморозки · использовано {daysWord(s.freeze_days_used)} из {s.rules.freeze_days_per_year}</span>
          {s.holds.map((hold) => (
            <div className="hold" key={hold.id}>
              <div>
                <b className="num">
                  {dayMonth(hold.from)} — {dayMonth(hold.to)}
                </b>
                <small>
                  {daysWord(hold.days)}
                  {hold.reason ? ` · ${hold.reason}` : ''}
                </small>
              </div>
              {canSell && (
                <button className="btn slim" onClick={() => onReleaseHold(hold.id)} title="Снять заморозку и вернуть срок назад">
                  Снять
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {canSell && (
        <div className="actions">
          <button className="btn pri" onClick={onSell}>
            Продать продление
          </button>
          <button className="btn" onClick={onFreeze}>
            Заморозить
          </button>
        </div>
      )}
    </div>
  );
}

/* ---------- журнал ---------- */

function LedgerCard({ ledger }: { ledger: LedgerEntry[] }) {
  return (
    <div className="card">
      <span className="lbl">Движение по абонементу</span>
      {ledger.length === 0 ? (
        <p className="trend">Движений пока нет — абонемент ещё не продавали.</p>
      ) : (
        <div className="tbl-wrap">
          <table>
            <thead>
              <tr>
                <th>Дата</th>
                <th>Событие</th>
                <th>Преподаватель</th>
                <th className="r">Занятий</th>
                <th className="r">Отработки</th>
                <th className="r">Деньги</th>
              </tr>
            </thead>
            <tbody>
              {ledger.map((entry) => (
                <tr key={entry.id}>
                  <td className="num">{dayMonth(entry.date)}</td>
                  <td>{entry.title}</td>
                  <td className={entry.teacher ? '' : 'dim'}>{entry.teacher ?? '—'}</td>
                  <td className={`r num ${deltaTone(entry.lessons_delta, entry.title)}`}>
                    {signed(entry.lessons_delta)}
                  </td>
                  <td className={`r num ${entry.makeups_delta > 0 ? 'good' : 'dim'}`}>
                    {entry.makeups_delta === 0 ? '—' : signed(entry.makeups_delta)}
                  </td>
                  <td className={`r num ${entry.amount ? '' : 'dim'}`}>{entry.amount ? money(entry.amount) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="hint">
        Журнал неизменяем: ошибка гасится обратной записью, а не правкой. Это тот экран, которым отвечают на вопрос
        «куда делось занятие».
      </p>
    </div>
  );
}

/** Прогул красим красным: списание есть, а занятия не было — это спорная строка. */
const deltaTone = (delta: number, title: string): string => {
  if (delta === 0) return 'dim';
  if (delta > 0) return 'good';
  return title.toLowerCase().includes('прогул') ? 'bad-num' : '';
};

const signed = (value: number): string => (value > 0 ? `+${value}` : value === 0 ? '—' : `−${Math.abs(value)}`);

/* ---------- заметки ---------- */

function NotesCard({ notes }: { notes: StudentNote[] }) {
  return (
    <div className="card">
      <span className="lbl">Заметки к урокам и репертуар</span>
      {notes.length === 0 ? (
        <p className="trend">Преподаватель ещё не оставлял заметок по этому ученику.</p>
      ) : (
        notes.map((note, index) => (
          <div className="note" key={`${note.date}-${index}`}>
            <div className="note-h">
              <time className="num">{dayMonth(note.date)}</time>
              <b>{note.author}</b>
            </div>
            <p>{note.body}</p>
            {note.homework && (
              <p className="hw">
                <strong>Домашнее задание:</strong> {note.homework}
              </p>
            )}
            {note.tags.length > 0 && (
              <div className="tags">
                {note.tags.map((tag) => (
                  <span className="tag" key={tag}>
                    {tag}
                  </span>
                ))}
              </div>
            )}
          </div>
        ))
      )}
    </div>
  );
}

/* ---------- семья ---------- */

function FamilyCard({
  family,
  currentId,
  onOpenStudent,
}: {
  family: Family | null;
  currentId: string;
  onOpenStudent: (id: string) => void;
}) {
  if (!family) {
    return (
      <div className="card">
        <span className="lbl">Семья · плательщик</span>
        <p className="trend">Семья не заведена: платит сам ученик, скидки за второго ребёнка не будет.</p>
      </div>
    );
  }

  return (
    <div className="card">
      <span className="lbl">Семья · плательщик</span>
      {family.payer ? (
        <div className="who-cell payer">
          <div className="chip" style={{ '--ch': 'var(--ch-plum)' } as CSSProperties}>
            {initials(family.payer.name)}
          </div>
          <div>
            <b>{family.payer.name}</b>
            <br />
            <span className="num phone">{family.payer.phone}</span>
          </div>
        </div>
      ) : (
        <p className="trend">Плательщик не указан — некому выставить счёт за продление.</p>
      )}

      {family.members.map((member) => (
        <div className="kv" key={member.student_id}>
          <span>
            {member.student_id === currentId ? (
              `${member.name}, ${member.age} ${plural(member.age, 'год', 'года', 'лет')}`
            ) : (
              <button className="link" onClick={() => onOpenStudent(member.student_id)}>
                {member.name}, {member.age} {plural(member.age, 'год', 'года', 'лет')}
              </button>
            )}
          </span>
          <b>
            {member.discipline} · {lessonsWord(member.lessons_balance)}
          </b>
        </div>
      ))}

      {family.discount_pct > 0 && (
        <div className="kv">
          <span>Скидка семьи</span>
          <b className="good">−{family.discount_pct}%</b>
        </div>
      )}
      <div className="kv">
        <span>Оплачено за месяц</span>
        <b className="num">{money(family.paid_this_month)}</b>
      </div>
      {family.debt > 0 && (
        <div className="kv">
          <span>Долг</span>
          <b className="num bad-num">{money(family.debt)}</b>
        </div>
      )}

      {family.payer && (
        <a
          className="btn wide"
          href={`https://wa.me/${family.payer.phone.replace(/\D/g, '')}`}
          target="_blank"
          rel="noreferrer"
        >
          Написать в WhatsApp
        </a>
      )}
    </div>
  );
}

/* ---------- правила ---------- */

function RulesCard({ subscription }: { subscription: StudentSubscription }) {
  const rules: SubscriptionRules = subscription.rules;

  return (
    <div className="card">
      <span className="lbl">Правила абонемента</span>
      <div className="kv">
        <span>Прогул без предупреждения</span>
        <b className={rules.no_show_burns ? 'bad-num' : 'good'}>{rules.no_show_burns ? 'Сгорает' : 'Не сгорает'}</b>
      </div>
      <div className="kv">
        <span>Отмена более чем за {rules.cancel_notice_hours} ч</span>
        <b className={rules.cancel_early_effect === 'burn' ? 'bad-num' : 'good'}>
          {EFFECT_LABELS[rules.cancel_early_effect]}
        </b>
      </div>
      <div className="kv">
        <span>Отменил преподаватель</span>
        <b className="good">{EFFECT_LABELS[rules.teacher_cancel_effect]}</b>
      </div>
      <div className="kv">
        <span>Заморозка</span>
        <b>
          до {daysWord(rules.freeze_days_per_year)} в год · осталось {subscription.freeze_days_left}
        </b>
      </div>
      <div className="kv">
        <span>Срок жизни отработки</span>
        <b>{daysWord(rules.makeup_ttl_days)}</b>
      </div>
      {rules.carry_over_lessons !== undefined && (
        <div className="kv">
          <span>Перенос остатка при продлении</span>
          <b className={rules.carry_over_lessons > 0 ? 'good' : ''}>
            {rules.carry_over_lessons > 0 ? `до ${lessonsWord(rules.carry_over_lessons)}` : 'не переносится'}
          </b>
        </div>
      )}
      <p className="hint">
        Правила скопированы в абонемент при продаже. Если школа изменит их завтра, этот абонемент продолжит жить
        по условиям своей покупки.
      </p>
    </div>
  );
}

const EFFECT_LABELS: Record<string, string> = {
  makeup: 'В отработки',
  keep: 'Занятие сохраняется',
  burn: 'Сгорает',
  no_charge: 'Без списания',
};

/* ---------- отработки ---------- */

function MakeupsCard({ makeups }: { makeups: MakeupCredit[] }) {
  const open = makeups.filter((m) => m.used_at === null);

  return (
    <div className="card">
      <span className="lbl">
        Отработки · {open.length} {plural(open.length, 'открытая', 'открытые', 'открытых')}
      </span>
      {makeups.length === 0 ? (
        <p className="trend">Отработок нет: все отменённые занятия либо списаны, либо уже отработаны.</p>
      ) : (
        makeups.map((makeup) => (
          <div className="kv" key={makeup.id}>
            <span>За {dayMonth(makeup.granted_for)}</span>
            {makeup.used_at ? (
              <b className="dim">Отработана</b>
            ) : (
              <b className={makeup.days_left <= 7 ? 'bad-num' : ''}>
                до {dateGen(makeup.expires_on)} · {daysWord(makeup.days_left)}
              </b>
            )}
          </div>
        ))
      )}
      {open.length > 0 && <p className="hint">После срока отработка сгорает — её надо поставить в расписание заранее.</p>}
    </div>
  );
}

/* ---------- риск оттока ---------- */

function ChurnCard({ risk }: { risk: ChurnRisk | null }) {
  if (!risk) {
    return (
      <div className="card">
        <span className="lbl">Риск оттока</span>
        <p className="trend">Данных для оценки пока недостаточно.</p>
      </div>
    );
  }

  const color = risk.level === 'high' ? 'var(--bad)' : risk.level === 'medium' ? 'var(--accent)' : 'var(--ok)';

  return (
    <div className="card">
      <span className="lbl">Риск оттока</span>
      <div className="stat" style={{ color }}>
        {CHURN_LABELS[risk.level]}
      </div>
      {/* Каждая причина — проверяемый факт из журнала, а не мнение системы */}
      <p className="trend">{risk.reasons.join(' · ')}</p>
      <div className="bar">
        <i style={{ width: `${risk.score}%`, background: color }} />
      </div>
    </div>
  );
}
