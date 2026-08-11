import { useEffect, useState, type CSSProperties } from 'react';
import { api, type StudentSearchItem } from '../../api';
import { useAsync } from '../../lib/useAsync';
import { dateGen, initials, lessonsWord, plural } from '../../lib/format';
import { EmptyState, ErrorState, ListSkeleton } from '../../components/States';

/** Короче двух букв искать бессмысленно: вернётся половина базы. */
const MIN_QUERY = 2;

/**
 * Экран «Ученики». Строка поиска и результаты.
 *
 * Ищем по имени ребёнка, имени плательщика и телефону: администратору звонит
 * родитель, а не ученик, и первое, что он называет, — своё имя или номер,
 * с которого звонит. Поэтому телефон плательщика стоит прямо в строке
 * результата: перезвонить можно, не открывая карточку.
 */
export function StudentsScreen({
  query,
  onQueryChange,
  onOpen,
}: {
  query: string;
  onQueryChange: (value: string) => void;
  onOpen: (studentId: string) => void;
}) {
  // Запрос отстаёт от ввода: иначе на каждую букву уходил бы запрос,
  // а список мигал бы промежуточными результатами
  const [debounced, setDebounced] = useState(query.trim());
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(query.trim()), 250);
    return () => clearTimeout(timer);
  }, [query]);

  const enabled = debounced.length >= MIN_QUERY;
  const results = useAsync<StudentSearchItem[]>(() => api.students(debounced), [debounced], enabled);
  const found = results.data ?? [];

  return (
    <section className="screen">
      <div className="tl-head">
        <div>
          <h1 className="h1">Ученики</h1>
          <p className="sub">Поиск по имени ученика, имени родителя и телефону — звонит родитель, искать нужно по нему</p>
        </div>
      </div>

      <div className="search-wrap">
        <input
          className="search"
          type="search"
          value={query}
          autoFocus
          placeholder="Амина, Гульнара или +7 701 555 00 03"
          aria-label="Поиск ученика"
          onChange={(event) => onQueryChange(event.target.value)}
        />
        {enabled && !results.loading && !results.error && (
          <span className="pill mute">
            {found.length} {plural(found.length, 'результат', 'результата', 'результатов')}
          </span>
        )}
      </div>

      {!enabled && (
        <EmptyState label="Поиск" title="Введите хотя бы две буквы">
          Найдётся и по имени ребёнка, и по имени родителя, и по номеру телефона — в любом виде записи.
        </EmptyState>
      )}
      {enabled && results.loading && (
        <div className="tl-wrap">
          <ListSkeleton />
        </div>
      )}
      {enabled && !results.loading && results.error && (
        <ErrorState error={results.error} onRetry={results.reload} title="Поиск не выполнился" />
      )}
      {enabled && !results.loading && !results.error && found.length === 0 && (
        <EmptyState label="Ничего не найдено" title={`По запросу «${debounced}» никого нет`}>
          Проверьте раскладку и попробуйте фамилию родителя или последние четыре цифры номера.
        </EmptyState>
      )}
      {enabled && !results.loading && !results.error && found.length > 0 && (
        <div className="tl-wrap">
          {found.map((item) => (
            <ResultRow key={item.id} item={item} onOpen={onOpen} />
          ))}
        </div>
      )}
    </section>
  );
}

function ResultRow({ item, onOpen }: { item: StudentSearchItem; onOpen: (id: string) => void }) {
  const s = item.subscription;
  // Остаток — первое, что спрашивает родитель по телефону, поэтому он
  // вынесен в строку и окрашен: 0 и 1–2 занятия требуют разговора о продлении
  const tone = !s ? 'acc' : s.lessons_balance === 0 ? 'bad' : s.lessons_balance <= 2 ? 'acc' : 'mute';

  return (
    <button className="res" onClick={() => onOpen(item.id)}>
      <span className="chip" style={{ '--ch': 'var(--ch-slate)' } as CSSProperties}>
        {initials(item.name)}
      </span>
      <span className="res-main">
        <b>{item.name}</b>
        <small>
          {item.age} {plural(item.age, 'год', 'года', 'лет')} · {item.discipline} · {item.teacher} · {item.branch}
        </small>
      </span>
      <span className="res-payer">
        {item.payer ? (
          <>
            <b>{item.payer.name}</b>
            <small className="num">{item.payer.phone}</small>
          </>
        ) : (
          <small>Плательщик не заведён</small>
        )}
      </span>
      <span className="res-sub">
        <span className={`pill ${tone}`}>
          {s ? `${s.lessons_balance} / ${s.lessons_total}` : 'Без абонемента'}
        </span>
        <small>{s ? `${lessonsWord(s.lessons_balance)} · до ${dateGen(s.valid_until)}` : 'разовая оплата'}</small>
      </span>
    </button>
  );
}
