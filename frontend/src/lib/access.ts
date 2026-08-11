import type { Me, Role } from '../api';

/**
 * Что показывать роли.
 *
 * Роль берётся из `GET /auth/me` и никогда не выводится на клиенте: роль,
 * вычисленная клиентом, — это роль, которую клиент себе назначил. Здесь
 * не проверка прав, а карта меню: сервер всё равно ответит 403, а задача
 * этого файла — не рисовать пункт, который гарантированно в 403 и упрётся.
 * Пункт меню, ведущий в отказ, хуже отсутствующего: он выглядит поломкой.
 *
 * Каждый флаг соответствует конкретной проверке бэкенда (`app/authz.py`),
 * и рядом с ним написано, какой именно — иначе через месяц никто не вспомнит,
 * почему у преподавателя нет воронки.
 */
export interface Access {
  /** `GET /schedule` → `require_staff`: сетка филиала. Родителю 403. */
  schedule: boolean;
  /** `GET /students` — доступен всем, но выдача урезана сервером по роли. */
  students: boolean;
  /**
   * Родителю и ученику список сразу их собственный, поэтому пустой запрос
   * для них осмыслен: сервер вернёт детей, а не половину школы.
   */
  ownStudentsOnly: boolean;
  /** `GET /leads` → `require_admin`: воронка только владельцу и админу. */
  leads: boolean;
  /** `GET /reports/*` → `require_admin`: сводка и отчёты. */
  money: boolean;
  /** `GET /payroll` → `require_owner`: ведомость по всем преподавателям. */
  payrollSheet: boolean;
  /**
   * `GET /payroll/teachers/{staff_id}` — свою расшифровку преподаватель
   * видит сам (§2: «видит свою ЗП»), чужую не видит никто, кроме владельца.
   */
  myPayroll: boolean;
  /**
   * Продажа, продление и заморозка → `require_admin`. У преподавателя
   * и родителя кнопок быть не должно: они дадут 403 после заполнения формы,
   * то есть после разговора с родителем о сумме.
   */
  sell: boolean;
  /** Переключатель филиалов имеет смысл только там, где есть сетка филиала. */
  branchSwitch: boolean;
  /**
   * Ресурсы `/me/*` → роли `guardian` и `student`. Это не «урезанный
   * административный интерфейс», а другой макет целиком: у родителя один
   * вопрос за раз — когда вести, сколько осталось, что задали, — и плотные
   * таблицы с фильтрами ему мешают. Взрослый ученик платит за себя сам
   * и должен видеть то же, что родитель видит про ребёнка.
   */
  familyCabinet: boolean;
}

const STAFF: Role[] = ['owner', 'admin', 'teacher'];

export function accessFor(me: Me): Access {
  const role = me.role;
  const isStaff = STAFF.includes(role);
  const isAdminOrOwner = role === 'owner' || role === 'admin';

  return {
    schedule: isStaff,
    students: true,
    ownStudentsOnly: role === 'guardian' || role === 'student',
    leads: isAdminOrOwner,
    money: isAdminOrOwner,
    payrollSheet: role === 'owner',
    // Без `staff_id` расшифровку не запросить — кнопки без адреса быть не должно.
    myPayroll: role === 'teacher' && me.staff_id !== null,
    sell: isAdminOrOwner,
    branchSwitch: isStaff,
    familyCabinet: role === 'guardian' || role === 'student',
  };
}

/**
 * Филиалы, доступные пользователю.
 *
 * ПУСТОЙ `branch_ids` ОЗНАЧАЕТ «ВСЕ ФИЛИАЛЫ», а не «ни одного»: школа
 * из одного филиала связь `staff_branch` не заполняет вовсе, и прочтение
 * пустоты как запрета выключило бы владельцу всю школу разом. Ровно так же
 * это трактует и бэкенд (`authz.require_branch`).
 */
export function allowedBranches<T extends { id: string }>(branches: T[], me: Me): T[] {
  if (me.branch_ids.length === 0) return branches;
  return branches.filter((branch) => me.branch_ids.includes(branch.id));
}
