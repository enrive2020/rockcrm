import type {
  AttendanceRequest,
  AttendanceResponse,
  AttendanceRevokeResponse,
  Branch,
  ConvertRequest,
  ConvertResponse,
  CreateLeadRequest,
  DirectoryRoom,
  DirectoryTeacher,
  Discipline,
  FunnelReport,
  HoldRequest,
  HoldReleaseResponse,
  HoldResponse,
  LeadCard,
  LeadsBoard,
  LessonCard,
  PatchLeadRequest,
  Plan,
  ScheduleResponse,
  SellSubscriptionRequest,
  SellSubscriptionResponse,
  StudentCard,
  StudentSearchItem,
  TrialRequest,
  TrialResponse,
  ApiErrorBody,
} from './types';

/**
 * Ошибка API в виде, пригодном для показа человеку.
 * `message` — всегда текст от сервера, если он его прислал:
 * контракт требует показывать именно серверное сообщение.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details?: Record<string, unknown>;

  constructor(status: number, code: string, message: string, details?: Record<string, unknown>) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

const BASE = import.meta.env.VITE_API_BASE_URL ?? '/api/v1';

/**
 * Заглушка авторизации из контракта: тенант и пользователь передаются
 * заголовками. TODO: убрать после появления настоящего входа — тогда
 * заголовки заменит токен сессии.
 */
function authHeaders(): Record<string, string> {
  return {
    'X-Tenant-Id': import.meta.env.VITE_TENANT_ID ?? '',
    'X-User-Id': import.meta.env.VITE_USER_ID ?? '',
  };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        ...authHeaders(),
        ...(init?.headers ?? {}),
      },
    });
  } catch {
    // Сеть не ответила — отдельный случай: сервер не виноват, показывать нечего.
    throw new ApiError(0, 'network_error', 'Сервер не отвечает. Проверьте, что бэкенд запущен, и повторите запрос.');
  }

  if (!response.ok) {
    let body: ApiErrorBody | null = null;
    try {
      body = (await response.json()) as ApiErrorBody;
    } catch {
      body = null;
    }
    throw new ApiError(
      response.status,
      body?.error?.code ?? 'http_error',
      body?.error?.message ?? `Запрос не выполнен, код ${response.status}.`,
      body?.error?.details,
    );
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const httpApi = {
  branches: () => request<Branch[]>('/branches'),
  schedule: (branchId: string, date: string) =>
    request<ScheduleResponse>(`/schedule?branch_id=${encodeURIComponent(branchId)}&date=${encodeURIComponent(date)}`),
  lesson: (lessonId: string) => request<LessonCard>(`/lessons/${encodeURIComponent(lessonId)}`),
  markAttendance: (lessonId: string, payload: AttendanceRequest) =>
    request<AttendanceResponse>(`/lessons/${encodeURIComponent(lessonId)}/attendance`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  /**
   * Отмена ошибочной отметки. Идентификатор берётся из `participants[].attendance_id`
   * карточки занятия — другого источника у интерфейса нет.
   */
  revokeAttendance: (attendanceId: string) =>
    request<AttendanceRevokeResponse>(`/attendance/${encodeURIComponent(attendanceId)}`, { method: 'DELETE' }),

  /* ---------- справочники (задача #1) ---------- */

  /**
   * Списки для форм. Пока эндпоинтов нет на живом бэкенде, запрос вернёт 404,
   * и вызывающая сторона откатится на сбор списков из расписания — но
   * основным источником считается справочник, а не срез дня.
   */
  teachers: (branchId?: string | null) =>
    request<DirectoryTeacher[]>(`/teachers${branchId ? `?branch_id=${encodeURIComponent(branchId)}` : ''}`),
  rooms: (branchId?: string | null) =>
    request<DirectoryRoom[]>(`/rooms${branchId ? `?branch_id=${encodeURIComponent(branchId)}` : ''}`),
  disciplines: () => request<Discipline[]>('/disciplines'),

  /* ---------- этап 2 ---------- */

  /** Поиск идёт и по имени ученика, и по имени и телефону плательщика. */
  students: (query: string, branchId?: string | null) => {
    const params = new URLSearchParams({ query, limit: '20' });
    if (branchId) params.set('branch_id', branchId);
    return request<StudentSearchItem[]>(`/students?${params.toString()}`);
  },
  student: (studentId: string) => request<StudentCard>(`/students/${encodeURIComponent(studentId)}`),
  plans: (disciplineId?: string | null, format?: string | null) => {
    const params = new URLSearchParams();
    if (disciplineId) params.set('discipline_id', disciplineId);
    if (format) params.set('format', format);
    const query = params.toString();
    return request<Plan[]>(`/plans${query ? `?${query}` : ''}`);
  },
  /** Продажа и продление — одна операция: продление есть продажа следующего абонемента. */
  sellSubscription: (studentId: string, payload: SellSubscriptionRequest) =>
    request<SellSubscriptionResponse>(`/students/${encodeURIComponent(studentId)}/subscriptions`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  createHold: (subscriptionId: string, payload: HoldRequest) =>
    request<HoldResponse>(`/subscriptions/${encodeURIComponent(subscriptionId)}/holds`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  releaseHold: (subscriptionId: string, holdId: string) =>
    request<HoldReleaseResponse>(
      `/subscriptions/${encodeURIComponent(subscriptionId)}/holds/${encodeURIComponent(holdId)}`,
      { method: 'DELETE' },
    ),

  /* ---------- этап 3 ---------- */

  leads: (filters: { stage?: string; source?: string; assigned_to?: string; branch_id?: string | null } = {}) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) {
      if (value) params.set(key, value);
    }
    const query = params.toString();
    return request<LeadsBoard>(`/leads${query ? `?${query}` : ''}`);
  },
  lead: (leadId: string) => request<LeadCard>(`/leads/${encodeURIComponent(leadId)}`),
  createLead: (payload: CreateLeadRequest) =>
    request<LeadCard>('/leads', { method: 'POST', body: JSON.stringify(payload) }),
  /** Смена стадии, ответственный, напоминание, счётчик попыток дозвона. */
  patchLead: (leadId: string, payload: PatchLeadRequest) =>
    request<LeadCard>(`/leads/${encodeURIComponent(leadId)}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  bookTrial: (leadId: string, payload: TrialRequest) =>
    request<TrialResponse>(`/leads/${encodeURIComponent(leadId)}/trial`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  convertLead: (leadId: string, payload: ConvertRequest) =>
    request<ConvertResponse>(`/leads/${encodeURIComponent(leadId)}/convert`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  funnel: (from: string, to: string) =>
    request<FunnelReport>(`/leads/funnel?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`),
};

export type Api = typeof httpApi;
