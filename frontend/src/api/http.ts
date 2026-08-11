import type {
  AttendanceRequest,
  AttendanceResponse,
  AttendanceRevokeResponse,
  Branch,
  ConvertRequest,
  ConvertResponse,
  CreateLeadRequest,
  DebtsReport,
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
  MoneySummary,
  PatchLeadRequest,
  PayrollDetail,
  PayrollPeriodRow,
  PayrollSheet,
  PeriodClosed,
  Plan,
  RevenueReport,
  RoomsReport,
  ScheduleResponse,
  SellSubscriptionRequest,
  SellSubscriptionResponse,
  StudentCard,
  StudentSearchItem,
  TrialRequest,
  TrialResponse,
  ApiErrorBody,
  CodeSent,
  LoggedIn,
  LoggedOut,
  LoginRequest,
  Me,
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
 * Сессия протухла или её погасили «выйти на всех устройствах» — интерфейс
 * обязан уйти на экран входа, из какого бы места ни пришёл 401.
 *
 * Обработчик один и живёт здесь, а не в каждом экране: иначе каждый новый
 * запрос пришлось бы отдельно учить тому, что делать с 401, и однажды кто-то
 * забыл бы — пользователь остался бы смотреть на «ошибка загрузки» вместо
 * формы входа.
 */
let onUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      // Токен сессии ездит в куке HttpOnly — прочитать её скриптом нельзя,
      // и без `credentials` браузер просто не приложит её к запросу.
      // Ровно поэтому бэкенду нужен точный Origin: `*` вместе с куками
      // запрещён стандартом, а не нашей осторожностью.
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
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
    // Вход исключён намеренно: 401 на `/auth/login` — это «код не подошёл»,
    // и сбрасывать из-за него форму, в которую человек только что печатал,
    // значило бы терять набранный номер на каждой опечатке.
    if (response.status === 401 && !path.startsWith('/auth/')) onUnauthorized?.();
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
  /* ---------- этап 5: вход ---------- */

  /**
   * Запрос одноразового кода. Всегда 202 — и на существующий телефон,
   * и на случайный: иначе форма входа отвечала бы на вопрос «ходит ли этот
   * ребёнок в эту школу». Слаг школы обязателен: телефон уникален внутри
   * школы, но не между школами.
   */
  requestCode: (tenant: string, login: string) =>
    request<CodeSent>('/auth/request-code', { method: 'POST', body: JSON.stringify({ tenant, login }) }),
  /** Код ИЛИ пароль — вместе сервер отвечает 400, поэтому формы разведены. */
  login: (payload: LoginRequest) =>
    request<LoggedIn>('/auth/login', { method: 'POST', body: JSON.stringify(payload) }),
  /** `everywhere` гасит и соседние устройства — на случай потерянного телефона. */
  logout: (everywhere = false) =>
    request<LoggedOut>(`/auth/logout${everywhere ? '?everywhere=true' : ''}`, { method: 'POST' }),
  /** Кто вошёл и что ему видно. Роль берётся отсюда, и только отсюда. */
  me: () => request<Me>('/auth/me'),

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

  /* ---------- этап 4: деньги и зарплаты ---------- */

  payroll: (from: string, to: string, branchId?: string | null) =>
    request<PayrollSheet>(`/payroll${periodQuery(from, to, branchId)}`),
  /** Расшифровка ведомости: за что именно начислено, занятие за занятием. */
  payrollTeacher: (staffId: string, from: string, to: string, branchId?: string | null) =>
    request<PayrollDetail>(`/payroll/teachers/${encodeURIComponent(staffId)}${periodQuery(from, to, branchId)}`),
  payrollPeriods: (limit = 12) => request<PayrollPeriodRow[]>(`/payroll/periods?limit=${limit}`),
  /**
   * Закрытие периода. Обратной операции нет: ошибка чинится корректировкой
   * в следующем периоде, а не переоткрытием, — поэтому вызов обязан идти
   * только из диалога с подтверждением.
   */
  closePayrollPeriod: (from: string, to: string) =>
    request<PeriodClosed>('/payroll/periods', { method: 'POST', body: JSON.stringify({ from, to }) }),

  revenueReport: (from: string, to: string, branchId?: string | null) =>
    request<RevenueReport>(`/reports/revenue${periodQuery(from, to, branchId)}`),
  roomsReport: (from: string, to: string, branchId?: string | null) =>
    request<RoomsReport>(`/reports/rooms${periodQuery(from, to, branchId)}`),
  /** Периода нет: долг — состояние на сейчас, а не за отрезок. */
  debtsReport: (limit = 50) => request<DebtsReport>(`/reports/debts?limit=${limit}`),
  moneySummary: (from: string, to: string, branchId?: string | null) =>
    request<MoneySummary>(`/reports/summary${periodQuery(from, to, branchId)}`),
};

/** Период плюс необязательный филиал — одинаковый хвост у пяти эндпоинтов этапа 4. */
function periodQuery(from: string, to: string, branchId?: string | null): string {
  const params = new URLSearchParams({ from, to });
  if (branchId) params.set('branch_id', branchId);
  return `?${params.toString()}`;
}

export type Api = typeof httpApi;
