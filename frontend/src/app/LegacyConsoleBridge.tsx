import { useState } from 'react';

/** Compatibility boundary while each legacy page moves into a feature package. */
export function LegacyConsoleBridge() {
  const [loaded, setLoaded] = useState(false);
  const [frameVersion] = useState(() => Date.now());
  return (
    <section className="console-bridge" aria-busy={!loaded}>
      {!loaded && <div className="console-bridge__loading">正在进入 ScholarAgent</div>}
      <iframe
        className="console-bridge__frame"
        src={`/app.html?v=${frameVersion}`}
        title="ScholarAgent 工作台"
        onLoad={() => setLoaded(true)}
      />
    </section>
  );
}
