import { openDB, type IDBPDatabase } from "idb";
import type { Analysis, Farm } from "./types";

const DB_NAME = "gfi-offline";
const DB_VERSION = 1;

let dbPromise: Promise<IDBPDatabase> | null = null;

function db(): Promise<IDBPDatabase> {
  if (!dbPromise) {
    dbPromise = openDB(DB_NAME, DB_VERSION, {
      upgrade(database) {
        if (!database.objectStoreNames.contains("farms")) database.createObjectStore("farms");
        if (!database.objectStoreNames.contains("analyses")) database.createObjectStore("analyses");
        if (!database.objectStoreNames.contains("meta")) database.createObjectStore("meta");
      },
    });
  }
  return dbPromise;
}

/** Analyses are cached per boundary scope: a field's result is not the farm's. */
export function analysisScopeKey(farmId: string, fieldId?: string | null): string {
  return `${farmId}:${fieldId ?? "farm"}`;
}

/** Farms, records and computed analyses stay on the device for offline use. */
export const offline = {  async saveFarms(farms: Farm[]) {
    const database = await db();
    await database.put("farms", farms, "all");
    await database.put("meta", new Date().toISOString(), "lastFarmsSync");
  },
  async loadFarms(): Promise<{ farms: Farm[]; savedAt: string | null }> {
    const database = await db();
    const farms = (await database.get("farms", "all")) as Farm[] | undefined;
    const savedAt = ((await database.get("meta", "lastFarmsSync")) as string | undefined) ?? null;
    return { farms: farms ?? [], savedAt };
  },
  async saveAnalysis(scope: string, analysis: Analysis) {
    const database = await db();
    await database.put("analyses", { analysis, savedAt: new Date().toISOString() }, scope);
  },
  async loadAnalysis(scope: string): Promise<{ analysis: Analysis; savedAt: string } | null> {
    const database = await db();
    const row = (await database.get("analyses", scope)) as
      | { analysis: Analysis; savedAt: string }
      | undefined;
    return row ?? null;
  },
  async allAnalyses(): Promise<Array<{ farmId: string; savedAt: string }>> {
    const database = await db();
    const keys = await database.getAllKeys("analyses");
    const values = await database.getAll("analyses");
    return keys.map((key, index) => ({
      farmId: String(key),
      savedAt: (values[index] as { savedAt: string }).savedAt,
    }));
  },
  async clearAnalyses() {
    const database = await db();
    await database.clear("analyses");
  },
  async usage(): Promise<{ usedMB: number; quotaMB: number | null }> {
    if (!navigator.storage?.estimate) return { usedMB: 0, quotaMB: null };
    const estimate = await navigator.storage.estimate();
    return {
      usedMB: Math.round(((estimate.usage ?? 0) / 1048576) * 10) / 10,
      quotaMB: estimate.quota ? Math.round(estimate.quota / 1048576) : null,
    };
  },
};
