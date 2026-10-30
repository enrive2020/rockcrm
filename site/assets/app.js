(function(){
const fmt=n=>Math.round(n).toLocaleString('ru-RU').replace(/,/g,' ')+' ₽';
const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
// CONFIG: заполнить перед запуском. tg — ник в Telegram без @, endpoint — URL приёма заявок (Formspree / вебхук бота), email — ящик для запасного mailto.
const CONFIG={tg:'',endpoint:'',email:'hello@podacha.ai'};
if(CONFIG.tg)$$('[data-tg]').forEach(a=>a.href='https://t.me/'+CONFIG.tg);
// burger
const b=$('.burger'),nav=$('.nav');const closeNav=()=>{if(!nav)return;nav.classList.remove('open');if(b)b.setAttribute('aria-expanded','false')};
if(b)b.onclick=e=>{e.stopPropagation();nav.classList.toggle('open');b.setAttribute('aria-expanded',nav.classList.contains('open'))};
$$('.nav a').forEach(a=>a.onclick=closeNav);
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeNav()});
document.addEventListener('click',e=>{if(nav&&nav.classList.contains('open')&&!nav.contains(e.target))closeNav()});

// services: [cat, name, problem, what, result, term, once, month, est]
const S=[
['qr','QR-меню «Старт»','Бумажное меню, стоп-лист неактуален, нет фото','Оцифровка меню, дизайн в стиле бренда, QR-таблички, синхронизация стоп-листа с кассой','Актуальное меню за 3 секунды, без перепечаток','3–5 дней',9900,1490],
['qr','QR-заказ, оплата СБП и чаевые','Официант тратит время на заказ и расчёт, дорогой эквайринг','Заказ со стола прямо в кассу, оплата по СБП/Яндекс Пэй, разделение счёта, чаевые','−5–7 мин работы официанта на стол; СБП 0,4–0,7% вместо 1,6–2,5%','1–2 недели',19900,1990],
['qr','Мультиязычное меню','Туристы не понимают меню','ИИ-перевод на 5–10 языков с ручной вычиткой, автоопределение языка','Меньше вопросов к персоналу, выше чек иностранных гостей*','2–3 дня',4900,0],
['qr','ИИ-официант и допродажи в меню','Гость не знает, что заказать; нет допродаж','ИИ-помощник в QR-меню (допродажи): подбор блюд, пары к напиткам, «добавить десерт?», аллергены только из вашей базы','+5–12% к чеку у пользователей*','1–2 недели',29900,2990],
['qr','ИИ-фото и описания блюд','Плохие фото, съёмка стоит 25–80 тыс. ₽','Обработка фото с телефона, продающие описания; пакет до 40 позиций (+300 ₽ за доп.)','Меню 30–50 позиций за 2–3 дня','2–3 дня',12000,null],
['guests','ИИ-администратор бронирований','Заявки теряются ночью и в час пик','Бот в Telegram, VK, MAX и на сайте: брони, ответы на вопросы, меню, передача сложного человеку, запись в CRM','Ответ за секунды 24/7, +10–20% броней*','1–2 недели',34900,5900],
['guests','Голосовой ИИ-администратор','30–40% звонков в пик без ответа*','ИИ отвечает на входящие, бронирует, предупреждает о записи, переводит на сотрудника. 300 мин включено, далее 12 ₽/мин','Возврат части потерянных броней*','2–3 недели',59900,9900],
['guests','Центр отзывов','Отзывы на Картах, 2ГИС и агрегаторах без ответа','Ответы в тоне бренда на всех площадках (в «Старте» — 1 площадка), алерт о негативе в Telegram за 5 минут, отчёт по жалобам. Без накруток','100% ответов, рейтинг +0,1–0,3 за 3–6 мес*','2–3 дня',0,3900],
['marketing','ИИ-контент и SMM','Нет маркетолога, фрилансер стоит 15–40 тыс. ₽','Контент-план, 12–20 постов в месяц для TG/VK, ИИ-визуалы, Stories, маркировка рекламы (ERID): регистрацию в ОРД делаем мы, плата ОРД включена','Регулярный контент дешевле фрилансера','1 неделя',9900,14900],
['marketing','CRM-лояльность и рассылки','Гости приходят один раз, базы нет','Лояльность (UDS/Loona/своя), группы гостей по частоте визитов, триггеры «давно не был», ДР; согласия по 38-ФЗ и 152-ФЗ','Кофейня на 3 000 гостей: +40–80 тыс. ₽/мес*','2–3 недели',24900,12900],
['marketing','Сайт заведения','Заказы уходят в агрегаторы с комиссией 20–35%','Сайт с меню из кассы, бронью и онлайн-заказом, SEO под карты. Лендинг 39 900 ₽, с заказом 119 900 ₽','Свой канал заказов без комиссии','1–3 недели',39900,1990,'от '],
['marketing','Карточки на Яндекс Картах и 2ГИС','Неполные карточки, мало переходов','Оформление, фото, меню, рекомендации по продвижению','Рост переходов*','3 дня',7900,0],
['ops','ИИ-аналитик продаж','Отчёты кассы никто не смотрит','Telegram-бот над данными кассы: «что упало вчера?», ABC-анализ и инженерия меню, утренняя сводка, аномалии','+3–8% к чеку за счёт решений по меню*','1–2 недели',29900,4900],
['ops','Контроль себестоимости','Фудкост растёт вместе с ценами поставщиков','Мониторинг цен закупки, алерты по техкартам, рекомендации по ценам меню','Фудкост ниже на 1–3% выручки*','2 недели',29900,4900],
['ops','Прогноз закупок и заготовок','Списания 4–10% фудкоста','ИИ-прогноз продаж по дням и часам с учётом погоды и праздников, план заготовок, заказ поставщику','−20–40% списаний*','3–4 недели',39900,6900],
['ops','Распознавание накладных','Ручной ввод накладных и ошибки','Фото накладной → распознавание → приходная накладная в iiko/r_keeper','−80% времени на приёмку*','1–2 недели',24900,3900],
['ops','Видеоаналитика касс (партнёрская)','Недопробития и сливы на кассе','Подбор и настройка готовых решений (Trassir ActivePOS и др.), сверка с чеками. Оборудование отдельно','Меньше потерь на кассе','2–4 недели',30000,3900,'от '],
['ops','Экспресс-аудит','Непонятно, где теряются деньги','2 часа: меню, отзывы, карточки на картах, данные кассы. Отчёт с 10 точками роста','План с расчётом окупаемости','3 дня',0,null],
['ops','Аудит и оптимизация процессов','Хаос в операционке, грязные данные в кассе','Выезд, разбор кухни и зала, чистка номенклатуры и техкарт, регламенты, дорожная карта. Сеть от 150 000 ₽','База для всех ИИ-модулей','2–3 недели',49900,null],
['ops','Документы по персональным данным (152-ФЗ)','Штрафы за утечку — от 3 до 15 млн ₽ в зависимости от масштаба (КоАП 13.11); отдельное согласие с 01.09.2025','Шаблоны политики, согласий и договора поручения + подача уведомления в РКН; юридическая экспертиза — партнёр-юрист','Соответствие закону для QR, ботов и рассылок','3–5 дней',9900,null],
['staff','Графики смен','Переработки и недоукомплектованные смены','ИИ-график по прогнозу трафика, бот для обмена сменами','−5–12% зарплатного фонда*','2–3 недели',29900,4900],
['staff','ИИ-рекрутер','Вакансии висят, кандидаты ждут ответа 1–2 дня','Бот отвечает на отклики hh/Авито/TG, проводит скрининг, записывает на собеседование (API hh.ru оплачивает клиент)','Первый контакт за 5–15 минут','1–2 недели',29900,5900],
['staff','Бот-наставник и база знаний','Новичков учат 2–3 недели, стандарты в головах','База знаний (меню, техкарты, сервис), тесты, ответы на вопросы 24/7','Ввод в должность за 1–1,5 недели вместо 2–3','2–3 недели',34900,4900],
['staff','Контроль стандартов','Тайный гость стоит 3–8 тыс. ₽ за визит','Фото-чек-листы открытия, закрытия и чистоты с ИИ-проверкой, рейтинг смен','100% чек-листов с фото','2 недели',39900,7900],
];
const CATS={guests:'Гости',marketing:'Маркетинг',ops:'Операционка',staff:'Персонал',qr:'QR-меню'};
const once=s=>s[6]===0?'бесплатно':(s[8]||'')+fmt(s[6]);
const mon=s=>s[7]===null?'—':s[7]===0?'входит':(s[8]&&s[1].startsWith('Видео')?'от ':'')+fmt(s[7])+'/мес';
const est=t=>t.replace(/\*$/,' <span class="est">оценка, проверяем на пилоте</span>');
const grid=$('#svc-grid');
if(grid){grid.innerHTML=S.map(s=>`<article class="card svc" data-cat="${s[0]}"><span class="eyebrow">${CATS[s[0]]}</span><h3>${s[1]}</h3><p><b>Боль:</b> ${s[2]}</p><p>${s[3]}</p><p class="res">${est(s[4])}</p><div class="meta"><span>${once(s)}${s[7]?' + '+mon(s):''}</span><span class="muted">${s[5]}</span></div></article>`).join('');
$$('.tab').forEach(t=>t.onclick=()=>{$$('.tab').forEach(x=>x.setAttribute('aria-pressed',x===t));const c=t.dataset.cat;$$('.svc').forEach(el=>el.hidden=c!=='all'&&el.dataset.cat!==c)});}
const tb=$('#price-body');
if(tb){const render=q=>{q=(q||'').toLowerCase();let h='';Object.keys(CATS).forEach(c=>{const rows=S.filter(s=>s[0]===c&&(!q||(s[1]+s[3]).toLowerCase().includes(q)));if(!rows.length)return;h+=`<tr class="grp"><td colspan="5">${CATS[c]}</td></tr>`+rows.map(s=>`<tr><td><b>${s[1]}</b><div class="note">${s[3]}</div></td><td class="n">${once(s)}</td><td class="n">${mon(s)}</td><td class="n">${s[5]}</td><td>${est(s[4])}</td></tr>`).join('')});tb.innerHTML=h||'<tr><td colspan="5">Ничего не найдено</td></tr>'};render();const si=$('#price-search');if(si)si.oninput=e=>render(e.target.value);}

