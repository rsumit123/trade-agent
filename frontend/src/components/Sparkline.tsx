"use client";

interface SparklineProps {
  values: number[];
  width?: number;
  height?: number;
  positive?: boolean;
  strokeWidth?: number;
  fill?: boolean;
  className?: string;
  responsive?: boolean;
}

export function Sparkline({
  values,
  width = 100,
  height = 28,
  positive,
  strokeWidth = 1.5,
  fill = true,
  className,
  responsive = false,
}: SparklineProps) {
  if (!values || values.length < 2) {
    return (
      <div
        className={className}
        style={{ width, height, display: "flex", alignItems: "center", justifyContent: "center" }}
      >
        <div style={{ width: "100%", height: 1, background: "rgba(100,116,139,0.2)" }} />
      </div>
    );
  }

  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const step = width / (values.length - 1);

  const points = values.map((v, i) => {
    const x = i * step;
    const y = height - ((v - min) / range) * height;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  });

  const isPositive = positive ?? values[values.length - 1] >= values[0];
  const stroke = isPositive ? "#22c55e" : "#ef4444";
  const fillColor = isPositive ? "rgba(34,197,94,0.15)" : "rgba(239,68,68,0.15)";

  const pathD = `M ${points.join(" L ")}`;
  const areaD = `${pathD} L ${width},${height} L 0,${height} Z`;
  const gradId = `spark-grad-${isPositive ? "up" : "dn"}`;

  return (
    <svg
      width={responsive ? "100%" : width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={className}
      preserveAspectRatio="none"
      style={responsive ? { display: "block", maxWidth: "100%" } : undefined}
    >
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={stroke} stopOpacity="0.35" />
          <stop offset="100%" stopColor={stroke} stopOpacity="0" />
        </linearGradient>
      </defs>
      {fill && <path d={areaD} fill={`url(#${gradId})`} />}
      <path d={pathD} fill="none" stroke={stroke} strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" />
      {/* End dot */}
      <circle
        cx={points[points.length - 1].split(",")[0]}
        cy={points[points.length - 1].split(",")[1]}
        r={strokeWidth + 0.5}
        fill={stroke}
      />
    </svg>
  );
}
