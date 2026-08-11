import { httpApi, type Api } from './http';
import { mockApi } from './mocks/server';

/**
 * Выбор источника данных. Мок-режим включается переменной окружения
 * `VITE_USE_MOCKS=true`: бэкенд пишется параллельно, и ждать его нельзя.
 * Всё приложение импортирует только `api` — подмена источника не меняет
 * ни одного компонента.
 */
export const USE_MOCKS = import.meta.env.VITE_USE_MOCKS === 'true';

export const api: Api = USE_MOCKS ? mockApi : httpApi;

export { ApiError } from './http';
export * from './types';
