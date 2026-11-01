/**
 * Translation infrastructure.
 *
 * English is the reviewed interface language. Twi/Ewe/Dagbani are selectable so
 * the plumbing (persistence, catalogue labels, agronomic terms) exists, but the
 * UI deliberately falls back to English: unreviewed agronomic translations must
 * not be presented as authoritative. API payloads already carry local crop names
 * from the cited knowledge base.
 */

export type LanguageCode = "en" | "tw" | "ee" | "dag";

export interface Language {
  code: LanguageCode;
  label: string;
  english: string;
  reviewed: boolean;
  note?: string;
}

export const LANGUAGES: Language[] = [
  { code: "en", label: "English", english: "English", reviewed: true },
  {
    code: "tw",
    label: "Twi (Akan)",
    english: "Twi",
    reviewed: false,
    note: "Interface translation not yet reviewed — English is shown.",
  },
  {
    code: "ee",
    label: "Ewe",
    english: "Ewe",
    reviewed: false,
    note: "Interface translation not yet reviewed — English is shown.",
  },
  {
    code: "dag",
    label: "Dagbani",
    english: "Dagbani",
    reviewed: false,
    note: "Interface translation not yet reviewed — English is shown.",
  },
];

const ENGLISH: Record<string, string> = {
  "nav.search": "Search a place",
  "nav.layers": "Layers",
  "nav.farm": "Selected farm",
  "nav.settings": "Settings",
  "action.draw": "Draw farm",
  "action.edit": "Edit vertices",
  "action.split": "Split into fields",
  "action.delete": "Delete",
  "action.rename": "Rename",
  "action.save": "Save",
  "action.cancel": "Cancel",
  "action.undo": "Undo",
  "action.redo": "Redo",
  "action.gps": "Walk boundary (GPS)",
  "action.import": "Import GeoJSON/KML/GPX",
  "action.analyze": "View data for this area",
  "action.refresh": "Refresh",
  "action.export": "Export",
  "tab.overview": "Overview",
  "tab.soil": "Soil",
  "tab.weather": "Weather",
  "tab.crops": "Crops",
  "tab.health": "Crop health",
  "tab.water": "Water & land",
  "tab.calendar": "Calendar",
  "status.ok": "Live",
  "status.stale": "Stale",
  "status.unavailable": "Unavailable",
  "status.error": "Failed",
  "units.hectares": "hectares",
  "units.acres": "acres",
  "data.source": "Source",
  "data.resolution": "Resolution",
  "data.updated": "Updated",
  "data.confidence": "Confidence",
  "data.limitations": "Limitations",
};

export function translate(code: LanguageCode, key: string): string {
  if (code === "en") return ENGLISH[key] ?? key;
  // Interface strings for other languages are intentionally not shipped as
  // authoritative until reviewed; fall back to the reviewed English string.
  return ENGLISH[key] ?? key;
}

export const DISCLAIMER =
  "Sourced estimates, not measurements. Farm Intelligence cannot replace a soil test, field inspection or extension advice.";