// pricing toggle
const DISC={month:1,half:0.9,year:0.8},PL={month:'',half:'6 мес',year:'12 мес'};
const curPer=()=>{const x=$('.toggle button[aria-pressed="true"]');return x?x.dataset.p:'month'};
const setPeriod=per=>{$$('.toggle button').forEach(x=>x.setAttribute('aria-pressed',x.dataset.p===per));const k=DISC[per],n=per==='year'?12:6;$$('.plan').forEach(p=>{const m=+p.dataset.m;if(!m)return;const v=m*k,from=p.dataset.from?'от ':'';p.querySelector('.pm').textContent=from+fmt(v);p.querySelector('.old').textContent=k<1?fmt(m):'';p.querySelector('.bill').textContent=k<1?`при оплате за ${PL[per]}: ${from}${fmt(v*n)} (−${Math.round((1-k)*100)}%)`:'помесячно, отмена за 30 дней';const su=p.querySelector('.su');if(su&&p.dataset.freeyear)su.textContent=per==='year'?'бесплатно при оплате за 12 мес':su.dataset.v});const ps=$('#per');if(ps&&ps.value!==per){ps.value=per;ps.dispatchEvent(new Event('change',{bubbles:true}))}};
$$('.toggle button').forEach(x=>x.onclick=()=>setPeriod(x.dataset.p));setPeriod('month');

