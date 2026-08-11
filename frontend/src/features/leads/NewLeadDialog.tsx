import { useState } from 'react';
import { ApiError, SOURCES, SOURCE_LABELS, api, type Branch, type LeadCard, type LeadSource } from '../../api';
import { useAsync } from '../../lib/useAsync';
import { Dialog } from '../../components/Dialog';

/**
 * Заведение заявки вручную — администратор принял звонок.
 *
 * Направление здесь не выбирается: справочника направлений контракт не даёт
 * ни одним эндпоинтом, а `discipline_id` — это uuid, который клиенту взять
 * неоткуда. Заявка создаётся без направления и с пометкой в комментарии;
 * как только появится `GET /disciplines`, поле встанет сюда за десять минут.
 */
export function NewLeadDialog({
  branchId,
  onClose,
  onCreated,
}: {
  branchId: string | null;
  onClose: () => void;
  onCreated: (lead: LeadCard) => void;
}) {
  const branches = useAsync<Branch[]>(() => api.branches(), []);
  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [studentName, setStudentName] = useState('');
  const [age, setAge] = useState('');
  const [source, setSource] = useState<LeadSource>('phone');
  const [branch, setBranch] = useState<string | null>(branchId);
  const [comment, setComment] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const ready = name.trim().length > 1 && phone.replace(/\D/g, '').length >= 10;

  const submit = async () => {
    setSaving(true);
    setError(null);
    try {
      const lead = await api.createLead({
        name: name.trim(),
        phone: phone.trim(),
        ...(studentName.trim() ? { student_name: studentName.trim() } : {}),
        ...(age ? { student_age: Number(age) } : {}),
        branch_id: branch,
        source,
        ...(comment.trim() ? { comment: comment.trim() } : {}),
      });
      onCreated(lead);
    } catch (err) {
      setError(
        err instanceof ApiError ? err : new ApiError(0, 'unexpected', 'Заявку завести не удалось. Повторите попытку.'),
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog
      title="Новая заявка"
      subtitle="Звонок принят администратором"
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={saving}>
            Отмена
          </button>
          <button className="btn pri" onClick={submit} disabled={!ready || saving}>
            {saving ? 'Заводим…' : 'Завести заявку'}
          </button>
        </>
      }
    >
      <div className="blk fields">
        <label className="field">
          <span>Кто звонит</span>
          <input
            className="inp"
            value={name}
            placeholder="Гульнара Сагындык"
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <label className="field">
          <span>Телефон</span>
          <input
            className="inp num"
            value={phone}
            placeholder="+7 701 555 00 03"
            onChange={(event) => setPhone(event.target.value)}
          />
        </label>
        <label className="field">
          <span>Имя ученика</span>
          <input
            className="inp"
            value={studentName}
            placeholder="если звонит родитель"
            onChange={(event) => setStudentName(event.target.value)}
          />
        </label>
        <label className="field">
          <span>Возраст</span>
          <input
            className="inp num"
            type="number"
            min={3}
            max={99}
            value={age}
            onChange={(event) => setAge(event.target.value)}
          />
        </label>
        <label className="field">
          <span>Источник</span>
          <select className="inp" value={source} onChange={(event) => setSource(event.target.value as LeadSource)}>
            {SOURCES.map((s) => (
              <option key={s} value={s}>
                {SOURCE_LABELS[s]}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Филиал</span>
          <select className="inp" value={branch ?? ''} onChange={(event) => setBranch(event.target.value || null)}>
            {(branches.data ?? []).map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field wide">
          <span>Комментарий</span>
          <input
            className="inp"
            value={comment}
            placeholder="направление, удобное время — всё, что сказали по телефону"
            onChange={(event) => setComment(event.target.value)}
          />
        </label>
      </div>

      <p className="hint" style={{ marginTop: 0 }}>
        Телефон нормализуется к международному формату. Если на этот номер уже есть открытая заявка, сервер откажет:
        два лида на одного человека заставят воронку врать.
      </p>

      {error && (
        <div className="err-inline" role="alert">
          <strong>{error.message}</strong>
        </div>
      )}
    </Dialog>
  );
}
