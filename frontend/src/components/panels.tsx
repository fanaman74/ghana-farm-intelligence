import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  api,
  formatDate,
  formatNumber,
  formatShortDate,
  relativeAge,
} from "../api";
import type {
  Analysis,
  CropEvaluation,
  CropSummary,
  Farm,
  Job,
  LayerState,
  Recommendation,
  Source,
  CropComponent,
} from "../types";
import {
  BarRow,
  Confidence,
  DataTable,
  EmptyState,
  Field,
  Metric,
  Modal,
  Pill,
  SectionHeader,
  Segmented,
  SeverityIcon,
  SourceList,
  Sparkline,
  StatusChip,
  Toast,
  formatGhanaDate,
  formatGhanaTime,
  useNow,
} from "./ui";
import { LANGUAGES, type LanguageCode } from "../i18n";

/* ------------------------------------------------------------------ helpers */

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function useSectionBody(section: { status: string; message?: string | null } | undefined, children: ReactNode) {
  if (!section) {
    return <EmptyState title="No analysis yet">Select a farm and run “View data for this area”.</EmptyState>;
  }
  if (section.status === "unavailable" || section.status === "error") {
    return (
      <EmptyState title={section.status === "unavailable" ? "Not available" : "Could not be computed"} tone="warn">
        {section.message ?? "No value is shown rather than an estimate."}
      </EmptyState>
    );
  }
  return <>{children}</>;
}

function severityLabel(severity: string): string {
  return { warning: "Warning", watch: "Watch", info: "Information", good: "Favourable" }[severity] ?? severity;
}

function RecommendationCard({ item, compact = false }: { item: Recommendation; compact?: boolean }) {
  return (
    <article className={`rec rec--${item.severity}`}>
      <div className="rec__head">
        <SeverityIcon severity={item.severity} />
        <h4>{item.title}</h4>
        <Pill tone={item.severity === "warning" ? "danger" : item.severity === "good" ? "good" : "neutral"}>
          {severityLabel(item.severity)}
        </Pill>
      </div>
      <p className="rec__action">{item.action}</p>
      {!compact ? (
        <>
          <p className="rec__why">
            <span className="rec__why-label">Why:</span> {item.why}
          </p>
          <div className="rec__evidence">
            {item.evidence.map((evidence, index) => (
              <span key={index} className="evidence-chip" title={evidence.detail ?? undefined}>
                <span className="evidence-chip__label">{evidence.label}</span>
                <span className="evidence-chip__value">{String(evidence.value ?? "—")}</span>
                <span className="evidence-chip__source">{evidence.source.replace(/_/g, " ")}</span>
              </span>
            ))}
          </div>
          <div className="rec__foot">
            <Confidence level={item.confidence} />
            <details>
              <summary>Limitations</summary>
              <p className="small muted">{item.limitations}</p>
            </details>
          </div>
        </>
      ) : null}
    </article>
  );
}

function MetricsGrid({ children }: { children: ReactNode }) {
  return <div className="metrics-grid">{children}</div>;
}

