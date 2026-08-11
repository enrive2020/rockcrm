import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ApiError,
  MARK_LABELS,
  MARK_ORDER,
  api,
  type AttendanceMark,
  type AttendanceResponse,
  type AttendanceRevokeResponse,
  type LessonCard,
  type LessonParticipant,
} from '../../api';
import { lessonsWord, money, signedMoney, wallTime } from '../../lib/format';
import { useAsync } from '../../lib/useAsync';
import { ErrorState } from '../../components/States';

export interface AppliedResult {
  participant: LessonParticipant;
  response: AttendanceResponse;
}

export interface RevokedResult {
  participant: LessonParticipant;
  response: AttendanceRevokeResponse;
}

/**
 * Панель отметки. Главная идея этапа: последствия видны ДО подтверждения.
 * Числа берутся из `mark_effects` ответа GET /lessons/{id} — на клиенте
 * ничего не считается, иначе предпросмотр разойдётся с тем, что сделает сервер.
 */
export function AttendancePanel({
  lessonId,
  onClose,
  onApplied,
  onRevoked,
  onOpenStudent,
}: {
  lessonId: string | null;
  onClose: () => void;
  onApplied: (results: AppliedResult[]) => void;
  onRevoked: (result: RevokedResult) => void;
  onOpenStudent: (studentId: string) => void;
}) {
  const card = useAsync<LessonCard>(() => api.lesson(lessonId as string), [lessonId], lessonId !== null);
  const [chosen, setChosen] = useState<Record<string, AttendanceMark>>({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<ApiError | null>(null);
  /** Кого сейчас переспрашиваем об отмене отметки — student_id, иначе null. */
  const [confirming, setConfirming] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<string | null>(null);
  /** Ошибка отмены живёт рядом с участником: на группе иначе непонятно, чья она. */
  const [revokeError, setRevokeError] = useState<{ studentId: string; error: ApiError } | null>(null);

  // Смена занятия обнуляет выбор: показать чужой предпросмотр опаснее, чем ничего
  useEffect(() => {
    setChosen({});
    setSaveError(null);
    setConfirming(null);
    setRevokeError(null);
  }, [lessonId]);

  const lesson = card.data;
  const pending = useMemo(
    () => (lesson ? lesson.participants.filter((p) => p.attendance === null) : []),
    [lesson],
  );

  const pick = useCallback((studentId: string, mark: AttendanceMark) => {
    setSaveError(null);
    setChosen((prev) => (prev[studentId] === mark ? omit(prev, studentId) : { ...prev, [studentId]: mark }));
  }, []);

  // Цифры 1–6 выбирают отметку, когда неотмеченный участник ровно один:
  // на ресепшене это самый частый случай, и мышь там лишняя.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        // Esc сначала снимает вопрос об отмене и только потом закрывает панель:
        // иначе рефлекторное «нет» закрыло бы карточку целиком.
        if (confirming) setConfirming(null);
        else onClose();
        return;
      }
      // Пока карточка грузится, участники в памяти относятся к прошлому занятию
      if (card.loading || pending.length !== 1) return;
      const index = Number(event.key);
      if (!Number.isInteger(index) || index < 1 || index > MARK_ORDER.length) return;
      if (event.target instanceof HTMLInputElement) return;
      pick(pending[0].student_id, MARK_ORDER[index - 1]);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [pending, pick, onClose, card.loading, confirming]);

  /**
   * Отмена ошибочной отметки. Числа для уведомления берутся из ответа сервера,
   * а не из предпросмотра: клиент показал ожидание, сервер отдаёт факт.
   */
  const revoke = async (participant: LessonParticipant) => {
    if (!participant.attendance_id) return;
    setRevoking(participant.student_id);
    setRevokeError(null);
    try {
      const response = await api.revokeAttendance(participant.attendance_id);
      setConfirming(null);
      onRevoked({ participant, response });
      // Перечитываем карточку: участник снова без отметки, и его можно
      // отметить заново — ровно то, ради чего отмена и нужна.
      card.reload();
    } catch (error) {
      setRevokeError({
        studentId: participant.student_id,
        error:
          error instanceof ApiError
            ? error
            : new ApiError(0, 'unexpected', 'Отметку отменить не удалось. Повторите попытку.'),
      });
    } finally {
      setRevoking(null);
    }
  };

  const submit = async () => {
    if (!lesson) return;
    setSaving(true);
    setSaveError(null);
    const results: AppliedResult[] = [];
    try {
      // Последовательно, а не Promise.all: при ошибке на втором ученике
      // важно знать, что первый уже записан, — параллель это скрывает.
      for (const [studentId, mark] of Object.entries(chosen)) {
        const participant = lesson.participants.find((p) => p.student_id === studentId);
        if (!participant) continue; // ученик из прошлой карточки — отправлять нечего
        const response = await api.markAttendance(lesson.id, { student_id: studentId, mark });
        results.push({ participant, response });
      }
      onApplied(results);
    } catch (error) {
      setSaveError(
        error instanceof ApiError
          ? error
          : new ApiError(0, 'unexpected', 'Отметку сохранить не удалось. Повторите попытку.'),
      );
      // Часть отметок могла пройти — сообщаем о них и перечитываем карточку
      if (results.length > 0) onApplied(results);
      card.reload();
      setChosen({});
    } finally {
      setSaving(false);
    }
  };

  const open = lessonId !== null;
  const chosenCount = Object.keys(chosen).length;

  return (
    <aside className={open ? 'insp on' : 'insp'} aria-hidden={!open} aria-label="Отметка посещаемости">
      {/* Состояния взаимоисключающие: при переходе на другое занятие данные
          предыдущего ещё в памяти, и показывать их под скелетом нельзя —
          администратор отметит не того ученика */}
      {open && card.loading && <PanelSkeleton onClose={onClose} />}
      {open && !card.loading && card.error && (
        <div className="insp-b">
          <ErrorState error={card.error} onRetry={card.reload} title="Карточка занятия не загрузилась" />
          <button className="btn" onClick={onClose}>
            Закрыть
          </button>
        </div>
      )}
      {open && !card.loading && !card.error && lesson && (
        <>
          <div className="insp-h">
            <div>
              <h2>{lessonTitle(lesson)}</h2>
              <p>
                {wallTime(lesson.starts_at)}–{wallTime(lesson.ends_at)} · {lesson.duration_min} мин · {lesson.room.name}
              </p>
            </div>
            <button className="x" onClick={onClose} title="Закрыть (Esc)">
              ✕
            </button>
          </div>

          <div className="insp-b">
            <div className="blk">
              <div className="kv">
                <span>Преподаватель</span>
                <b>{lesson.teacher.name}</b>
              </div>
              <div className="kv">
                <span>Формат</span>
                <b>{formatLabel(lesson)}</b>
              </div>
              <div className="kv">
                <span>Ставка преподавателя</span>
                <b className="num">{money(lesson.teacher.rate)}</b>
              </div>
            </div>

            <div className="blk">
              <span className="lbl">{lesson.participants.length > 1 ? 'Участники' : 'Отметить посещаемость'}</span>
              {lesson.participants.map((participant) => (
                <ParticipantBlock
                  key={participant.student_id}
                  participant={participant}
                  chosen={chosen[participant.student_id] ?? null}
                  onPick={(mark) => pick(participant.student_id, mark)}
                  showKeys={pending.length === 1}
                  onOpenStudent={onOpenStudent}
                  confirming={confirming === participant.student_id}
                  onConfirmRevoke={() => {
                    setRevokeError(null);
                    setConfirming(participant.student_id);
                  }}
                  onCancelRevoke={() => setConfirming(null)}
                  onRevoke={() => revoke(participant)}
                  revoking={revoking === participant.student_id}
                  revokeError={
                    revokeError && revokeError.studentId === participant.student_id ? revokeError.error : null
                  }
                />
              ))}
            </div>

            {lesson.note && (
              <div className="blk">
                <span className="lbl">Заметка к уроку</span>
                <div className="rule" style={{ borderStyle: 'solid' }}>
                  {lesson.note.body}
                  {lesson.note.homework && (
                    <>
                      <br />
                      <strong>Домашнее задание:</strong> {lesson.note.homework}
                    </>
                  )}
                </div>
              </div>
            )}

            {saveError && (
              <div className="err-inline" role="alert">
                <strong>{saveError.message}</strong>
              </div>
            )}
          </div>

          <div className="insp-f">
            <button className="btn" onClick={onClose} disabled={saving}>
              Отмена
            </button>
            <button className="btn pri" onClick={submit} disabled={chosenCount === 0 || saving}>
              {saving ? 'Сохраняем…' : chosenCount > 1 ? `Сохранить (${chosenCount})` : 'Сохранить'}
            </button>
          </div>
        </>
      )}
    </aside>
  );
}

