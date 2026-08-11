import type {
  AttendanceRequest,
  AttendanceResponse,
  Branch,
  LessonCard,
  ScheduleResponse,
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
};

export type Api = typeof httpApi;
