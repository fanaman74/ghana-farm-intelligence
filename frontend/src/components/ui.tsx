import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import type { DataStatus, Source } from "../types";
import { formatDate, relativeAge } from "../api";

export function StatusChip({ status, label }: { status: DataStatus; label?: string }) {
  const text =
    label ??
    {
      ok: "Live",
      stale: "Stale",
      unavailable: "Unavailable",
      error: "Failed",
      demo: "Sample data",
    }[status];
  return (
    <span className={`status status--${status}`} data-status={status}>
      <span className="status__dot" aria-hidden="true" />
      {text}
    </span>
  );
}

export function Pill({
  children,
  tone = "neutral",
  title,
}: {
  children: ReactNode;
  tone?: "neutral" | "accent" | "teal" | "warn" | "danger" | "good";
  title?: string;
}) {
  return (
    <span className={`pill pill--${tone}`} title={title}>
      {children}
    </span>
  );
}

export function Metric({
  label,
  value,
  unit,
  hint,
  tone,
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  hint?: string;
  tone?: "accent" | "teal" | "danger" | "warn";
}) {
  return (
    <div className="metric">
      <div className="metric__label">{label}</div>
      <div className={`metric__value ${tone ? `metric__value--${tone}` : ""}`}>
        {value}
        {unit ? <span className="metric__unit">{unit}</span> : null}
      </div>
      {hint ? <div className="metric__hint">{hint}</div> : null}
    </div>
  );
}

export function Confidence({ level }: { level: string | null | undefined }) {
  if (!level) return null;
  const map: Record<string, { tone: "good" | "warn" | "danger" | "neutral"; label: string }> = {
    high: { tone: "good", label: "High confidence" },
    medium: { tone: "warn", label: "Medium confidence" },
    low: { tone: "danger", label: "Low confidence" },
    insufficient: { tone: "neutral", label: "Insufficient data" },
  };
  const entry = map[level] ?? { tone: "neutral" as const, label: level };
  return <Pill tone={entry.tone}>{entry.label}</Pill>;
}

