import { render } from 'preact';
import { useEffect, useState } from 'preact/hooks';

/** Admin panel shell (owner/manager). Shows API health until modules are built. */
function App() {
  const [health, setHealth] = useState<string>('…');
  useEffect(() => {
    fetch('/v1/health').then((r) => r.json()).then((j: { status: string }) => setHealth(j.status)).catch(() => setHealth('недоступен'));
  }, []);
  return <main><h1>Подача.AI — кабинет</h1><p>API: {health}</p></main>;
}
render(<App />, document.getElementById('app')!);
