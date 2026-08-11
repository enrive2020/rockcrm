/**
 * Слаг школы, в которой открыт кабинет.
 *
 * Вход требует слаг, потому что телефон уникален внутри школы, но не между
 * школами: без названия школы «вход по телефону» означал бы «покажи все
 * школы, где есть этот номер» — то есть выдачу чужих данных ещё до входа.
 *
 * Спрашивать школу у человека нельзя: родитель знает адрес, по которому
 * ему дали ссылку, а не внутренний слаг. Поэтому слаг берётся из поддомена
 * (`rockschool.crm.kz` → `rockschool`), а на машине разработчика, где
 * поддомена нет, — из конфигурации сборки.
 */

/** Метки, которые поддоменом школы не являются никогда. */
const NOT_A_TENANT = new Set(['www', 'app', 'api', 'localhost', 'crm']);

function fromSubdomain(host: string): string | null {
  const name = host.split(':')[0].toLowerCase();
  // IP-адрес поддоменов не имеет: 127.0.0.1 разобрался бы в школу «127».
  if (/^\d+(\.\d+)*$/.test(name) || name === 'localhost') return null;

  const parts = name.split('.');
  // `school.localhost` — рабочий приём для проверки многотенантности локально,
  // поэтому двух меток достаточно, если вторая — localhost.
  const looksLikeSubdomain = parts.length >= 3 || (parts.length === 2 && parts[1] === 'localhost');
  if (!looksLikeSubdomain) return null;

  const first = parts[0];
  return NOT_A_TENANT.has(first) ? null : first;
}

const CONFIGURED = (import.meta.env.VITE_TENANT_SLUG ?? '').trim();

/**
 * Порядок намеренно такой: поддомен главнее конфигурации сборки. Один и тот же
 * собранный бандл обслуживает все школы, и зашитый в него слаг обязан быть
 * лишь запасным вариантом для разработки, а не подменять адрес, по которому
 * кабинет реально открыт.
 */
export const TENANT_SLUG =
  fromSubdomain(typeof location === 'undefined' ? '' : location.host) || CONFIGURED || 'rockschool-demo';