export function SourceList({ sources, compact = false }: { sources: Source[]; compact?: boolean }) {
  if (!sources.length) return null;
  const seen = new Set<string>();
  const unique = sources.filter((source) => {
    const key = `${source.provider_id}:${source.dataset}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  return (
    <div className={`sources ${compact ? "sources--compact" : ""}`}>
      {unique.map((source) => (
        <details key={`${source.provider_id}:${source.dataset}`} className="source">
          <summary>
            <span className="source__provider">{source.provider_id.replace(/_/g, " ")}</span>
            <span className="source__dataset">{source.dataset}</span>
          </summary>
          <div className="source__body">
            <dl>
              <div>
                <dt>Licence</dt>
                <dd>{source.licence}</dd>
              </div>
              {source.resolution ? (
                <div>
                  <dt>Resolution</dt>
                  <dd>{source.resolution}</dd>
                </div>
              ) : null}
              {source.period ? (
                <div>
                  <dt>Period</dt>
                  <dd>{source.period}</dd>
                </div>
              ) : null}
              {source.method ? (
                <div>
                  <dt>Method</dt>
                  <dd>{source.method}</dd>
                </div>
              ) : null}
              {source.limitations ? (
                <div>
                  <dt>Limitations</dt>
                  <dd>{source.limitations}</dd>
                </div>
              ) : null}
              <div>
                <dt>Fetched</dt>
                <dd>{relativeAge(source.fetched_at)}</dd>
              </div>
            </dl>
            <a href={source.url} target="_blank" rel="noreferrer noopener" className="link">
              Open source documentation
            </a>
          </div>
        </details>
      ))}
    </div>
  );
}

export function SectionHeader({
  title,
  status,
  retrievedAt,
  right,
}: {
  title: string;
  status?: DataStatus;
  retrievedAt?: string;
  right?: ReactNode;
}) {
  return (
    <div className="section-head">
      <div>
        <h3>{title}</h3>
        {retrievedAt ? <span className="muted small">Fetched {relativeAge(retrievedAt)}</span> : null}
      </div>
      <div className="section-head__right">
        {status ? <StatusChip status={status} /> : null}
        {right}
      </div>
    </div>
  );
}

export function EmptyState({
  title,
  children,
  tone = "neutral",
}: {
  title: string;
  children?: ReactNode;
  tone?: "neutral" | "warn" | "danger";
}) {
  return (
    <div className={`empty empty--${tone}`}>
      <strong>{title}</strong>
      {children ? <div className="empty__body">{children}</div> : null}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline-spinner" role="status">
      <span className="spinner" aria-hidden="true" />
      {label ? <span className="muted small">{label}</span> : null}
    </span>
  );
}

export function Sparkline({
  values,
  width = 220,
  height = 44,
  color = "var(--accent)",
  fill = true,
  label,
}: {
  values: Array<number | null>;
  width?: number;
  height?: number;
  color?: string;
  fill?: boolean;
  label?: string;
}) {
  const numbers = values.map((value) => (value === null || Number.isNaN(value) ? null : value));
  const defined = numbers.filter((value): value is number => value !== null);
  if (!defined.length) return <div className="sparkline-empty">No data</div>;
  const min = Math.min(...defined);
  const max = Math.max(...defined);
  const span = max - min || 1;
  const step = numbers.length > 1 ? width / (numbers.length - 1) : width;
  const points = numbers.map((value, index) => {
    if (value === null) return null;
    const x = index * step;
    const y = height - ((value - min) / span) * (height - 4) - 2;
    return [x, y];
  });
  const path = points
    .map((point, index) => (point ? `${index === 0 ? "M" : "L"}${point[0].toFixed(1)},${point[1].toFixed(1)}` : ""))
    .join(" ")
    .replace(/\s+/g, " ")
    .replace(/ L/g, " L");
  const validPoints = points.filter((point): point is number[] => point !== null);
  const area = fill && validPoints.length > 1
    ? `${path} L${validPoints[validPoints.length - 1][0].toFixed(1)},${height} L${validPoints[0][0].toFixed(1)},${height} Z`
    : null;
  return (
    <svg
      className="sparkline"
      viewBox={`0 0 ${width} ${height}`}
      width="100%"
      height={height}
      preserveAspectRatio="none"
      role="img"
      aria-label={label ?? "Trend"}
    >
      {area ? <path d={area} fill={color} opacity="0.16" /> : null}
      <path d={path} fill="none" stroke={color} strokeWidth="1.8" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

export function BarRow({
  label,
  value,
  max,
  unit,
  color = "var(--teal)",
  secondary,
}: {
  label: string;
  value: number;
  max: number;
  unit?: string;
  color?: string;
  secondary?: string;
}) {
  const percent = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0;
  return (
    <div className="bar-row">
      <div className="bar-row__label">{label}</div>
      <div className="bar-row__track">
        <div className="bar-row__fill" style={{ width: `${percent}%`, background: color }} />
      </div>
      <div className="bar-row__value">
        {value.toLocaleString("en-GH", { maximumFractionDigits: 1 })}
        {unit ? <span className="muted"> {unit}</span> : null}
      </div>
      {secondary ? <div className="bar-row__secondary">{secondary}</div> : null}
    </div>
  );
}

export function Modal({
  open,
  title,
  onClose,
  children,
  footer,
  wide = false,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  const titleId = useId();
  const ref = useRef<HTMLDivElement | null>(null);
  // Callers pass an inline arrow, so depending on onClose re-ran this effect on
  // every parent render — which moved focus back to the trigger and then to the
  // first field while the user was typing (a job poll re-renders every 900 ms).
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const handle = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab" || !ref.current) return;
      const focusable = Array.from(
        ref.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((element) => element.offsetParent !== null || element === document.activeElement);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement as HTMLElement | null;
      if (!active || !ref.current.contains(active)) {
        // Focus escaped into the page behind: bring it back into the dialog.
        event.preventDefault();
        first.focus();
      } else if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handle, true);
    const timer = window.setTimeout(() => {
      ref.current?.querySelector<HTMLElement>("input, button, select, textarea")?.focus();
    }, 30);
    return () => {
      document.removeEventListener("keydown", handle, true);
      window.clearTimeout(timer);
      previous?.focus?.();
    };
  }, [open]);

  if (!open) return null;
  return (
    <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div
        className={`modal ${wide ? "modal--wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        ref={ref}
      >
        <div className="modal__head">
          <h2 id={titleId}>{title}</h2>
          <button type="button" className="icon-button" onClick={onClose} aria-label="Close dialog">
            ✕
          </button>
        </div>
        <div className="modal__body">{children}</div>
        {footer ? <div className="modal__footer">{footer}</div> : null}
      </div>
    </div>
  );
}

export function Toast({
  message,
  tone = "info",
  onDismiss,
}: {
  message: string;
  tone?: "info" | "success" | "error";
  onDismiss: () => void;
}) {
  useEffect(() => {
    const timer = setTimeout(onDismiss, 5200);
    return () => clearTimeout(timer);
  }, [message, onDismiss]);
  return (
    <div className={`toast toast--${tone}`} role="status">
      <span>{message}</span>
      <button type="button" className="icon-button" onClick={onDismiss} aria-label="Dismiss message">
        ✕
      </button>
    </div>
  );
}

export function Field({
  label,
  hint,
  children,
  htmlFor,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
  htmlFor?: string;
}) {
  return (
    <label className="field" htmlFor={htmlFor}>
      <span className="field__label">{label}</span>
      {children}
      {hint ? <span className="field__hint">{hint}</span> : null}
    </label>
  );
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
}: {
  options: Array<{ value: T; label: string }>;
  value: T;
  onChange: (value: T) => void;
  ariaLabel: string;
}) {
  return (
    <div className="segmented" role="group" aria-label={ariaLabel}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          className={`segmented__item ${option.value === value ? "is-active" : ""}`}
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function DataTable({
  head,
  rows,
  caption,
}: {
  head: string[];
  rows: Array<Array<ReactNode>>;
  caption?: string;
}) {
  return (
    <div className="table-wrap">
      <table className="data-table">
        {caption ? <caption className="sr-only">{caption}</caption> : null}
        <thead>
          <tr>
            {head.map((cell) => (
              <th key={cell} scope="col">
                {cell}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              {row.map((cell, cellIndex) => (
                <td key={cellIndex}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function SeverityIcon({ severity }: { severity: string }) {
  const map: Record<string, { symbol: string; label: string }> = {
    warning: { symbol: "▲", label: "Warning" },
    watch: { symbol: "◐", label: "Watch" },
    info: { symbol: "◼", label: "Information" },
    good: { symbol: "●", label: "Favourable" },
  };
  const entry = map[severity] ?? map.info;
  return (
    <span className={`severity severity--${severity}`} aria-label={entry.label} title={entry.label}>
      {entry.symbol}
    </span>
  );
}

export function useNow(intervalMs = 30000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}

export function formatGhanaTime(date: Date): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "Africa/Accra",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

export function formatGhanaDate(date: Date): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "Africa/Accra",
    weekday: "short",
    day: "2-digit",
    month: "short",
  }).format(date);
}

export { formatDate, relativeAge };
