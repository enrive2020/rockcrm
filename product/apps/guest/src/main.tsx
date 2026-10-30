import { render } from 'preact';
import { useEffect, useState } from 'preact/hooks';

/** QR guest app: /t/<qr_token> -> POST /v1/guest/sessions -> menu. MVP shell. */
function App() {
  const token = location.pathname.split('/t/')[1] ?? '';
  const [status, setStatus] = useState('Загрузка…');
  useEffect(() => {
    if (!token) { setStatus('Отсканируйте QR-код на столе'); return; }
    fetch('/v1/guest/sessions', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ qr_token: token }) })
      .then((r) => setStatus(r.ok ? 'Меню скоро появится' : 'QR-код недействителен'))
      .catch(() => setStatus('Нет связи'));
  }, [token]);
  return <main><h1>Меню</h1><p>{status}</p></main>;
}
render(<App />, document.getElementById('app')!);