// ROI
const roi=$('#roi');
if(roi){const P={start:[2990,14900],grow:[14900,49900],pro:[34900,129900]};
// модуль калькулятора -> [индекс в S, пакеты, куда входит]
const MOD={up:[3,['grow','pro']],sbp:[1,['grow','pro']],lab:[20,['pro']],calls:[6,['pro']],fc:[13,['pro']],waste:[14,['pro']]};
const calc=()=>{const v=id=>+$('#'+id).value;const R=v('r'),L=v('l')/100,FC=v('fc')/100,Q=v('q')/100,calls=v('calls'),B=v('b');
$$('#roi output').forEach(o=>{const i=$('#'+o.htmlFor);const u=i.dataset.u||'';o.textContent=u==='₽'?fmt(+i.value):(+i.value).toLocaleString('ru-RU')+u});
const on=k=>$('#m-'+k).checked;const m=$('#mult').value==='low'?0.5:1;
const plan=$('#plan').value,per=$('#per').value,k=DISC[per];
const e={up:R*Q*0.06*(1-FC),lab:R*L*0.025,fc:R*0.015,waste:R*FC*0.06*0.25,calls:calls*0.3*0.2*B*0.3,sbp:R*Q*0.013};
let E=0,xo=0,xm=0,names=[];Object.keys(e).forEach(k2=>{const val=on(k2)?e[k2]*m:0;E+=val;$('#o-'+k2).textContent=on(k2)?fmt(val):'—';
 if(on(k2)&&!MOD[k2][1].includes(plan)){const s=S[MOD[k2][0]];xo+=s[6]||0;xm+=s[7]||0;names.push(s[1])}});
let [mo,setup]=P[plan];if(plan==='start'&&per==='year')setup=0;
mo=mo*k+xm*k;setup+=xo;
if(on('calls')){const over=Math.max(0,calls*1.5-300)*12;mo+=over}
const S2=mo+setup/12;
$('#o-E').textContent=fmt(E);$('#o-S').textContent=fmt(S2);
$('#o-extra').textContent=names.length?' · + модули вне пакета: '+names.join(', '):'';
$('#o-roi').textContent=E>0?((E-S2)/S2).toFixed(1).replace('.',',')+'x':'—';
const net=E-mo;$('#o-pb').textContent=setup===0&&net>0?'сразу':net>0?(setup/net<1?'< 1 мес':(setup/net).toFixed(1).replace('.',',')+' мес'):'не окупается';};
roi.addEventListener('input',calc);roi.addEventListener('change',calc);
const ps=$('#per');if(ps)ps.addEventListener('change',()=>{if(curPer()!==ps.value)setPeriod(ps.value)});
calc();}

// form
const f=$('#lead');
if(f)f.onsubmit=async e=>{e.preventDefault();const ok=$('.ok');const d=new FormData(f);const body=[...d.entries()].map(([k,v])=>k+': '+v).join('\n');
const btn=f.querySelector('button[type=submit]');btn.disabled=true;
const mailto=()=>{location.href='mailto:'+CONFIG.email+'?subject='+encodeURIComponent('Заявка с сайта: '+(d.get('Заведение')||''))+'&body='+encodeURIComponent(body)};
try{if(!CONFIG.endpoint)throw new Error('no endpoint');
 const res=await fetch(CONFIG.endpoint,{method:'POST',headers:{'Accept':'application/json'},body:d});if(!res.ok)throw new Error(res.status);
 ok.textContent='Спасибо! Заявка получена, ответим в течение рабочего дня.';ok.style.display='block';f.reset();}
catch(err){ok.textContent='Не удалось отправить заявку напрямую. Открываем ваш почтовый клиент — если он не открылся, напишите на '+CONFIG.email+(CONFIG.tg?' или в Telegram @'+CONFIG.tg:'')+'.';ok.style.display='block';mailto();}
finally{btn.disabled=false}};
})();