function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="skeleton" aria-hidden="true">
      {Array.from({ length: lines }).map((_, index) => (
        <div key={index} className="skeleton__line" style={{ width: `${88 - index * 12}%` }} />
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------- top bar */

export function TopBar({
  farms,
  selectedFarmId,
  onSelectFarm,
  onSearch,
  onSettings,
  online,
  theme,
  onToggleTheme,
  dataAge,
  onToggleLayers,
  onTogglePanel,
  layersOpen,
  panelOpen,
}: {
  farms: Farm[];
  selectedFarmId: string | null;
  onSelectFarm: (id: string) => void;
  onSearch: () => void;
  onSettings: () => void;
  online: boolean;
  theme: "dark" | "daylight";
  onToggleTheme: () => void;
  dataAge: string | null;
  onToggleLayers: () => void;
  onTogglePanel: () => void;
  layersOpen: boolean;
  panelOpen: boolean;
}) {
  const now = useNow(20000);
  return (
    <header className="topbar">
      <div className="topbar__brand">
        <svg viewBox="0 0 64 64" className="brand-mark" aria-hidden="true">
          <rect width="64" height="64" rx="14" fill="#12321f" />
          <path d="M18 44c0-14 10-24 28-24 0 16-10 26-24 26" fill="none" stroke="#e0a33e" strokeWidth="3.5" strokeLinecap="round" />
          <path d="M18 46c6-12 12-18 22-22" fill="none" stroke="#3fa88f" strokeWidth="3" strokeLinecap="round" />
          <circle cx="46" cy="46" r="3.2" fill="#e0a33e" />
        </svg>
        <div className="topbar__title">
          <strong>Ghana Farm Intelligence</strong>
          <span className="topbar__sub">
            {formatGhanaTime(now)} · {formatGhanaDate(now)} · Accra (GMT)
          </span>
        </div>
      </div>

      <div className="topbar__center">
        <button type="button" className="search-trigger" onClick={onSearch} aria-label="Search a Ghana locality, district or region">
          <span className="search-trigger__icon" aria-hidden="true">⌕</span>
          <span>Search a locality, district or region</span>
          <kbd>/</kbd>
        </button>
      </div>

      <div className="topbar__actions">
        <span className={`conn ${online ? "conn--on" : "conn--off"}`} title={online ? "Connected — live providers reachable" : "Offline — saved data only"}>
          <span className="conn__dot" aria-hidden="true" />
          {online ? "Online" : "Offline"}
        </span>
        {dataAge ? <span className="tiny muted" title="Age of the displayed analysis">Analysis {dataAge}</span> : null}
        <select
          className="farm-select"
          value={selectedFarmId ?? ""}
          onChange={(event) => event.target.value && onSelectFarm(event.target.value)}
          aria-label="Select farm"
        >
          <option value="">{farms.length ? "Select a farm…" : "No farms yet"}</option>
          {farms.map((farm) => (
            <option key={farm.id} value={farm.id}>
              {farm.name} · {formatNumber(farm.area_ha, 2)} ha
            </option>
          ))}
        </select>
        <button
          type="button"
          className={`icon-button ${layersOpen ? "is-active" : ""}`}
          onClick={onToggleLayers}
          aria-label="Toggle layers panel"
          aria-pressed={layersOpen}
          title="Layers"
        >
          ▤
        </button>
        <button
          type="button"
          className={`icon-button ${panelOpen ? "is-active" : ""}`}
          onClick={onTogglePanel}
          aria-label="Toggle farm panel"
          aria-pressed={panelOpen}
          title="Farm panel"
        >
          ▥
        </button>
        <button type="button" className="icon-button" onClick={onToggleTheme} aria-label="Switch daylight mode" title="Daylight / dark mode">
          {theme === "dark" ? "☀" : "☾"}
        </button>
        <button type="button" className="icon-button" onClick={onSettings} aria-label="Settings and data sources" title="Settings">
          ⚙
        </button>
      </div>
    </header>
  );
}

/* -------------------------------------------------------------- layers panel */

export function LayersPanel({
  layers,
  onChange,
  onClose,
  analysis,
}: {
  layers: LayerState[];
  onChange: (id: string, patch: Partial<LayerState>) => void;
  onClose: () => void;
  analysis: Analysis | null;
}) {
  const groups: Array<{ title: string; ids: string[] }> = [
    { title: "Base map", ids: ["basemap", "districts", "roads", "water", "farms"] },
    { title: "Satellite imagery", ids: ["true-color", "ndvi", "ndmi"] },
    { title: "Evidence layers", ids: ["soil-ph", "soil-carbon", "soil-texture", "cropland", "slope", "elevation", "evapotranspiration"] },
  ];
  const byId = Object.fromEntries(layers.map((layer) => [layer.id, layer]));
  return (
    <aside className="panel panel--left" aria-label="Map layers">
      <div className="panel__head">
        <h2>Layers</h2>
        <button type="button" className="icon-button" onClick={onClose} aria-label="Close layers panel">
          ✕
        </button>
      </div>
      <div className="panel__body">
        {groups.map((group) => (
          <section key={group.title} className="layer-group">
            <h3>{group.title}</h3>
            {group.ids.map((id) => {
              const layer = byId[id];
              if (!layer) return null;
              return (
                <div key={id} className={`layer ${layer.available ? "" : "layer--disabled"}`}>
                  <div className="layer__head">
                    <label className="switch">
                      <input
                        type="checkbox"
                        checked={layer.visible}
                        disabled={!layer.available && !layer.loading}
                        onChange={(event) => onChange(id, { visible: event.target.checked })}
                      />
                      <span className="switch__track" aria-hidden="true" />
                      <span className="layer__name">{layer.name}</span>
                    </label>
                    {layer.loading ? (
                      <Pill tone="neutral">Loading…</Pill>
                    ) : layer.available ? (
                      <span className="tiny muted">{Math.round(layer.opacity * 100)}%</span>
                    ) : (
                      <Pill tone="warn">No data</Pill>
                    )}
                  </div>
                  {layer.available ? (
                    <input
                      className="range"
                      type="range"
                      min={0}
                      max={1}
                      step={0.05}
                      value={layer.opacity}
                      aria-label={`${layer.name} opacity`}
                      onChange={(event) => onChange(id, { opacity: Number(event.target.value) })}
                    />
                  ) : (
                    <p className="tiny muted">{layer.unavailableReason}</p>
                  )}
                  <p className="layer__desc">{layer.description}</p>
                  {layer.resolutionM ? (
                    <p className="layer__meta tiny muted">
                      {layer.resolutionM} m grid
                      {layer.valueRange
                        ? ` · ${formatNumber(layer.valueRange[0], 2)}–${formatNumber(layer.valueRange[1], 2)} ${layer.unit ?? ""}`
                        : ""}
                    </p>
                  ) : layer.available && layer.kind === "derived" ? (
                    <p className="layer__meta tiny muted">Toggle on to render this field’s grid</p>
                  ) : null}
                  {layer.legend ? (
                    <div className="legend">
                      {layer.legend.map((entry) => (
                        <span key={entry.label} className="legend__item">
                          <i style={{ background: entry.color }} aria-hidden="true" />
                          {entry.label}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </div>
              );
            })}
          </section>
        ))}
        <div className="layer-footnote">
          <p className="tiny muted">
            Vector base map: OpenStreetMap via Geofabrik (ODbL). Boundaries: geoBoundaries. Imagery: Copernicus
            Sentinel-2, analysed over the selected polygon. No public tile server is prefetched.
          </p>
          {analysis ? (
            <p className="tiny muted">
              Imagery processed {relativeAge(analysis.generated_at)} · source date{" "}
              {analysis.sections.vegetation?.data?.latest?.date ?? "—"}
            </p>
          ) : null}
        </div>
      </div>
    </aside>
  );
}

/* --------------------------------------------------------------- farm panel */

const TABS = [
  { id: "overview", label: "Overview", key: "A" },
  { id: "soil", label: "Soil", key: "B" },
  { id: "weather", label: "Weather", key: "C" },
  { id: "crops", label: "Crops", key: "D" },
  { id: "health", label: "Health", key: "E" },
  { id: "water", label: "Water", key: "F" },
  { id: "calendar", label: "Calendar", key: "G" },
] as const;

export type TabId = (typeof TABS)[number]["id"];

export function FarmPanel({
  farm,
  analysis,
  job,
  partial,
  tab,
  onTab,
  onRefresh,
  onAnalyze,
  onAnalyzeField,
  onExport,
  onEdit,
  onDelete,
  onSplit,
  onAddSoilTest,
  onAddRecord,
  crops,
  onSelectCrop,
  offlineSavedAt,
  onClose,
  busy,
  cropsBusy,
  activeFieldId,
}: {
  farm: Farm | null;
  analysis: Analysis | null;
  job: Job | null;
  partial: Analysis["sections"] | null;
  tab: TabId;
  onTab: (tab: TabId) => void;
  onRefresh: () => void;
  onAnalyze: () => void;
  onAnalyzeField: (fieldId: string | null) => void;
  onExport: (format: "geojson" | "kml" | "csv" | "pdf") => void;
  onEdit: () => void;
  onDelete: () => void;
  onSplit: () => void;
  onAddSoilTest: (payload: Record<string, unknown>) => Promise<void>;
  onAddRecord: (payload: Record<string, unknown>) => Promise<void>;
  crops: CropSummary[];
  onSelectCrop: (cropId: string) => void;
  offlineSavedAt: string | null;
  onClose: () => void;
  busy: boolean;
  cropsBusy: boolean;
  activeFieldId: string | null;
}) {
  const [expanded, setExpanded] = useState(true);
  const progress = job && job.status !== "complete" ? job : null;
  const sections = analysis?.sections ?? partial ?? undefined;
  const refreshed = analysis?.generated_at ?? null;

  return (
    <aside className={`panel panel--right ${expanded ? "" : "panel--collapsed"}`} aria-label="Selected farm intelligence">
      <div className="panel__head">
        <div>
          <h2>{farm?.name ?? "No farm selected"}</h2>
          {farm ? (
            <p className="tiny muted">
              {analysis?.farm.district || farm.district || "District unknown"} ·{" "}
              {analysis?.farm.region || farm.region || "Region unknown"}
              {analysis?.farm.zone ? ` · ${analysis.farm.zone.name}` : ""}
            </p>
          ) : (
            <p className="tiny muted">Draw a boundary, search for a place, or try the sample farm.</p>
          )}
        </div>
        <div className="panel__head-actions">
          <button
            type="button"
            className="icon-button"
            onClick={() => setExpanded((value) => !value)}
            aria-label={expanded ? "Collapse farm panel" : "Expand farm panel"}
          >
            {expanded ? "▾" : "▴"}
          </button>
          <button type="button" className="icon-button" onClick={onClose} aria-label="Close farm panel">
            ✕
          </button>
        </div>
      </div>

      {farm ? (
        <>
          <div className="farm-summary" aria-live="polite">
            <div className="farm-summary__main">
              <span className="farm-summary__area">{formatNumber(farm.area_ha, 2)} ha</span>
              <span className="muted small">
                {formatNumber(farm.area_ha * 2.4710538, 1)} acres · {formatNumber(farm.perimeter_m, 0)} m perimeter
              </span>
            </div>
            <div className="farm-summary__meta">
              <Pill tone={analysis?.summary.data_status.core_complete ? "teal" : "warn"}>
                {analysis?.summary.data_status.core_complete ? "Core data complete" : "Core data incomplete"}
              </Pill>
              {analysis?.summary.top_actions?.[0] ? (
                <span className="farm-summary__action">
                  <strong>Next:</strong> {analysis.summary.top_actions[0].title}
                </span>
              ) : null}
            </div>
            {offlineSavedAt ? <p className="tiny muted">Saved on device {relativeAge(offlineSavedAt)}</p> : null}
          </div>

          <nav className="tabs" aria-label="Farm intelligence sections">
            {TABS.map((item) => (
              <button
                key={item.id}
                type="button"
                className={`tabs__item ${tab === item.id ? "is-active" : ""}`}
                aria-current={tab === item.id ? "page" : undefined}
                onClick={() => onTab(item.id)}
              >
                <span className="tabs__key" aria-hidden="true">{item.key}</span>
                {item.label}
              </button>
            ))}
          </nav>

          {progress ? (
            <div className="progress" role="status" aria-live="polite">
              <div className="progress__bar">
                <div className="progress__fill" style={{ width: `${Math.max(4, progress.progress * 100)}%` }} />
              </div>
              <span className="tiny">
                {progress.message ?? "Analysing"}… {Math.round(progress.progress * 100)}%
              </span>
            </div>
          ) : null}

          <div className="panel__body panel__body--tabs">
            {!analysis && !progress ? (
              <div className="analyze-cta">
                <p>
                  This farm has no analysis loaded. Selecting a farm normally starts an analysis automatically; you can
                  also run it now.
                </p>
                <button type="button" className="button button--primary" onClick={onAnalyze} disabled={busy}>
                  View data for this area
                </button>
              </div>
            ) : null}

            {tab === "overview" ? (
              <TabOverview
                analysis={analysis}
                farm={farm}
                job={progress}
                onRefresh={onRefresh}
                onExport={onExport}
                onEdit={onEdit}
                onDelete={onDelete}
                onSplit={onSplit}
                onAnalyzeField={onAnalyzeField}
                activeFieldId={activeFieldId}
              />
            ) : null}
            {tab === "soil" ? (
              <TabSoil analysis={analysis} onAddSoilTest={onAddSoilTest} />
            ) : null}
            {tab === "weather" ? <TabWeather analysis={analysis} /> : null}
            {tab === "crops" ? (
              <TabCrops
                analysis={analysis}
                crops={crops}
                onSelectCrop={onSelectCrop}
                busy={cropsBusy}
                onAnalyze={onAnalyze}
              />
            ) : null}
            {tab === "health" ? <TabHealth analysis={analysis} /> : null}
            {tab === "water" ? <TabWater analysis={analysis} /> : null}
            {tab === "calendar" ? <TabCalendar analysis={analysis} onAddRecord={onAddRecord} farm={farm} onExport={onExport} /> : null}

            {analysis ? (
              <div className="analysis-footer">
                <p className="tiny muted">
                  Generated {relativeAge(refreshed)} · {analysis.elapsed_ms ? `${(analysis.elapsed_ms / 1000).toFixed(1)} s` : ""} ·
                  analysis v{analysis.version}
                </p>
                <p className="tiny muted">{analysis.disclaimer}</p>
              </div>
            ) : null}
          </div>
        </>
      ) : (
        <div className="panel__body">
          <EmptyState title="No farm selected">
            Use <strong>Draw farm</strong> to tap the boundary, import a GeoJSON/KML/GPX file, search a locality, or try the
            sample farm. Every analysis uses the polygon itself, not the nearest town.
          </EmptyState>
        </div>
      )}
    </aside>
  );
}

/* ------------------------------------------------------------------ overview */

function TabOverview({
  analysis,
  farm,
  job,
  onRefresh,
  onExport,
  onEdit,
  onDelete,
  onSplit,
  onAnalyzeField,
  activeFieldId,
}: {
  analysis: Analysis | null;
  farm: Farm;
  job: Job | null;
  onRefresh: () => void;
  onExport: (format: "geojson" | "kml" | "csv" | "pdf") => void;
  onEdit: () => void;
  onDelete: () => void;
  onSplit: () => void;
  onAnalyzeField: (fieldId: string | null) => void;
  activeFieldId: string | null;
}) {
  if (!analysis) {
    return job ? <Skeleton lines={5} /> : null;
  }
  const { farm: info, summary, sections } = analysis;
  const soil = sections.soil?.data;
  const rainfall = sections.rainfall?.data;
  const vegetation = sections.vegetation?.data;
  const terrain = sections.terrain?.data;
  const weather = sections.weather?.data;
  const crops = sections.crops?.data;
  const selected = crops?.selected ?? null;
  const statuses = [
    ["Soil", sections.soil?.status],
    ["Weather", sections.weather?.status],
    ["Rainfall", sections.rainfall?.status],
    ["Imagery", sections.vegetation?.status],
    ["Terrain", sections.terrain?.status],
    ["Land cover", sections.landcover?.status],
    ["WaPOR", sections.water?.status],
  ] as Array<[string, string | undefined]>;

  return (
    <div className="stack">
      {farm.is_demo ? (
        <div className="banner banner--warn">Sample data — not for farm decisions.</div>
      ) : null}

      <MetricsGrid>
        <Metric label="Area" value={formatNumber(info.area_ha, 2)} unit="ha" hint={`${formatNumber(info.area_ha * 2.471, 1)} acres`} />
        <Metric label="Perimeter" value={formatNumber(info.perimeter_m, 0)} unit="m" hint={`${info.vertex_count} vertices`} />
        <Metric label="Annual rainfall" value={info.annual_rainfall_mm ? formatNumber(info.annual_rainfall_mm, 0) : "—"} unit="mm" hint="1991–2020 CHIRPS" />
        <Metric label="Soil pH (0–30 cm)" value={soil?.modelled?.profile_0_30?.phh2o?.value ? formatNumber(soil.modelled.profile_0_30.phh2o.value, 2) : "—"} hint={soil?.modelled?.acidity_class ?? "estimate"} />
        <Metric
          label="Latest NDVI"
          value={vegetation?.latest?.ndvi?.mean !== undefined && vegetation?.latest?.ndvi?.mean !== null ? formatNumber(vegetation.latest.ndvi.mean, 2) : "—"}
          hint={vegetation?.latest?.date ? `Sentinel-2 ${formatShortDate(vegetation.latest.date)}` : "no usable scene"}
        />
        <Metric label="Slope" value={terrain?.slope_percent ? formatNumber(terrain.slope_percent.mean, 1) : "—"} unit="%" hint={terrain?.erosion_screening?.level ? `${terrain.erosion_screening.level} erosion screening` : undefined} />
      </MetricsGrid>

      <div className="coverage-strip">
        {statuses.map(([label, status]) => (
          <span key={label} className="coverage-strip__item">
            <span>{label}</span>
            <StatusChip status={(status ?? "unavailable") as never} label={status === "ok" ? "Live" : status === "stale" ? "Stale" : status === "error" ? "Failed" : "No data"} />
          </span>
        ))}
      </div>

      {summary.top_actions.length ? (
        <section>
          <div className="section-head">
            <div>
              <h3>Priority actions</h3>
              <span className="muted small">Each item shows its reason, source and confidence.</span>
            </div>
            <button type="button" className="button button--ghost button--small" onClick={onRefresh}>
              Refresh
            </button>
          </div>
          <div className="stack stack--tight">
            {summary.top_actions.map((item) => (
              <RecommendationCard key={item.id} item={item} />
            ))}
          </div>
        </section>
      ) : (
        <EmptyState title="No recommendations yet">This usually means key data were unavailable. Check the data status chips above.</EmptyState>
      )}

      {sections.recommendations?.data?.recommendations?.length ? (
        <details className="details">
          <summary>All recommendations ({sections.recommendations.data.recommendations.length})</summary>
          <div className="stack stack--tight">
            {sections.recommendations.data.recommendations.slice(3).map((item) => (
              <RecommendationCard key={item.id} item={item} compact />
            ))}
          </div>
        </details>
      ) : null}

      <section>
        <h3>Farm details</h3>
        <DataTable
          head={["Field", "Value"]}
          rows={[
            ["Location", `${formatNumber(info.centroid.lat, 5)}, ${formatNumber(info.centroid.lon, 5)}`],
            ["Region / district", `${info.region ?? "—"} / ${info.district ?? "—"}`],
            ["Agro-ecological zone", `${info.zone.name} (${info.zone.season_type}, ${info.zone.confidence} confidence)`],
            ["Current crop", selected ? `${selected.name}${selected.perennial ? " · perennial" : ""}` : info.crop_id ?? "not set"],
            ["Planting date", info.planting_date ? formatDate(info.planting_date) : "not set"],
            ["Irrigation", info.irrigation ?? "—"],
            ["Geometry", info.geometry_label],
            ["Current conditions", weather?.current ? `${weather.current.temperature_c} °C, ${weather.current.weather}, wind ${weather.current.wind_kmh} km/h` : "—"],
            ["Rain, last 30 available days", rainfall?.windows?.last_30_days ? `${formatNumber(rainfall.windows.last_30_days.total_mm, 1)} mm (${rainfall.anomalies?.last_30_days?.percent_of_normal ?? "—"}% of normal, to ${formatDate(rainfall.latest_available)})` : "—"],
          ]}
        />
      </section>

      <section>
        <h3>Boundary tools</h3>
        <div className="button-row">
          <button type="button" className="button button--ghost" onClick={onEdit}>
            Edit vertices
          </button>
          <button type="button" className="button button--ghost" onClick={onSplit}>
            Split into fields
          </button>
          <button type="button" className="button button--ghost" onClick={() => onExport("geojson")}>
            Export GeoJSON
          </button>
          <button type="button" className="button button--danger-ghost" onClick={onDelete}>
            Delete farm
          </button>
        </div>
      </section>

      <section>
        <div className="section-head">
          <div>
            <h3>Fields in this farm</h3>
            <span className="muted small">
              {farm.fields?.length
                ? "Analyse the whole farm or one field. Each field is a saved polygon with its own analysis."
                : "No fields yet. Draw a split line to divide the farm into fields."}
            </span>
          </div>
        </div>
        {farm.fields?.length ? (
          <>
            <DataTable
              head={["Field", "Area", "Crop", "Action"]}
              rows={farm.fields.map((field) => [
                field.name,
                `${formatNumber(field.area_ha, 2)} ha`,
                field.crop_id ? field.crop_id.replace(/_/g, " ") : "—",
                <button
                  key={field.id}
                  type="button"
                  className={`button button--small ${activeFieldId === field.id ? "button--primary" : "button--ghost"}`}
                  onClick={() => onAnalyzeField(field.id)}
                >
                  {activeFieldId === field.id ? "Showing" : "View data"}
                </button>,
              ])}
            />
            <div className="button-row" style={{ marginTop: 8 }}>
              <button
                type="button"
                className={`button button--small ${activeFieldId ? "button--ghost" : "button--primary"}`}
                onClick={() => onAnalyzeField(null)}
              >
                {activeFieldId ? "Back to whole farm" : "Whole farm is shown"}
              </button>
            </div>
            <p className="tiny muted">
              Comparison: areas and crops are listed per field. A full side-by-side metric comparison across
              fields is not implemented in this release; open each field to see its own sourced values.
            </p>
          </>
        ) : null}
      </section>
    </div>
  );
}

/* ---------------------------------------------------------------------- soil */

function TabSoil({ analysis, onAddSoilTest }: { analysis: Analysis | null; onAddSoilTest: (payload: Record<string, unknown>) => Promise<void> }) {
  const section = analysis?.sections.soil;
  const [depth, setDepth] = useState<"0-30" | "0-5cm" | "5-15cm" | "15-30cm">("0-30");
  const [formOpen, setFormOpen] = useState(false);
  const modelled = section?.data.modelled;
  const measured = section?.data.measured;
  const properties: Array<[string, string, string]> = [
    ["phh2o", "pH", "pH"],
    ["soc", "Organic carbon", "g/kg"],
    ["nitrogen", "Total nitrogen", "g/kg"],
    ["clay", "Clay", "%"],
    ["sand", "Sand", "%"],
    ["silt", "Silt", "%"],
    ["cec", "CEC", "cmol(c)/kg"],
    ["bdod", "Bulk density", "kg/dm³"],
    ["cfvo", "Coarse fragments", "vol %"],
  ];

  return (
    <div className="stack">
      {useSectionBody(section, (
        <>
          <div className="section-head">
            <div>
              <h3>Soil profile</h3>
              <p className="muted small">
                {modelled?.texture_class ? `Approximate USDA class: ${modelled.texture_class}` : "Texture unavailable"}
                {modelled?.acidity_class ? ` · ${modelled.acidity_class}` : ""}
              </p>
            </div>
            <Segmented
              ariaLabel="Soil depth"
              value={depth}
              onChange={setDepth}
              options={[
                { value: "0-30", label: "0–30 cm" },
                { value: "0-5cm", label: "0–5" },
                { value: "5-15cm", label: "5–15" },
                { value: "15-30cm", label: "15–30" },
              ]}
            />
          </div>

          {measured ? (
            <div className="measured-card">
              <div className="measured-card__head">
                <Pill tone="teal">Laboratory measurement</Pill>
                <span className="tiny muted">
                  Sampled {formatDate(measured.sampled_on as string)} {measured.lab_name ? `· ${measured.lab_name}` : ""}
                </span>
              </div>
              <p className="small">{section?.data.precedence}</p>
              <div className="measured-values">
                {measured.ph !== null && measured.ph !== undefined ? <span>pH <strong>{formatNumber(Number(measured.ph), 2)}</strong></span> : null}
                {measured.soc_gkg ? <span>SOC <strong>{formatNumber(Number(measured.soc_gkg), 1)}</strong> g/kg</span> : null}
                {measured.total_n_gkg ? <span>N <strong>{formatNumber(Number(measured.total_n_gkg), 2)}</strong> g/kg</span> : null}
                {measured.p_mgkg ? <span>P <strong>{formatNumber(Number(measured.p_mgkg), 1)}</strong> mg/kg</span> : null}
                {measured.texture_class ? <span><strong>{String(measured.texture_class)}</strong></span> : null}
              </div>
            </div>
          ) : (
            <div className="banner banner--info">
              No laboratory soil test is recorded for this farm. Estimates below are model predictions at 250 m — a
              soil test is the only way to confirm them.
            </div>
          )}

          {depth === "0-30" && modelled?.profile_0_30 ? (
            <>
              <DataTable
                head={["Property", "Value", "Unit", "Valid coverage", "Grid"]}
                rows={properties.map(([key]) => {
                  const entry = modelled.profile_0_30[key];
                  const uncertainty = modelled.uncertainty?.[key]?.depths?.["0-5cm"];
                  return [
                    entry?.label ?? key,
                    <span className="numeric">{entry?.value !== undefined ? formatNumber(entry.value, 2) : "—"}</span>,
                    entry?.unit ?? "—",
                    entry ? `${Math.round((entry.valid_fraction ?? 0) * 100)}% · ${entry.pixels_in_polygon} px` : "—",
                    entry?.resolution ? `${entry.resolution.nominal_m} m` : "—",
                  ] as ReactNode[];
                })}
              />
              {modelled.uncertainty?.phh2o?.depths?.["0-5cm"] ? (
                <div className="ph-interval">
                  <div className="ph-interval__head">
                    <span>pH 90% prediction interval (topsoil)</span>
                    <span className="numeric">
                      {formatNumber(modelled.uncertainty.phh2o.depths["0-5cm"].q05, 2)} –{" "}
                      {formatNumber(modelled.uncertainty.phh2o.depths["0-5cm"].q95, 2)}
                    </span>
                  </div>
                  <div className="ph-interval__track" aria-hidden="true">
                    <span className="ph-interval__band" />
                    <span
                      className="ph-interval__marker"
                      style={{
                        left: `${Math.min(
                          98,
                          Math.max(
                            2,
                            ((modelled.profile_0_30.phh2o?.value ?? 6) - 3) / 8 * 100,
                          ),
                        )}%`,
                      }}
                    />
                  </div>
                  <div className="ph-interval__scale tiny muted">
                    <span>pH 3</span><span>5.5</span><span>7</span><span>11</span>
                  </div>
                  <p className="tiny muted">{modelled.uncertainty.phh2o.note}</p>
                </div>
              ) : null}
            </>
          ) : (
            <DataTable
              head={["Property", "Value", "Unit", "Valid coverage"]}
              rows={properties.map(([key]) => {
                const prop = modelled?.depths?.[key];
                const entry = prop?.values?.[depth];
                return [
                  prop?.label ?? key,
                  <span className="numeric">{entry?.mean !== null && entry?.mean !== undefined ? formatNumber(entry.mean, 2) : "—"}</span>,
                  prop?.unit ?? "—",
                  entry ? `${Math.round((entry.valid_fraction ?? 0) * 100)}%` : "—",
                ] as ReactNode[];
              })}
            />
          )}

          <div className="callout">
            <h4>What this can and cannot tell you</h4>
            <ul className="list">
              {(modelled?.limitations ?? []).map((item) => (
                <li key={item}>{item}</li>
              ))}
              <li>
                {modelled?.coverage?.sub_field_detail
                  ? "The 250 m grid is smaller than this field, but it still cannot resolve soil variation within it."
                  : "This field is smaller than one 250 m grid cell: the values describe that cell, not within-field variation."}
              </li>
            </ul>
          </div>

          <div className="button-row">
            <button type="button" className="button button--ghost" onClick={() => setFormOpen(true)}>
              Add a soil test result
            </button>
          </div>
        </>
      ))}

      <SourceList sources={section?.sources ?? []} />

      <Modal open={formOpen} title="Record a laboratory soil test" onClose={() => setFormOpen(false)}>
        <SoilTestForm
          onSubmit={async (payload) => {
            await onAddSoilTest(payload);
            setFormOpen(false);
          }}
          onCancel={() => setFormOpen(false)}
        />
      </Modal>
    </div>
  );
}

function SoilTestForm({ onSubmit, onCancel }: { onSubmit: (payload: Record<string, unknown>) => Promise<void>; onCancel: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const formRef = useRef<HTMLFormElement | null>(null);
  return (
    <form
      ref={formRef}
      className="form"
      onSubmit={async (event) => {
        event.preventDefault();
        setBusy(true);
        setError(null);
        const data = new FormData(event.currentTarget);
        const payload: Record<string, unknown> = { sampled_on: data.get("sampled_on") || todayIso() };
        for (const key of ["lab_name", "texture_class", "source_url", "notes"]) {
          const value = data.get(key);
          if (value) payload[key] = value;
        }
        for (const key of ["ph", "soc_gkg", "total_n_gkg", "p_mgkg", "k_mgkg", "cec_cmolkg", "clay_pct", "sand_pct", "silt_pct"]) {
          const value = data.get(key);
          if (value !== null && value !== "") payload[key] = Number(value);
        }
        const from = data.get("depth_from_cm");
        const to = data.get("depth_to_cm");
        if (from !== null && from !== "") payload.depth_from_cm = Number(from);
        if (to !== null && to !== "") payload.depth_to_cm = Number(to);
        try {
          await onSubmit(payload);
        } catch (submitError) {
          setError(submitError instanceof Error ? submitError.message : "Could not save the soil test");
        } finally {
          setBusy(false);
        }
      }}
    >
      <p className="small muted">
        Measured values take precedence over SoilGrids estimates in guidance. Leave unknown fields blank rather than
        guessing.
      </p>
      <div className="form-grid">
        <Field label="Sampled on" htmlFor="sampled_on">
          <input id="sampled_on" name="sampled_on" type="date" defaultValue={todayIso()} required />
        </Field>
        <Field label="Laboratory" htmlFor="lab_name">
          <input id="lab_name" name="lab_name" placeholder="e.g. Soil Research Institute" />
        </Field>
        <Field label="Depth from (cm)" htmlFor="depth_from_cm">
          <input id="depth_from_cm" name="depth_from_cm" type="number" min={0} max={200} defaultValue={0} />
        </Field>
        <Field label="Depth to (cm)" htmlFor="depth_to_cm">
          <input id="depth_to_cm" name="depth_to_cm" type="number" min={0} max={200} defaultValue={20} />
        </Field>
        <Field label="pH" htmlFor="ph">
          <input id="ph" name="ph" type="number" step="0.01" min={0} max={14} />
        </Field>
        <Field label="Organic carbon (g/kg)" htmlFor="soc_gkg">
          <input id="soc_gkg" name="soc_gkg" type="number" step="0.1" min={0} />
        </Field>
        <Field label="Total N (g/kg)" htmlFor="total_n_gkg">
          <input id="total_n_gkg" name="total_n_gkg" type="number" step="0.01" min={0} />
        </Field>
        <Field label="Available P (mg/kg)" htmlFor="p_mgkg">
          <input id="p_mgkg" name="p_mgkg" type="number" step="0.1" min={0} />
        </Field>
        <Field label="Exchangeable K (mg/kg)" htmlFor="k_mgkg">
          <input id="k_mgkg" name="k_mgkg" type="number" step="0.1" min={0} />
        </Field>
        <Field label="CEC (cmol/kg)" htmlFor="cec_cmolkg">
          <input id="cec_cmolkg" name="cec_cmolkg" type="number" step="0.01" min={0} />
        </Field>
        <Field label="Texture class" htmlFor="texture_class">
          <input id="texture_class" name="texture_class" placeholder="e.g. sandy loam" />
        </Field>
        <Field label="Report URL" htmlFor="source_url">
          <input id="source_url" name="source_url" type="url" placeholder="https://…" />
        </Field>
      </div>
      <Field label="Notes" htmlFor="notes">
        <textarea id="notes" name="notes" rows={2} maxLength={2000} />
      </Field>
      {error ? <p className="form-error">{error}</p> : null}
      <div className="button-row">
        <button type="button" className="button button--ghost" onClick={onCancel}>
          Cancel
        </button>
        <button type="submit" className="button button--primary" disabled={busy}>
          {busy ? "Saving…" : "Save soil test"}
        </button>
      </div>
    </form>
  );
}

/* ------------------------------------------------------------------- weather */

function TabWeather({ analysis }: { analysis: Analysis | null }) {
  const section = analysis?.sections.weather;
  const rainfall = analysis?.sections.rainfall?.data;
  const data = section?.data;
  const today = todayIso();
  const forecast = (data?.daily ?? []).filter((day) => String(day.date) >= today).slice(0, 10);
  const analytics = data?.analytics;

  return (
    <div className="stack">
      {useSectionBody(section, (
        <>
          <div className="section-head">
            <div>
              <h3>Weather and rainfall</h3>
              <p className="muted small">
                Model grid cell {data?.grid.latitude?.toFixed(3)}, {data?.grid.longitude?.toFixed(3)} · ~
                {data?.grid.nominal_resolution_km} km · {data?.grid_distance_km ?? "—"} km from the farm centroid
              </p>
            </div>
            <StatusChip status={section!.status} />
          </div>

          <MetricsGrid>
            <Metric label="Temperature" value={data?.current.temperature_c ?? "—"} unit="°C" hint={`Feels like ${data?.current.apparent_temperature_c ?? "—"} °C`} />
            <Metric label="Humidity" value={data?.current.humidity_pct ?? "—"} unit="%" hint={String(data?.current.weather ?? "")} />
            <Metric label="Rain now" value={data?.current.precipitation_mm ?? "—"} unit="mm" hint={`Wind ${data?.current.wind_kmh ?? "—"} km/h`} />
            <Metric label="Soil moisture (0–10 cm)" value={data?.current.soil_moisture_0_10cm ?? "—"} unit="m³/m³" hint="Model estimate" />
            <Metric label="Rain next 7 days" value={analytics?.next_7_days.precipitation_mm ?? "—"} unit="mm" tone="teal" hint={`${analytics?.next_7_days.rain_days ?? 0} rain days`} />
            <Metric label="ET₀ next 7 days" value={analytics?.next_7_days.et0_mm ?? "—"} unit="mm" hint={`Balance ${analytics?.next_7_days.water_balance_mm ?? "—"} mm`} />
          </MetricsGrid>

          {analytics && (analytics.dry_spell_days >= 4 || analytics.heat_days.length || analytics.heavy_rain_days.length) ? (
            <div className="alert-grid">
              {analytics.dry_spell_days >= 4 ? (
                <div className="alert alert--watch">
                  <strong>{analytics.dry_spell_days}-day dry spell forecast</strong>
                  <span>Plan moisture conservation and irrigation if available.</span>
                </div>
              ) : null}
              {analytics.heat_days.length ? (
                <div className="alert alert--warning">
                  <strong>Heat risk</strong>
                  <span>≥35 °C on {analytics.heat_days.slice(0, 3).map((day) => formatShortDate(day)).join(", ")}.</span>
                </div>
              ) : null}
              {analytics.heavy_rain_days.length ? (
                <div className="alert alert--info">
                  <strong>Heavy rain</strong>
                  <span>
                    {analytics.heavy_rain_days.map((day) => `${formatShortDate(day.date)} (${day.mm} mm)`).join(", ")}
                  </span>
                </div>
              ) : null}
            </div>
          ) : null}

          <section>
            <h3>10-day outlook</h3>
            <div className="forecast" role="table" aria-label="Daily forecast">
              {forecast.map((day) => {
                const rain = Number(day.precipitation_mm ?? 0);
                return (
                  <div className="forecast__day" key={String(day.date)} role="row">
                    <span className="forecast__date">{formatShortDate(String(day.date))}</span>
                    <span className="forecast__rain" aria-label={`${rain} mm rain`}>
                      <i style={{ height: `${Math.min(52, 6 + rain * 3)}px` }} />
                    </span>
                    <span className="forecast__mm numeric">{formatNumber(rain, 1)}</span>
                    <span className="forecast__temp numeric">
                      {Math.round(Number(day.temperature_max_c ?? 0))}° / {Math.round(Number(day.temperature_min_c ?? 0))}°
                    </span>
                    <span className="forecast__prob tiny muted">{day.precipitation_probability ?? "—"}%</span>
                  </div>
                );
              })}
            </div>
            <p className="tiny muted">
              Rainfall is a grid-cell forecast. Tropical convective storms are localised, so a forecast value is not a
              measurement on your field.
            </p>
          </section>

          {rainfall ? (
            <section>
              <h3>Observed rainfall (CHIRPS)</h3>
              <p className="tiny muted">
                Latest available day: {formatDate(rainfall.latest_available)} ({rainfall.lag_days} days behind today)
              </p>
              <div className="totals">
                {(["last_7_days", "last_30_days", "last_90_days"] as const).map((key) => {
                  const window = rainfall.windows[key];
                  const anomaly = rainfall.anomalies[key];
                  const percent = anomaly?.percent_of_normal ?? null;
                  return (
                    <div key={key} className="total-card">
                      <span className="total-card__label">{key.replace("last_", "Last ").replace("_", " ")}</span>
                      <span className="total-card__value numeric">{formatNumber(window?.total_mm, 1)} mm</span>
                      <span className={`total-card__anomaly ${percent !== null && percent < 80 ? "is-low" : percent !== null && percent > 120 ? "is-high" : ""}`}>
                        {percent !== null ? `${Math.round(percent)}% of 1991–2020` : "No baseline"}
                      </span>
                      <div className="total-card__bar" aria-hidden="true">
                        <i style={{ width: `${Math.min(100, (percent ?? 0) / 2)}%` }} />
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>
          ) : null}

          <section>
            <h3>Daily rainfall, recent and forecast</h3>
            <div className="rain-chart" aria-label="Recent and forecast daily rainfall">
              {(rainfall?.daily_series ?? []).slice(-30).map((day) => (
                <span key={day.date} className="rain-chart__bar" title={`${day.date}: ${day.mm} mm`}>
                  <i style={{ height: `${Math.min(46, 3 + Number(day.mm ?? 0) * 3)}px` }} />
                </span>
              ))}
              <span className="rain-chart__divider" aria-hidden="true" />
              {forecast.slice(0, 10).map((day) => (
                <span key={`f-${day.date}`} className="rain-chart__bar rain-chart__bar--forecast" title={`${day.date}: ${day.precipitation_mm} mm forecast`}>
                  <i style={{ height: `${Math.min(46, 3 + Number(day.precipitation_mm ?? 0) * 3)}px` }} />
                </span>
              ))}
            </div>
            <div className="rain-chart__legend tiny muted">
              <span><i className="dot dot--solid" aria-hidden="true" /> CHIRPS daily to {formatShortDate(rainfall?.latest_available)}</span>
              <span><i className="dot dot--hollow" aria-hidden="true" /> Open-Meteo forecast</span>
            </div>
          </section>

          <Advisories />

          <SourceList sources={section?.sources ?? []} />
        </>
      ))}
    </div>
  );
}

function Advisories() {
  const [items, setItems] = useState<Array<Record<string, unknown>>>([]);
  useEffect(() => {
    api
      .advisories()
      .then((payload) => setItems(payload.advisories))
      .catch(() => setItems([]));
  }, []);
  if (!items.length) {
    return (
      <div className="banner banner--info">
        No official GMet advisory has been entered for this deployment. Advisories are added by an administrator with
        a source link — absence of an advisory is not evidence that conditions are normal.
      </div>
    );
  }
  return (
    <section>
      <h3>Official advisories</h3>
      <div className="stack stack--tight">
        {items.slice(0, 3).map((item) => (
          <article key={String(item.id)} className="advisory">
            <div className="advisory__head">
              <Pill tone="teal">{String(item.agency)}</Pill>
              <span className="tiny muted">{formatDate(String(item.published_at ?? ""))}</span>
            </div>
            <h4>{String(item.title)}</h4>
            <p className="small">{String(item.body).slice(0, 500)}</p>
            <a className="link" href={String(item.source_url)} target="_blank" rel="noreferrer noopener">
              Read the original advisory
            </a>
          </article>
        ))}
      </div>
    </section>
  );
}

/* --------------------------------------------------------------------- crops */

function ComponentBar({ component }: { component: CropComponent }) {
  const score = component.score ?? 0;
  const tone = score >= 78 ? "good" : score >= 60 ? "watch" : "danger";
  return (
    <div className="component">
      <div className="component__head">
        <span>{component.name}</span>
        <span className={`numeric component__score component__score--${tone}`}>{component.available ? Math.round(score) : "—"}</span>
      </div>
      <div className="component__track">
        <div className={`component__fill component__fill--${tone}`} style={{ width: `${component.available ? score : 0}%` }} />
      </div>
      <div className="component__values tiny muted">
        <span>Farm: {component.farm_value ?? "—"}</span>
        <span>Needs: {component.requirement ?? "—"}</span>
      </div>
      {component.note ? <p className="tiny component__note">{component.note}</p> : null}
    </div>
  );
}

function CropCard({ crop, detailed = false }: { crop: CropEvaluation; detailed?: boolean }) {
  return (
    <article className={`crop-card ${detailed ? "crop-card--detailed" : ""}`}>
      <div className="crop-card__head">
        <div>
          <h4>
            {crop.name}
            {crop.perennial ? <Pill tone="neutral">perennial</Pill> : null}
          </h4>
          <span className="tiny muted">{crop.scientific_name}</span>
        </div>
        <div className="crop-card__score">
          <span className="numeric">{crop.score !== null ? Math.round(crop.score) : "—"}</span>
          {crop.score_range ? <span className="tiny muted">±{Math.round((crop.score_range[1] - crop.score_range[0]) / 2)}</span> : null}
        </div>
      </div>
      <div className="crop-card__badges">
        <Pill tone={crop.category === "highly_suitable" ? "good" : crop.category === "suitable" ? "teal" : crop.category === "marginal" ? "warn" : "danger"}>
          {crop.category_label}
        </Pill>
        <Confidence level={crop.confidence} />
        {crop.zone_warning ? <Pill tone="warn">Zone note</Pill> : null}
      </div>
      {detailed ? (
        <>
          {crop.confidence_note ? <p className="tiny muted">{crop.confidence_note}</p> : null}
          {crop.zone_fit_note ? (
            <p className="small">
              <strong>Zone note:</strong> {crop.zone_fit_note}
            </p>
          ) : null}
          {crop.planting_window ? (
            <div className="crop-window">
              <div>
                <span className="tiny muted">Planting window</span>
                <strong>{crop.planting_window.planting_window.label}</strong>
                <span className="tiny">
                  {crop.planting_window.state === "open"
                    ? `Open now — closes in ${crop.planting_window.days_to_window ?? 0} days`
                    : crop.planting_window.state === "upcoming"
                      ? `Opens ${formatDate(crop.planting_window.start_iso)}`
                      : "Window has passed for this season"}
                </span>
              </div>
              <div>
                <span className="tiny muted">Harvest window</span>
                <strong>{crop.harvest_window?.label ?? "—"}</strong>
                <span className="tiny muted">Cycle {crop.cycle_days?.typical} days (typical)</span>
              </div>
            </div>
          ) : (
            <p className="small muted">
              Perennial crop: this is an establishment and long-term suitability view, not a one-season planting window.
            </p>
          )}
          {crop.current_stage ? (
            <p className="small">
              <strong>Current stage:</strong> {crop.current_stage.name} ({crop.current_stage.days_since_planting} days since
              planting)
            </p>
          ) : null}
          <div className="components">
            {crop.components.map((component) => (
              <ComponentBar key={component.name} component={component} />
            ))}
          </div>
          {crop.constraints.length ? (
            <div className="callout callout--tight">
              <h4>Limiting factors</h4>
              <ul className="list">
                {crop.constraints.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {crop.missing_inputs.length ? (
            <p className="tiny muted">
              Missing inputs that would change this result: {crop.missing_inputs.join(", ")}.
            </p>
          ) : null}
          {crop.limitations ? <p className="tiny muted">{crop.limitations}</p> : null}
          <details className="details">
            <summary>Crop reference ranges and sources</summary>
            <p className="small">
              Seasonal water requirement {(crop as unknown as { seasonal_rainfall_mm?: number }).seasonal_rainfall_mm ?? "—"} mm against
              the published range in the reference table. {crop.water_note}
            </p>
            <ul className="list small">
              {crop.sources.map((source) => (
                <li key={source.title}>
                  {source.url ? (
                    <a className="link" href={source.url} target="_blank" rel="noreferrer noopener">
                      {source.title ?? source.url}
                    </a>
                  ) : (
                    source.title
                  )}{" "}
                  {source.publisher ? <span className="muted">— {source.publisher}</span> : null}
                </li>
              ))}
            </ul>
          </details>
        </>
      ) : null}
    </article>
  );
}

function TabCrops({
  analysis,
  crops,
  onSelectCrop,
  busy,
  onAnalyze,
}: {
  analysis: Analysis | null;
  crops: CropSummary[];
  onSelectCrop: (cropId: string) => void;
  busy: boolean;
  onAnalyze: () => void;
}) {
  const data = analysis?.sections.crops?.data;
  const selected = data?.selected ?? null;
  const [query, setQuery] = useState("");
  const filtered = useMemo(
    () => crops.filter((crop) => crop.name.toLowerCase().includes(query.toLowerCase())).slice(0, 8),
    [crops, query],
  );

  return (
    <div className="stack">
      <div className="section-head">
        <div>
          <h3>Crops and planting</h3>
          <p className="muted small">
            Rule-based screening from cited crop ranges, not a GAEZ raster and not a yield forecast.
          </p>
        </div>
      </div>

      <div className="crop-picker">
        <Field label="Choose the crop you are growing or planning" htmlFor="crop-search">
          <input
            id="crop-search"
            type="search"
            placeholder="Search 20 Ghana crops…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </Field>
        <div className="crop-picker__list">
          {filtered.map((crop) => (
            <button
              key={crop.id}
              type="button"
              className={`crop-chip ${data?.selected_id === crop.id ? "is-active" : ""}`}
              onClick={() => onSelectCrop(crop.id)}
              disabled={busy}
            >
              {crop.name}
              {crop.perennial ? <span className="tiny muted"> · perennial</span> : null}
            </button>
          ))}
        </div>
        <p className="tiny muted">
          {busy ? "Re-running analysis with the new crop…" : "Changing the crop re-runs the analysis for this polygon."}
        </p>
      </div>

      {!analysis ? (
        <EmptyState title="No analysis yet">Select a farm to evaluate crops against its soil, rainfall, temperature and slope.</EmptyState>
      ) : !data?.ranking?.length ? (
        <EmptyState title="Insufficient data for a ranking" tone="warn">
          At least three component inputs are needed (soil pH/texture, rainfall, temperature, slope). Missing:{" "}
          {data?.missing_inputs?.join(", ") ?? "unknown"}.
          <div className="button-row">
            <button type="button" className="button button--primary" onClick={onAnalyze}>
              Retry analysis
            </button>
          </div>
        </EmptyState>
      ) : (
        <>
          {selected ? <CropCard crop={selected} detailed /> : null}
          <section>
            <h3>Top three by evidence</h3>
            <div className="stack stack--tight">
              {(data?.ranking ?? []).map((crop) => (
                <CropCard key={crop.id} crop={crop} />
              ))}
            </div>
          </section>
          {data?.insufficient?.length ? (
            <p className="tiny muted">
              Not scored for lack of inputs: {data.insufficient.join(", ")}.
            </p>
          ) : null}
          <div className="callout callout--tight">
            <h4>How this ranking works</h4>
            <p className="small">{data?.method?.note}</p>
            <p className="tiny muted">
              Weights:{" "}
              {data?.method
                ? Object.entries(data.method.weights)
                    .map(([key, value]) => `${key} ${Math.round(Number(value) * 100)}%`)
                    .join(" · ")
                : "—"}
            </p>
            <p className="tiny muted">
              Market and economic suitability are deliberately not scored: no verified, licensed price feed is connected.
            </p>
          </div>
        </>
      )}
      <SourceList sources={analysis?.sections.crops?.sources ?? []} />
    </div>
  );
}

/* -------------------------------------------------------------------- health */

function TabHealth({ analysis }: { analysis: Analysis | null }) {
  const section = analysis?.sections.vegetation;
  const latest = section?.data.latest;
  const history = section?.data.history ?? [];
  const [view, setView] = useState<"true_color" | "ndvi" | "ndmi">("ndvi");

  return (
    <div className="stack">
      {useSectionBody(section, (
        <>
          <div className="section-head">
            <div>
              <h3>Satellite vegetation</h3>
              <p className="muted small">
                {latest ? `Sentinel-2 ${formatDate(latest.date)} · ${(latest.cloud_free_fraction * 100).toFixed(0)}% cloud-free over this field` : ""}
              </p>
            </div>
            {latest?.chips ? (
              <Segmented
                ariaLabel="Vegetation view"
                value={view}
                onChange={setView}
                options={[
                  { value: "true_color", label: "True colour" },
                  { value: "ndvi", label: "NDVI" },
                  { value: "ndmi", label: "NDMI" },
                ]}
              />
            ) : null}
          </div>

          {latest?.chips ? (
            <figure className="scene">
              <img src={latest.chips[view]} alt={`${view.replace("_", " ")} of the selected field on ${latest.date}`} loading="lazy" />
              <figcaption className="tiny muted">
                {view === "ndvi" ? "NDVI" : view === "ndmi" ? "NDMI" : "True colour"} · 10 m · cloud and shadow masked ·{" "}
                {latest.pixels.toLocaleString()} pixels sampled
              </figcaption>
            </figure>
          ) : null}

          <MetricsGrid>
            <Metric label="NDVI mean" value={latest?.ndvi.mean !== null && latest?.ndvi.mean !== undefined ? formatNumber(latest.ndvi.mean, 2) : "—"} hint={`median ${formatNumber(latest?.ndvi.median ?? null, 2)}`} tone="teal" />
            <Metric label="NDMI mean" value={latest?.ndmi?.mean !== null && latest?.ndmi?.mean !== undefined ? formatNumber(latest.ndmi.mean, 2) : "—"} hint="canopy moisture proxy" />
            <Metric label="Cloud-free over field" value={latest ? Math.round(latest.cloud_free_fraction * 100) : "—"} unit="%" hint={`${(latest?.valid_fraction ?? 0) * 100 > 0 ? Math.round((latest?.valid_fraction ?? 0) * 100) : 0}% valid pixels`} />
            <Metric label="Change vs recent median" value={section?.data.change_vs_recent_median !== null && section?.data.change_vs_recent_median !== undefined ? formatNumber(section.data.change_vs_recent_median, 2) : "—"} hint="NDVI units" tone={section?.data.change_vs_recent_median !== null && (section?.data.change_vs_recent_median ?? 0) < -0.05 ? "danger" : undefined} />
          </MetricsGrid>

          {latest?.spatial_pattern ? (
            <section>
              <h3>Within-field pattern</h3>
              <p className="small">
                {(latest.spatial_pattern.low_vigour_share * 100).toFixed(0)}% of the field is more than one standard
                deviation below its own mean NDVI. This is a relative pattern in one image, not a diagnosis.
              </p>
              <DataTable
                head={["Quarter", "Mean NDVI"]}
                rows={latest.spatial_pattern.quadrants_lowest_first.map((item) => [item.quadrant, formatNumber(item.mean_ndvi, 2)])}
              />
            </section>
          ) : null}

          <section>
            <h3>Scene history</h3>
            <div className="history">
              {history.map((scene) => (
                <div key={scene.scene_id} className="history__row">
                  <span className="history__date">{formatShortDate(scene.date)}</span>
                  <span className="history__bar" aria-hidden="true">
                    <i style={{ width: `${Math.min(100, Math.max(0, (scene.ndvi_mean ?? 0) * 100))}%` }} />
                  </span>
                  <span className="numeric history__value">{formatNumber(scene.ndvi_mean, 2)}</span>
                  <span className="tiny muted">{Math.round(scene.cloud_free_fraction * 100)}% clear</span>
                </div>
              ))}
              {!history.length ? <p className="muted small">No usable scenes in the search window.</p> : null}
            </div>
            {section?.data.trend_per_scene !== null && section?.data.trend_per_scene !== undefined ? (
              <p className="tiny muted">
                Linear trend across these scenes: {formatNumber(section.data.trend_per_scene, 3)} NDVI per scene. Scene
                dates differ in season, so this is not a seasonal trend estimate.
              </p>
            ) : null}
          </section>

          <div className="callout">
            <h4>What this does not tell you</h4>
            <ul className="list">
              {(section?.data.notes ?? []).map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          </div>
        </>
      ))}
      <SourceList sources={section?.sources ?? []} />
    </div>
  );
}

/* --------------------------------------------------------------------- water */

function TabWater({ analysis }: { analysis: Analysis | null }) {
  const terrain = analysis?.sections.terrain;
  const landcover = analysis?.sections.landcover;
  const weather = analysis?.sections.weather?.data;
  const rainfall = analysis?.sections.rainfall?.data;
  const water = analysis?.sections.water;
  const analytics = weather?.analytics;

  return (
    <div className="stack">
      <section>
        <h3>Water balance</h3>
        <p className="muted small">Rainfall and reference evapotranspiration only — WaPOR evapotranspiration is unavailable.</p>
        <MetricsGrid>
          <Metric label="Rain, next 7 days" value={analytics?.next_7_days.precipitation_mm ?? "—"} unit="mm" tone="teal" />
          <Metric label="ET₀, next 7 days" value={analytics?.next_7_days.et0_mm ?? "—"} unit="mm" />
          <Metric
            label="Balance"
            value={analytics?.next_7_days.water_balance_mm ?? "—"}
            unit="mm"
            tone={(analytics?.next_7_days.water_balance_mm ?? 0) < -10 ? "danger" : "teal"}
            hint="Rain minus ET₀"
          />
          <Metric label="Soil moisture (0–10 cm)" value={weather?.current.soil_moisture_0_10cm ?? "—"} unit="m³/m³" hint="Model estimate" />
        </MetricsGrid>
        <p className="tiny muted">
          Irrigation need is a planning estimate, not a measured water requirement. Coarse inputs cannot predict
          waterlogging or flood depth for an individual small field.
        </p>
      </section>

      <section>
        <h3>Rainfall context</h3>
        {rainfall ? (
          <div className="totals">
            {(["last_30_days", "last_90_days"] as const).map((key) => {
              const anomaly = rainfall.anomalies[key];
              return (
                <div key={key} className="total-card">
                  <span className="total-card__label">{key === "last_30_days" ? "Last 30 days" : "Last 90 days"}</span>
                  <span className="total-card__value numeric">{formatNumber(rainfall.windows[key]?.total_mm, 1)} mm</span>
                  <span className="total-card__anomaly">{anomaly?.label ?? "No baseline"}</span>
                </div>
              );
            })}
          </div>
        ) : (
          <p className="muted small">Rainfall history unavailable.</p>
        )}
      </section>

      <section>
        <h3>Terrain</h3>
        {terrain && terrain.status === "ok" ? (
          <>
            <MetricsGrid>
              <Metric label="Elevation" value={formatNumber(terrain.data.elevation_m.mean, 0)} unit="m" hint={`${formatNumber(terrain.data.elevation_m.min, 0)}–${formatNumber(terrain.data.elevation_m.max, 0)} m`} />
              <Metric label="Mean slope" value={formatNumber(terrain.data.slope_percent.mean, 1)} unit="%" hint={`${formatNumber(terrain.data.slope_percent.p90, 1)}% at 90th percentile`} />
              <Metric label="Aspect" value={terrain.data.aspect_label ?? "—"} hint={`${formatNumber(terrain.data.aspect_degrees ?? 0, 0)}°`} />
              <Metric
                label="Erosion screening"
                value={terrain.data.erosion_screening.level}
                tone={terrain.data.erosion_screening.level === "high" ? "danger" : terrain.data.erosion_screening.level === "moderate" ? "warn" : "teal"}
                hint={`${Math.round(terrain.data.steep_share_over_15pct * 100)}% above 15% slope`}
              />
            </MetricsGrid>
            <p className="tiny muted">{terrain.data.erosion_screening.note}</p>
          </>
        ) : (
          <EmptyState title="Terrain unavailable" tone="warn">{terrain?.message ?? "The DEM window could not be read."}</EmptyState>
        )}
      </section>

      <section>
        <h3>Land cover (2021 baseline)</h3>
        {landcover && landcover.status === "ok" ? (
          <>
            {landcover.data.classes.map((entry) => (
              <BarRow key={entry.code} label={entry.name} value={entry.share * 100} max={100} unit="%" color={entry.color} />
            ))}
            <p className="tiny muted">{landcover.data.notes[0]}</p>
          </>
        ) : (
          <EmptyState title="Land cover unavailable" tone="warn">{landcover?.message ?? "No WorldCover tile for this area."}</EmptyState>
        )}
      </section>

      <section>
        <h3>Evapotranspiration (WaPOR)</h3>
        {water?.status === "unavailable" ? (
          <EmptyState title="WaPOR is unavailable" tone="warn">
            {water.message}
            <ul className="list small">
              {(water.data.not_shown ?? []).map((item) => (
                <li key={item}>Not shown: {item}</li>
              ))}
            </ul>
          </EmptyState>
        ) : (
          useSectionBody(water, <p className="small">WaPOR values are shown with their own period and resolution.</p>)
        )}
      </section>

      <SourceList sources={[...(terrain?.sources ?? []), ...(landcover?.sources ?? []), ...(water?.sources ?? [])]} />
    </div>
  );
}

/* ------------------------------------------------------------------ calendar */

function TabCalendar({
  analysis,
  onAddRecord,
  farm,
  onExport,
}: {
  analysis: Analysis | null;
  onAddRecord: (payload: Record<string, unknown>) => Promise<void>;
  farm: Farm;
  onExport: (format: "geojson" | "kml" | "csv" | "pdf") => void;
}) {
  const section = analysis?.sections.calendar;
  const [formOpen, setFormOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="stack">
      <div className="section-head">
        <div>
          <h3>Crop calendar and records</h3>
          <p className="muted small">
            {section?.data.crop_name ? `${section.data.crop_name} · ` : ""}
            {section?.data.planting_date ? `planted ${formatDate(section.data.planting_date)}` : "no planting date recorded"}
          </p>
        </div>
        <button type="button" className="button button--ghost button--small" onClick={() => setFormOpen(true)}>
          Add record
        </button>
      </div>

      {!analysis ? (
        <EmptyState title="No analysis yet">Run an analysis to build the crop calendar.</EmptyState>
      ) : (
        <>
          {section?.data.alerts?.length ? (
            <div className="stack stack--tight">
              {section.data.alerts.map((alert) => (
                <div key={`${alert.title}-${alert.due_date}`} className="alert alert--watch">
                  <strong>{alert.title}</strong>
                  <span>
                    {formatDate(alert.due_date)} · {alert.notes}
                  </span>
                </div>
              ))}
            </div>
          ) : null}

          <section>
            <h3>Milestones</h3>
            <ol className="timeline-list">
              {(section?.data.milestones ?? []).map((milestone) => (
                <li key={milestone.name} className={`timeline-item timeline-item--${milestone.status}`}>
                  <span className="timeline-item__dot" aria-hidden="true" />
                  <div>
                    <strong>{milestone.name}</strong>
                    <div className="tiny muted">
                      {milestone.start_date ? `${formatDate(milestone.start_date)} → ${formatDate(milestone.end_date)}` : "Add a planting date to date this stage"}
                      {milestone.status === "current" ? " · current stage" : ""}
                    </div>
                    {milestone.notes ? <p className="tiny muted">{milestone.notes}</p> : null}
                  </div>
                </li>
              ))}
            </ol>
          </section>

          <section>
            <h3>Tasks</h3>
            {(section?.data.tasks ?? []).length ? (
              <DataTable
                head={["Task", "Due", "Window ends"]}
                rows={(section?.data.tasks ?? []).slice(0, 12).map((task) => [
                  task.title,
                  formatDate(task.due_date),
                  task.window_end ? formatDate(task.window_end) : "—",
                ])}
              />
            ) : (
              <p className="muted small">No crop tasks yet — choose a crop and planting date.</p>
            )}
          </section>

          <section>
            <h3>Farm records</h3>
            {farm.records?.length ? (
              <div className="records">
                {farm.records.slice(0, 8).map((record) => (
                  <article key={record.id} className="record">
                    <div className="record__head">
                      <Pill tone="neutral">{record.kind}</Pill>
                      <span className="tiny muted">{formatDate(record.recorded_at)}</span>
                    </div>
                    <strong>{record.title ?? "Record"}</strong>
                    {record.notes ? <p className="small">{record.notes}</p> : null}
                  </article>
                ))}
              </div>
            ) : (
              <p className="muted small">No observations, visits or actions recorded yet.</p>
            )}
          </section>

          <section>
            <h3>Export</h3>
            <div className="button-row">
              <button type="button" className="button button--ghost" onClick={() => onExport("pdf")}>
                PDF report
              </button>
              <button type="button" className="button button--ghost" onClick={() => onExport("csv")}>
                CSV records
              </button>
              <button type="button" className="button button--ghost" onClick={() => onExport("geojson")}>
                GeoJSON
              </button>
              <button type="button" className="button button--ghost" onClick={() => onExport("kml")}>
                KML
              </button>
            </div>
            <p className="tiny muted">
              The PDF includes the latest completed analysis, sources and limitations. Farm boundaries are private and
              are only written to files you download.
            </p>
          </section>
        </>
      )}

      <Modal open={formOpen} title="Record an observation, action or visit" onClose={() => setFormOpen(false)}>
        <form
          className="form"
          onSubmit={async (event) => {
            event.preventDefault();
            setBusy(true);
            setError(null);
            const data = new FormData(event.currentTarget);
            try {
              await onAddRecord({
                kind: data.get("kind"),
                title: data.get("title"),
                notes: data.get("notes"),
                recorded_at: data.get("recorded_at") || undefined,
              });
              setFormOpen(false);
            } catch (recordError) {
              setError(recordError instanceof Error ? recordError.message : "Could not save the record");
            } finally {
              setBusy(false);
            }
          }}
        >
          <Field label="Type" htmlFor="record-kind">
            <select id="record-kind" name="kind" defaultValue="observation">
              <option value="observation">Observation</option>
              <option value="action">Action taken</option>
              <option value="visit">Farm visit</option>
              <option value="note">Note</option>
            </select>
          </Field>
          <Field label="Title" htmlFor="record-title">
            <input id="record-title" name="title" required maxLength={160} placeholder="e.g. Fall armyworm seen in the north-east corner" />
          </Field>
          <Field label="Date" htmlFor="record-date">
            <input id="record-date" name="recorded_at" type="date" defaultValue={todayIso()} />
          </Field>
          <Field label="Notes" htmlFor="record-notes">
            <textarea id="record-notes" name="notes" rows={3} maxLength={4000} />
          </Field>
          {error ? <p className="form-error">{error}</p> : null}
          <div className="button-row">
            <button type="button" className="button button--ghost" onClick={() => setFormOpen(false)}>
              Cancel
            </button>
            <button type="submit" className="button button--primary" disabled={busy}>
              {busy ? "Saving…" : "Save record"}
            </button>
          </div>
        </form>
      </Modal>
    </div>
  );
}

/* ----------------------------------------------------------------- timeline */

export function Timeline({
  analysis,
  range,
  onRange,
}: {
  analysis: Analysis | null;
  range: 30 | 90 | 180;
  onRange: (value: 30 | 90 | 180) => void;
}) {
  const rainfall = analysis?.sections.rainfall?.data;
  const weather = analysis?.sections.weather?.data;
  const vegetation = analysis?.sections.vegetation?.data;
  const calendar = analysis?.sections.calendar?.data;
  const today = todayIso();
  const daily = (rainfall?.daily_series ?? []).slice(-range);
  const forecast = (weather?.daily ?? []).filter((day) => String(day.date) >= today).slice(0, 10);
  const scenes = vegetation?.history ?? [];
  const milestones = (calendar?.milestones ?? []).filter((item) => item.start_date);
  const maxRain = Math.max(
    1,
    ...daily.map((day) => Number(day.mm ?? 0)),
    ...forecast.map((day) => Number(day.precipitation_mm ?? 0)),
  );

  return (
    <section className="timeline" aria-label="Timeline of rainfall, imagery and crop calendar">
      <div className="timeline__head">
        <div>
          <h3>Timeline</h3>
          <span className="tiny muted">
            History is observed/estimated; the forecast is model output. Latest CHIRPS day{" "}
            {rainfall ? formatDate(rainfall.latest_available) : "—"}.
          </span>
        </div>
        <div className="timeline__controls">
          <Segmented
            ariaLabel="Timeline range"
            value={String(range)}
            onChange={(value) => onRange(Number(value) as 30 | 90 | 180)}
            options={[
              { value: "30", label: "30 d" },
              { value: "90", label: "90 d" },
              { value: "180", label: "180 d" },
            ]}
          />
        </div>
      </div>
      <div className="timeline__track">
        <div className="timeline__rain" aria-hidden="true">
          {daily.map((day) => (
            <span key={day.date} className="timeline__bar" title={`${formatDate(day.date)}: ${day.mm ?? "—"} mm`}>
              <i style={{ height: `${Math.max(2, (Number(day.mm ?? 0) / maxRain) * 100)}%` }} />
            </span>
          ))}
          {forecast.map((day) => (
            <span
              key={`f-${day.date}`}
              className="timeline__bar timeline__bar--forecast"
              title={`${formatDate(String(day.date))}: ${day.precipitation_mm} mm forecast`}
            >
              <i style={{ height: `${Math.max(2, (Number(day.precipitation_mm ?? 0) / maxRain) * 100)}%` }} />
            </span>
          ))}
        </div>
        <div className="timeline__markers">
          {scenes.map((scene) => {
            const index = daily.findIndex((day) => day.date >= scene.date);
            const position = index >= 0 ? (index / Math.max(daily.length, 1)) * 70 : 0;
            return (
              <span
                key={scene.scene_id}
                className="timeline__marker timeline__marker--scene"
                style={{ left: `${position}%` }}
                title={`Sentinel-2 ${formatDate(scene.date)} · NDVI ${scene.ndvi_mean ?? "—"} · ${Math.round(scene.cloud_free_fraction * 100)}% clear`}
              >
                ◉
              </span>
            );
          })}
          {milestones.map((milestone) => {
            const index = daily.findIndex((day) => milestone.start_date && day.date >= milestone.start_date);
            if (index < 0) return null;
            return (
              <span
                key={`m-${milestone.name}`}
                className="timeline__marker timeline__marker--crop"
                style={{ left: `${(index / Math.max(daily.length, 1)) * 70}%` }}
                title={`${milestone.name} · ${formatDate(milestone.start_date)}`}
              >
                ▲
              </span>
            );
          })}
          <span className="timeline__now" style={{ left: `${Math.min(70, (daily.length / Math.max(daily.length + forecast.length, 1)) * 100)}%` }}>
            now
          </span>
        </div>
      </div>
      <div className="timeline__legend tiny muted">
        <span><i className="dot dot--solid" aria-hidden="true" /> CHIRPS daily rainfall</span>
        <span><i className="dot dot--hollow" aria-hidden="true" /> forecast</span>
        <span>◉ Sentinel-2 scene</span>
        <span>▲ crop stage</span>
      </div>
    </section>
  );
}

/* --------------------------------------------------------------- onboarding */

export function Onboarding({
  onSearch,
  onDraw,
  onImport,
  onGps,
  onSample,
  onSettings,
  busy,
  error,
}: {
  onSearch: () => void;
  onDraw: () => void;
  onImport: () => void;
  onGps: () => void;
  onSample: () => void;
  onSettings: () => void;
  busy: boolean;
  error: string | null;
}) {
  return (
    <div className="onboarding" role="dialog" aria-modal="false" aria-label="Getting started">
      <div className="onboarding__card">
        <div className="onboarding__head">
          <Pill tone="accent">Ghana first</Pill>
          <h2>Find your farm, then see what the data says</h2>
          <p className="muted">
            Every result uses the polygon you draw or import — soil, rainfall, satellite imagery and terrain are
            sampled inside it. Sources, dates, resolution and uncertainty are shown for every number.
          </p>
        </div>
        <ol className="onboarding__steps">
          <li>
            <span className="step-number">1</span>
            <div>
              <strong>Locate the farm</strong>
              <p className="small muted">Search a region, district or village, or use GPS at the field edge.</p>
              <div className="button-row">
                <button type="button" className="button button--primary" onClick={onSearch}>
                  Search a locality
                </button>
                <button type="button" className="button button--ghost" onClick={onGps}>
                  Use GPS
                </button>
              </div>
            </div>
          </li>
          <li>
            <span className="step-number">2</span>
            <div>
              <strong>Draw or import the boundary</strong>
              <p className="small muted">
                Tap the corners, then finish and save. GeoJSON, KML and GPX imports are validated for self-intersections
                and Ghana coverage.
              </p>
              <div className="button-row">
                <button type="button" className="button button--primary" onClick={onDraw}>
                  Draw farm
                </button>
                <button type="button" className="button button--ghost" onClick={onImport}>
                  Import file
                </button>
              </div>
            </div>
          </li>
          <li>
            <span className="step-number">3</span>
            <div>
              <strong>Choose the crop and see a real analysis</strong>
              <p className="small muted">
                With no data of your own, try the sample boundary. It is a real location analysed with real sourced data,
                clearly labelled as a sample.
              </p>
              <div className="button-row">
                <button type="button" className="button button--accent" onClick={onSample} disabled={busy}>
                  {busy ? "Creating sample…" : "Try the sample farm"}
                </button>
                <button type="button" className="button button--ghost" onClick={onSettings}>
                  Language and display
                </button>
              </div>
            </div>
          </li>
        </ol>
        {error ? <p className="form-error">{error}</p> : null}
        <p className="tiny muted">
          Works on a modest phone and offline once an analysis has been computed. No trackers; boundaries stay private.
        </p>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------- search */

export function SearchOverlay({
  open,
  onClose,
  onSelect,
  onDrawHere,
}: {
  open: boolean;
  onClose: () => void;
  onSelect: (result: { lat: number; lon: number; name: string; region: string | null; district: string | null; kind: string }) => void;
  onDrawHere: () => void;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Array<Record<string, unknown>>>([]);
  const [loading, setLoading] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setResults([]);
    setError(null);
    setTimeout(() => inputRef.current?.focus(), 50);
  }, [open]);

  useEffect(() => {
    if (!open || query.trim().length < 2) {
      setResults([]);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    const timer = setTimeout(() => {
      fetch(`/api/places/search?q=${encodeURIComponent(query)}&limit=12`, { signal: controller.signal })
        .then((response) => response.json())
        .then((payload) => {
          setResults(payload.results ?? []);
          setActiveIndex(0);
        })
        .catch((fetchError) => {
          if (fetchError.name !== "AbortError") setError("Search failed — check the connection.");
        })
        .finally(() => setLoading(false));
    }, 180);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [query, open]);

  if (!open) return null;

  const choose = (result: Record<string, unknown>) => {
    const lat = Number(result.lat);
    const lon = Number(result.lon);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
    onSelect({
      lat,
      lon,
      name: String(result.name),
      region: (result.region as string) ?? null,
      district: (result.district as string) ?? null,
      kind: String(result.kind ?? result.type ?? "place"),
    });
    onClose();
  };

  return (
    <div className="modal-backdrop modal-backdrop--top" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="search-modal" role="dialog" aria-modal="true" aria-label="Search Ghana localities">
        <div className="search-modal__input">
          <span aria-hidden="true">⌕</span>
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown") {
                event.preventDefault();
                setActiveIndex((index) => Math.min(index + 1, results.length - 1));
              } else if (event.key === "ArrowUp") {
                event.preventDefault();
                setActiveIndex((index) => Math.max(index - 1, 0));
              } else if (event.key === "Enter" && results[activeIndex]) {
                choose(results[activeIndex]);
              } else if (event.key === "Escape") {
                onClose();
              }
            }}
            placeholder="Region, district, town or village…"
            aria-label="Search Ghana localities"
          />
          {loading ? <span className="spinner" aria-hidden="true" /> : <kbd>esc</kbd>}
        </div>
        <ul className="search-results" role="listbox" aria-label="Search results">
          {results.map((result, index) => (
            <li key={String(result.id)}>
              <button
                type="button"
                role="option"
                aria-selected={index === activeIndex}
                className={`search-result ${index === activeIndex ? "is-active" : ""}`}
                onMouseEnter={() => setActiveIndex(index)}
                onClick={() => choose(result)}
              >
                <span>
                  <strong>{String(result.name)}</strong>
                  <span className="tiny muted">
                    {[result.type, result.district, result.region].filter(Boolean).join(" · ")}
                  </span>
                </span>
                <span className="tiny muted">{result.lat !== null ? "↗" : "region"}</span>
              </button>
            </li>
          ))}
          {!results.length && !loading ? (
            <li className="search-empty tiny muted">
              {query.length < 2 ? "Type at least two letters. Search runs against the local OpenStreetMap Ghana index." : error ?? "No matching localities."}
            </li>
          ) : null}
        </ul>
        <div className="search-modal__foot">
          <span className="tiny muted">Local place index · © OpenStreetMap contributors (ODbL)</span>
          <button type="button" className="button button--ghost button--small" onClick={onDrawHere}>
            Draw a farm at the map centre
          </button>
        </div>
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------- settings */

export function SettingsModal({
  open,
  onClose,
  theme,
  onTheme,
  units,
  onUnits,
  language,
  onLanguage,
  providers,
  providersLoading,
  usage,
  onClearOffline,
  onExportAll,
}: {
  open: boolean;
  onClose: () => void;
  theme: "dark" | "daylight";
  onTheme: (theme: "dark" | "daylight") => void;
  units: "metric" | "imperial";
  onUnits: (units: "metric" | "imperial") => void;
  language: LanguageCode;
  onLanguage: (language: LanguageCode) => void;
  providers: Array<Record<string, unknown>>;
  providersLoading: boolean;
  usage: { usedMB: number; quotaMB: number | null } | null;
  onClearOffline: () => void;
  onExportAll: () => void;
}) {
  const [tab, setTab] = useState<"general" | "sources" | "about">("general");
  return (
    <Modal open={open} title="Settings and data sources" onClose={onClose} wide>
      <div className="settings-tabs">
        <button type="button" className={tab === "general" ? "is-active" : ""} onClick={() => setTab("general")}>General</button>
        <button type="button" className={tab === "sources" ? "is-active" : ""} onClick={() => setTab("sources")}>Data sources & licences</button>
        <button type="button" className={tab === "about" ? "is-active" : ""} onClick={() => setTab("about")}>About & privacy</button>
      </div>

      {tab === "general" ? (
        <div className="stack">
          <Field label="Display mode" hint="Daylight mode is high-contrast for outdoor phone use.">
            <Segmented
              ariaLabel="Display mode"
              value={theme}
              onChange={(value) => onTheme(value as "dark" | "daylight")}
              options={[
                { value: "dark", label: "Mission control (dark)" },
                { value: "daylight", label: "Daylight (high contrast)" },
              ]}
            />
          </Field>
          <Field label="Units">
            <Segmented
              ariaLabel="Area units"
              value={units}
              onChange={(value) => onUnits(value as "metric" | "imperial")}
              options={[
                { value: "metric", label: "Hectares & metres" },
                { value: "imperial", label: "Acres & feet" },
              ]}
            />
          </Field>
          <Field label="Language">
            <select value={language} onChange={(event) => onLanguage(event.target.value as LanguageCode)}>
              {LANGUAGES.map((item) => (
                <option key={item.code} value={item.code}>
                  {item.label}
                  {item.reviewed ? "" : " — translation pending"}
                </option>
              ))}
            </select>
          </Field>
          <p className="tiny muted">
            Twi, Ewe and Dagbani are prepared in the interface but not yet reviewed, so English is shown rather than
            publishing unreviewed agronomic translations.
          </p>
          <div className="callout callout--tight">
            <h4>Offline storage</h4>
            <p className="small">
              {usage ? `${usage.usedMB} MB used on this device${usage.quotaMB ? ` of ${usage.quotaMB} MB available` : ""}.` : "Storage estimate unavailable in this browser."}
            </p>
            <p className="tiny muted">
              Saved farms, records and computed analyses remain available offline. Live forecasts and new imagery need
              connectivity and are labelled stale when they cannot be refreshed.
            </p>
            <div className="button-row">
              <button type="button" className="button button--ghost button--small" onClick={onClearOffline}>
                Clear saved analyses
              </button>
              <button type="button" className="button button--ghost button--small" onClick={onExportAll}>
                Export all farms
              </button>
            </div>
          </div>
          <div className="callout callout--tight">
            <h4>App version</h4>
            <p className="small">
              Build{" "}
              {typeof __BUILD_ID__ === "string"
                ? __BUILD_ID__.slice(0, 16).replace("T", " ") + " UTC"
                : "development"}
            </p>
            <p className="tiny muted">
              If a recent change (for example the Satellite basemap) is not appearing, an old service worker is
              probably serving a cached bundle. This clears the app cache and reloads; saved farms are kept.
            </p>
            <button
              type="button"
              className="button button--ghost button--small"
              onClick={async () => {
                try {
                  if ("serviceWorker" in navigator) {
                    const registrations = await navigator.serviceWorker.getRegistrations();
                    await Promise.all(registrations.map((registration) => registration.unregister()));
                  }
                  if ("caches" in window) {
                    const keys = await caches.keys();
                    await Promise.all(keys.map((key) => caches.delete(key)));
                  }
                } finally {
                  window.location.reload();
                }
              }}
            >
              Reload app &amp; clear cache
            </button>
          </div>
        </div>
      ) : null}

      {tab === "sources" ? (
        <div className="stack stack--tight">
          <p className="small muted">
            Each dataset keeps its own licence. Status is the live verification result at the time this page was loaded.
          </p>
          {providersLoading ? <Skeleton lines={6} /> : null}
          {providers.map((provider) => (
            <details key={String(provider.id)} className="provider">
              <summary>
                <span className="provider__name">{String(provider.name)}</span>
                <Pill tone={String(provider.status) === "connected" ? "good" : String(provider.status) === "unavailable" ? "danger" : "warn"}>
                  {String(provider.status).replace(/_/g, " ")}
                </Pill>
                <span className="tiny muted">{String(provider.resolution)}</span>
              </summary>
              <div className="provider__body">
                <p className="small">{String(provider.variables)}</p>
                <dl>
                  <div><dt>Licence</dt><dd>{String(provider.licence)}</dd></div>
                  <div><dt>Access</dt><dd>{String(provider.access)}</dd></div>
                  <div><dt>Coverage</dt><dd>{String(provider.coverage)}</dd></div>
                  <div><dt>Temporal</dt><dd>{String(provider.temporal)}</dd></div>
                  <div><dt>Integration</dt><dd>{String(provider.integration_notes)}</dd></div>
                  <div><dt>Limitations</dt><dd>{String(provider.limitations)}</dd></div>
                  {provider.commercial_note ? <div><dt>Commercial</dt><dd>{String(provider.commercial_note)}</dd></div> : null}
                </dl>
                <a className="link" href={String(provider.homepage)} target="_blank" rel="noreferrer noopener">
                  {String(provider.homepage)}
                </a>
              </div>
            </details>
          ))}
        </div>
      ) : null}

      {tab === "about" ? (
        <div className="stack">
          <h3>What this app is</h3>
          <p className="small">
            Ghana Farm Intelligence samples publicly sourced datasets inside the farm polygon you select, then explains
            what the numbers mean, where they come from, their resolution and their uncertainty.
          </p>
          <h3>Privacy</h3>
          <ul className="list small">
            <li>Farm boundaries are private by default and are never published.</li>
            <li>Location is requested only when you press “Use GPS”, and only the resulting points are stored.</li>
            <li>No advertising trackers, fingerprinting or third-party analytics are included.</li>
            <li>Provider requests are made server-side without your identity; only a bounding box is sent.</li>
          </ul>
          <h3>What it cannot do</h3>
          <ul className="list small">
            <li>It does not measure your soil, replace a laboratory test, or diagnose pests and diseases from imagery.</li>
            <li>It does not estimate yields, prices or fertiliser rates.</li>
            <li>It does not infer land ownership or legal boundaries.</li>
          </ul>
          <p className="tiny muted">
            Code: MIT licence. Data: see the sources tab. Built for browsers on modest Android phones first.
          </p>
        </div>
      ) : null}
    </Modal>
  );
}

/* ---------------------------------------------------------------- save farm */

export function SaveFarmModal({
  open,
  geometry,
  stats,
  defaultName,
  onSave,
  onClose,
  region,
  district,
  overlaps,
  busy,
  error,
}: {
  open: boolean;
  geometry: unknown;
  stats: { areaHa: number; perimeterM: number; vertices: number } | null;
  defaultName: string;
  onSave: (payload: Record<string, unknown>) => Promise<void>;
  onClose: () => void;
  region: string | null;
  district: string | null;
  overlaps: Array<{ name: string; overlap_ha: number }>;
  busy: boolean;
  error: string | null;
}) {
  const [loading, setLoading] = useState(false);
  const [crops, setCrops] = useState<CropSummary[]>([]);
  useEffect(() => {
    if (!open) return;
    api.crops().then((payload) => setCrops(payload.crops)).catch(() => setCrops([]));
  }, [open]);

  return (
    <Modal
      open={open}
      title="Save this farm boundary"
      onClose={onClose}
      footer={
        <div className="button-row button-row--end">
          <button type="button" className="button button--ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="button button--primary"
            disabled={loading || !geometry}
            onClick={async () => {
              setLoading(true);
              try {
                await onSave({});
              } finally {
                setLoading(false);
              }
            }}
          >
            {loading ? "Saving…" : "Save and analyse"}
          </button>
        </div>
      }
    >
      <div className="stack">
        <div className="save-summary">
          <div>
            <span className="tiny muted">Geodesic area</span>
            <strong className="numeric">{stats ? formatNumber(stats.areaHa, 2) : "—"} ha</strong>
            <span className="tiny muted">{stats ? `${formatNumber(stats.areaHa * 2.471, 1)} acres` : ""}</span>
          </div>
          <div>
            <span className="tiny muted">Perimeter</span>
            <strong className="numeric">{stats ? formatNumber(stats.perimeterM, 0) : "—"} m</strong>
          </div>
          <div>
            <span className="tiny muted">Vertices</span>
            <strong className="numeric">{stats?.vertices ?? "—"}</strong>
          </div>
          <div>
            <span className="tiny muted">Located in</span>
            <strong>{district ?? "—"}</strong>
            <span className="tiny muted">{region ?? ""}</span>
          </div>
        </div>
        <form
          id="save-farm-form"
          className="form"
          onSubmit={async (event) => {
            event.preventDefault();
            const data = new FormData(event.currentTarget);
            const payload: Record<string, unknown> = {
              name: data.get("name"),
              crop_id: data.get("crop_id") || null,
              planting_date: data.get("planting_date") || null,
              irrigation: data.get("irrigation") || "rainfed",
              notes: data.get("notes") || null,
            };
            setLoading(true);
            try {
              await onSave(payload);
            } finally {
              setLoading(false);
            }
          }}
        >
          <Field label="Farm name" htmlFor="farm-name">
            <input id="farm-name" name="name" defaultValue={defaultName} required maxLength={120} />
          </Field>
          <div className="form-grid">
            <Field label="Current or planned crop" htmlFor="farm-crop">
              <select id="farm-crop" name="crop_id" defaultValue="maize">
                <option value="">Not sure yet</option>
                {crops.map((crop) => (
                  <option key={crop.id} value={crop.id}>
                    {crop.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Planting date" hint="Optional — used for the crop calendar" htmlFor="farm-planting">
              <input id="farm-planting" name="planting_date" type="date" />
            </Field>
            <Field label="Irrigation" htmlFor="farm-irrigation">
              <select id="farm-irrigation" name="irrigation" defaultValue="rainfed">
                <option value="rainfed">Rainfed</option>
                <option value="supplementary">Supplementary irrigation</option>
                <option value="full">Full irrigation</option>
                <option value="unknown">Unknown</option>
              </select>
            </Field>
          </div>
          <Field label="Notes" htmlFor="farm-notes">
            <textarea id="farm-notes" name="notes" rows={2} maxLength={2000} placeholder="Variety, land tenure notes, anything you want to remember" />
          </Field>
          <button type="submit" hidden aria-hidden="true" tabIndex={-1} />
        </form>
        {overlaps.length ? (
          <div className="banner banner--warn">
            This boundary overlaps {overlaps.length} saved boundary(ies):{" "}
            {overlaps.map((item) => `${item.name} (${formatNumber(item.overlap_ha, 2)} ha)`).join(", ")}.
          </div>
        ) : null}
        <p className="tiny muted">
          Saving starts a polygon-based analysis immediately. You can rename, split or delete the farm later.
        </p>
        {error ? <p className="form-error">{error}</p> : null}
      </div>
    </Modal>
  );
}