function ParticipantBlock({
  participant,
  chosen,
  onPick,
  showKeys,
  onOpenStudent,
  confirming,
  onConfirmRevoke,
  onCancelRevoke,
  onRevoke,
  revoking,
  revokeError,
}: {
  participant: LessonParticipant;
  chosen: AttendanceMark | null;
  onPick: (mark: AttendanceMark) => void;
  showKeys: boolean;
  onOpenStudent: (studentId: string) => void;
  confirming: boolean;
  onConfirmRevoke: () => void;
  onCancelRevoke: () => void;
  onRevoke: () => void;
  revoking: boolean;
  revokeError: ApiError | null;
}) {
  const subscription = participant.subscription;
  const effect = chosen ? participant.mark_effects[chosen] : null;
  /**
   * Что отметка уже сделала, берём из `applied_effect` — это факт из журнала.
   * Раньше здесь стоял обход: сервер отдавал только `mark_effects`, то есть
   * предпросмотр, посчитанный от УЖЕ уменьшенного остатка, и `lessons_after`
   * врал на одно занятие («спишется ещё раз»); интерфейс подменял его
   * остатком абонемента. Бэкенд закрыл это полем (issue #22), и подмены
   * больше нет — числа целиком серверные.
   *
   * `?? null` оставлен для старого бэкенда без этого поля: тогда блок
   * «Отмечено» покажет только саму отметку, но ничего не соврёт.
   */
  const applied = participant.applied_effect ?? null;
  // Кнопка отмены появляется только когда есть чем вызвать DELETE: без
  // `attendance_id` (старый бэкенд) отмены просто нет, а не есть неработающая.
  const revocable = participant.attendance !== null && participant.attendance_id !== null;

  return (
    <div className="participant">
      <header>
        {/* Из панели тоже нужен переход в карточку: перед отметкой часто
            спрашивают, сколько осталось и когда платили */}
        <button className="link strong" onClick={() => onOpenStudent(participant.student_id)} title="Открыть карточку ученика">
          {participant.name}
        </button>
        <span className="spacer" />
        {subscription ? (
          <span className="pill mute num">
            {subscription.lessons_balance} / {subscription.lessons_total}
            {subscription.makeups_balance > 0 ? ` · +${subscription.makeups_balance} отр.` : ''}
          </span>
        ) : (
          <span className="pill acc">Без абонемента</span>
        )}
      </header>

      {subscription && (
        <Meter
          total={subscription.lessons_total}
          balance={subscription.lessons_balance}
          // Пока переспрашиваем об отмене, счётчик показывает возвращённое занятие:
          // администратор видит будущий остаток до нажатия, как и при отметке.
          after={
            confirming && applied
              ? subscription.lessons_balance - applied.lessons_delta
              : effect
                ? effect.lessons_after
                : subscription.lessons_balance
          }
        />
      )}

      {participant.attendance && confirming ? (
        <div className="rule">
          <strong>Отменить отметку «{MARK_LABELS[participant.attendance]}»?</strong>
          {applied && <RevertRows effect={applied} subscriptionBalance={subscription?.lessons_balance ?? null} />}
          {/* Имя в этой фразе не подставляем: род не выведешь из строки,
              а «Амина снова доступен» на ресепшене читают вслух родителю */}
          <p style={{ margin: '8px 0 0' }}>
            Записи журнала не удаляются — сервер добавит компенсирующие: возврат по абонементу и корректировку
            в зарплате. После отмены ученика снова можно отметить.
          </p>
        </div>
      ) : participant.attendance ? (
        <div className="rule applied">
          <strong>Отмечено: {MARK_LABELS[participant.attendance]}</strong>
          {applied && <EffectRows effect={applied} />}
          {/* Формулировка сервера — она описывает случившееся, а не правило */}
          {applied && <p style={{ margin: '8px 0 0' }}>{applied.summary}</p>}
        </div>
      ) : (
        <>
          <div className="marks">
            {MARK_ORDER.map((mark, index) => (
              <button
                key={mark}
                className="mark"
                aria-pressed={chosen === mark}
                onClick={() => onPick(mark)}
              >
                {MARK_LABELS[mark]}
                {showKeys && <span className="key">{index + 1}</span>}
              </button>
            ))}
          </div>
          {effect ? (
            <div className={effect.summary.includes('не осталось') ? 'rule warn' : 'rule'}>
              <strong>{MARK_LABELS[chosen as AttendanceMark]}</strong>
              <EffectRows effect={effect} />
              {/* Текст последствия — дословно от сервера: он знает правила абонемента */}
              <p style={{ margin: '8px 0 0' }}>{effect.summary}</p>
            </div>
          ) : (
            <div className="rule">
              Выберите отметку — здесь появится, что именно спишется и начислится. Правила заданы абонементом, считать
              вручную не нужно.
            </div>
          )}
        </>
      )}

      {revocable && (
        <div className="row-actions">
          {confirming ? (
            <>
              <button className="btn slim" onClick={onCancelRevoke} disabled={revoking}>
                Не отменять
              </button>
              <button className="btn slim pri" onClick={onRevoke} disabled={revoking}>
                {revoking ? 'Отменяем…' : 'Отменить отметку'}
              </button>
            </>
          ) : (
            <button className="btn slim" onClick={onConfirmRevoke} title="Отменить ошибочную отметку">
              Отменить отметку
            </button>
          )}
        </div>
      )}

      {revokeError && (
        <div className="err-inline" role="alert">
          <strong>{revokeError.message}</strong>
        </div>
      )}
    </div>
  );
}

