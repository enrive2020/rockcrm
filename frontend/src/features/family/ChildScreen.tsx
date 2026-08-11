import { api, type MeChild, type MeChildCard, type MeHistoryEntry } from '../../api';
import { useAsync } from '../../lib/useAsync';
import { dateGen, daysWord, lessonsWord, wallTime } from '../../lib/format';
import { CardSkeleton, ErrorState } from '../../components/States';
import { FAMILY_MARK_LABELS, SubscriptionBlock, joinDot, markTone, monthsWord } from './lib';
import type { RenewTarget } from './RenewDialog';

/**
 * Карточка ребёнка: то, ради чего родитель платит и чего не видно в цифрах.
 *
 * Остаток абонемента отвечает на вопрос «сколько ещё», но не на вопрос
 * «а есть ли толк». Ответ на второй — репертуар и заметки преподавателя:
 * список выученного за полгода убеждает продлить сильнее любой скидки,
 * а домашнее задание закрывает ежевечернее «что вам там задали».
 *
 * Внутренних заметок здесь нет и быть не может: сервер выбирает только
 * помеченные `visible_to_family`, и это условие стоит в запросе, а не
 * фильтром поверх ответа.
 */
export function ChildScreen({
  children,
  studentId,
  onSelect,
  renewSent,
  onRenew,
}: {
  children: MeChild[];
  studentId: string;
  onSelect: (studentId: string) => void;
  renewSent: Set<string>;
  onRenew: (target: RenewTarget) => void;
}) {
  const card = useAsync<MeChildCard>(() => api.meChild(studentId), [studentId]);
  const data = card.data;

  return (
    <div className="fam-list">
      {/* Переключатель детей стоит только там, где детей больше одного:
          вкладка из одной кнопки — это украшение, отнимающее строку экрана. */}
      {children.length > 1 && (
        <div className="seg-ctl fam-seg">
          {children.map((child) => (
            <button
              key={child.student_id}
              aria-pressed={child.student_id === studentId}
              onClick={() => onSelect(child.student_id)}
            >
              {child.name}
            </button>
          ))}
        </div>
      )}

      {card.loading && <CardSkeleton />}
      {card.error && <ErrorState error={card.error} onRetry={card.reload} title="Карточка не загрузилась" />}
      {data && (
        <ChildBody
          card={data}
          renewSent={renewSent.has(data.student_id)}
          onRenew={() => onRenew({ student_id: data.student_id, full_name: data.name, discipline: data.discipline })}
        />
      )}
    </div>
  );
}

function ChildBody({ card, renewSent, onRenew }: { card: MeChildCard; renewSent: boolean; onRenew: () => void }) {
  const { progress } = card;

  return (
    <>
      <section className="card fam-card">
        <header className="fam-child-h">
          <div>
            <h2>{card.name}</h2>
            <p>{joinDot(card.discipline, card.teacher)}</p>
          </div>
        </header>
        <p className="fam-since">
          Занимается с {dateGen(card.started_on)} — {monthsWord(progress.months)}, {lessonsWord(progress.lessons_attended)}{' '}
          проведено
        </p>

        <SubscriptionBlock subscription={card.subscription} renewSent={renewSent} onRenew={onRenew} />

        {/* Отработка — это оплаченное занятие, которое сгорит, если о нём
            забыть. Родитель узнаёт о нём здесь, а не в разговоре постфактум. */}
        {card.makeups.length > 0 && (
          <div className="fam-makeups">
            <span className="lbl">Отработки</span>
            {card.makeups.map((makeup, i) => (
              <p key={i} className="fam-makeup">
                Пропущенное занятие можно отработать до {dateGen(makeup.expires_on)}
                <small>осталось {daysWord(makeup.days_left)} — время выберет администратор, напомните ему</small>
              </p>
            ))}
          </div>
        )}
      </section>

      <section className="card fam-card">
        <span className="lbl">Что уже играет</span>
        {progress.repertoire.length > 0 ? (
          <div className="tags fam-tags">
            {progress.repertoire.map((item) => (
              <span className="tag" key={item}>
                {item}
              </span>
            ))}
          </div>
        ) : (
          <p className="hint" style={{ marginTop: 0 }}>
            Преподаватель ещё не отмечал разобранные вещи. Они появятся здесь после ближайших занятий.
          </p>
        )}
      </section>

      <section className="card fam-card">
        <span className="lbl">Занятия и домашние задания</span>
        {card.history.length === 0 ? (
          <p className="hint" style={{ marginTop: 0 }}>
            Занятий пока не было. После первого здесь появится заметка преподавателя.
          </p>
        ) : (
          card.history.map((entry) => <HistoryEntry key={entry.date} entry={entry} />)
        )}
      </section>
    </>
  );
}

/**
 * Одно занятие в истории: когда, как прошло, что сказал преподаватель,
 * что задали и что списалось с абонемента.
 *
 * Списание показано вместе с занятием, а не отдельным журналом: вопрос
 * «куда делось занятие» задают именно про конкретный день, и ответ должен
 * лежать в той же строке.
 */
function HistoryEntry({ entry }: { entry: MeHistoryEntry }) {
  return (
    <article className="fam-hist">
      <header>
        <time>
          {dateGen(entry.date)}
          {entry.starts_at && <>, {wallTime(entry.starts_at)}</>}
        </time>
        {entry.attendance && <span className={`pill ${markTone(entry.attendance)}`}>{FAMILY_MARK_LABELS[entry.attendance]}</span>}
      </header>

      {entry.note ? (
        <>
          <p className="fam-hist-body">{entry.note.body}</p>
          {entry.note.homework && (
            <p className="fam-hw">
              <b>Дома:</b> {entry.note.homework}
            </p>
          )}
          {entry.note.tags.length > 0 && (
            <div className="tags">
              {entry.note.tags.map((tag) => (
                <span className="tag" key={tag}>
                  {tag}
                </span>
              ))}
            </div>
          )}
        </>
      ) : (
        <p className="fam-hist-body dim">Заметки к этому занятию нет.</p>
      )}

      {/* Что стало с абонементом. Формулировка сервера (`title`) показывается
          ТОЛЬКО там, где отметки нет и объяснить движение больше нечем:
          рядом с отметкой она дублирует её словами регламента —
          «Прогул без предупреждения» под мягким «Не пришёл» звучит как
          второй, настоящий приговор. Сами числа — всегда серверные:
          пересчитывать журнал абонемента на клиенте нельзя. */}
      {(entry.lessons_delta !== 0 || Boolean(entry.makeups_delta) || (!entry.attendance && entry.title)) && (
        <p className="fam-hist-delta">
          {joinDot(
            entry.attendance ? null : entry.title,
            entry.lessons_delta < 0
              ? `списано ${lessonsWord(-entry.lessons_delta)}`
              : entry.lessons_delta > 0
                ? `возвращено ${lessonsWord(entry.lessons_delta)}`
                : null,
            entry.makeups_delta && entry.makeups_delta > 0 ? 'начислена отработка' : null,
          )}
        </p>
      )}
    </article>
  );
}
