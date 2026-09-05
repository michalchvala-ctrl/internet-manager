type BarPoint = {
  label: string;
  download: number;
  upload: number;
};

function formatBytes(n: number): string {
  if (!n) return "0";
  const units = ["B", "KB", "MB", "GB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export function TrafficBars({
  title,
  points,
  emptyHint,
}: {
  title: string;
  points: BarPoint[];
  emptyHint?: string;
}) {
  const max = Math.max(
    1,
    ...points.map((p) => p.download + p.upload),
  );
  const hasData = points.some((p) => p.download + p.upload > 0);

  return (
    <div className="chart-block">
      <div className="chart-title">{title}</div>
      {!hasData ? (
        <div className="device-meta">{emptyHint ?? "Zatiaľ žiadne dáta – počkaj pár minút používania."}</div>
      ) : (
        <div className="chart-bars" role="img" aria-label={title}>
          {points.map((p) => {
            const total = p.download + p.upload;
            const h = Math.max(total > 0 ? 8 : 2, Math.round((total / max) * 100));
            const downPct = total ? (p.download / total) * 100 : 0;
            return (
              <div key={p.label} className="chart-col" title={`${p.label}: ↓ ${formatBytes(p.download)} · ↑ ${formatBytes(p.upload)}`}>
                <div className="chart-bar-wrap">
                  <div className="chart-bar" style={{ height: `${h}%` }}>
                    <span className="chart-bar-down" style={{ height: `${downPct}%` }} />
                    <span className="chart-bar-up" />
                  </div>
                </div>
                <div className="chart-label">{p.label}</div>
              </div>
            );
          })}
        </div>
      )}
      <div className="chart-legend">
        <span className="lg-down">↓ download</span>
        <span className="lg-up">↑ upload</span>
      </div>
    </div>
  );
}