/**
 * Минимум, который нужен строкам последствий. Подходит и предпросмотру
 * (`MarkEffect`), и факту применённой отметки (`AppliedMarkEffect`):
 * у второго `lessons_after` допускает null — у ученика без абонемента
 * остатка нет вовсе, и ноль там был бы враньём.
 */
type EffectLike = {
  lessons_delta: number;
  makeups_delta: number;
  teacher_amount: number;
  lessons_after: number | null;
};

/**
 * Что вернётся при отмене. Эндпоинта предпросмотра отмены контракт не даёт,
 * поэтому здесь не расчёт правил, а разворот знака: дельты уже посчитаны
 * сервером, клиент лишь показывает их обратной стороной.
 * Точные числа приходят в ответе DELETE и повторяются уведомлением —
 * тот же приём, что у продажи и заморозки во втором этапе.
 */
function RevertRows({
  effect,
  subscriptionBalance,
}: {
  effect: EffectLike;
  subscriptionBalance: number | null;
}) {
  const lessonsBack = -effect.lessons_delta;
  const makeupsBack = -effect.makeups_delta;
  return (
    <ul>
      <li>
        <span>Вернётся на абонемент</span>
        <em>
          {lessonsBack === 0
            ? 'ничего не списывалось'
            : `+${lessonsBack} → ${lessonsWord((subscriptionBalance ?? 0) + lessonsBack)}`}
        </em>
      </li>
      {makeupsBack !== 0 && (
        <li>
          <span>Отработки</span>
          <em>
            {makeupsBack > 0 ? '+' : ''}
            {makeupsBack}
          </em>
        </li>
      )}
      <li>
        <span>Снимется с преподавателя</span>
        <em>{effect.teacher_amount > 0 ? signedMoney(-effect.teacher_amount) : 'начисления не было'}</em>
      </li>
    </ul>
  );
}

function EffectRows({ effect }: { effect: EffectLike }) {
  return (
    <ul>
      <li>
        <span>Абонемент</span>
        <em>
          {effect.lessons_delta === 0
            ? 'без списания'
            : effect.lessons_after === null
              ? `${effect.lessons_delta}`
              : `${effect.lessons_delta} → ${lessonsWord(effect.lessons_after)}`}
        </em>
      </li>
      {effect.makeups_delta !== 0 && (
        <li>
          <span>Отработки</span>
          <em>
            {effect.makeups_delta > 0 ? '+' : ''}
            {effect.makeups_delta}
          </em>
        </li>
      )}
      <li>
        <span>Преподавателю</span>
        <em>{effect.teacher_amount > 0 ? money(effect.teacher_amount) : '0 ₸'}</em>
      </li>
    </ul>
  );
}

/** Сегментированный счётчик занятий: остаток читается, а не оценивается на глаз. */
function Meter({ total, balance, after }: { total: number; balance: number; after: number }) {
  return (
    <div className="meter" aria-hidden="true">
      {Array.from({ length: total }, (_, i) => {
        // Ячейки сверх будущего остатка, но в пределах текущего, — то, что спишется
        const className = i < after ? 'on' : i < balance ? 'next' : '';
        return <i key={i} className={className} />;
      })}
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
        <div className="skeleton" style={{ height: 92, marginBottom: 14 }} />
        <div className="skeleton" style={{ height: 220 }} />
      </div>
    </>
  );
}

/**
 * Контракт не даёт заголовка в GET /lessons/{id} — собираем его из участников.
 * Для пробного урока участник один и это имя из заявки.
 */
function lessonTitle(lesson: LessonCard): string {
  const names = lesson.participants.map((p) => p.name);
  if (lesson.kind === 'trial') return `Пробный · ${names[0] ?? 'без ученика'}`;
  if (names.length === 0) return 'Занятие';
  if (names.length <= 2) return names.join(', ');
  return `Группа · ${names.length} чел.`;
}

const formatLabel = (lesson: LessonCard): string => {
  if (lesson.kind === 'trial') return `Пробный, ${lesson.duration_min} мин`;
  if (lesson.participants.length > 1) return `Группа, ${lesson.participants.length} чел.`;
  return 'Индивидуально';
};

const omit = <T extends Record<string, unknown>>(source: T, key: string): T => {
  const copy = { ...source };
  delete copy[key];
  return copy;
};
