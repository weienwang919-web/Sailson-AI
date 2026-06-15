import axios from "axios";

export type KolRecord = {
  id: number;
  name: string;
  category: string;
  normalized_category?: string | null;
  major_category?: string | null;
  source_file?: string | null;
  country?: string | null;
  language?: string | null;
  platform_text?: string | null;
  notes?: string | null;
  content_tags?: string | null;
  recommendation?: string | null;
  case_links?: string | null;
  tt_link?: string | null;
  tt_follower?: number | null;
  tt_avv?: number | null;
  tt_short_video_price?: number | null;
  tt_anchor_link_price?: number | null;
  ins_link?: string | null;
  ins_follower?: number | null;
  ins_post_price?: number | null;
  ins_reels_price?: number | null;
  yt_link?: string | null;
  yt_follower?: number | null;
  yt_avv?: number | null;
  yt_full_video_price?: number | null;
  yt_live_2hr_price?: number | null;
  yt_pre_roll_price?: number | null;
  yt_short_video_price?: number | null;
  avg_engagement?: number | null;
  extra_fields?: Record<string, string | number | null>;
  last_scraped_at?: string | null;
  updated_at: string;
};

export type KolListResponse = {
  total: number;
  items: KolRecord[];
};

export type ScrapeJob = {
  id: number;
  status: string;
  total: number;
  done: number;
  error?: string | null;
  created_at: string;
  updated_at: string;
};

export type FilterRule = {
  field: string;
  op: string;
  value?: unknown;
};

export type FilterGroup = {
  logic: "and" | "or";
  children: FilterNode[];
};

export type FilterNode = FilterRule | FilterGroup;

export type FilterPayload = {
  logic: "and" | "or";
  rules?: FilterRule[];
  children?: FilterNode[];
};

export type BusinessField = {
  key: string;
  filter_key: string;
  label: string;
  group: string;
  data_type: "text" | "number" | "link" | "date";
  source: "model" | "extra";
};

export type BusinessFieldCatalog = {
  list: BusinessField[];
  filter: BusinessField[];
  detail: BusinessField[];
  export: BusinessField[];
  create: BusinessField[];
  update: BusinessField[];
};

export type FieldInventoryItem = {
  raw_field: string;
  normalized_field: string;
  count: number;
  sources: string[];
  origins: string[];
  sample_values: string[];
  suggested_standard_field?: string | null;
  platform?: string | null;
};

export type FieldInventory = {
  items: FieldInventoryItem[];
  summary: Record<string, number>;
};

export type AliasRules = {
  standard_business_fields: BusinessField[];
  platform_alias_rules: { standard_field: string; label: string; aliases: string[] }[];
};

export const api = axios.create({ baseURL: "/kol-api" });

export function hasFilterRules(filters?: FilterPayload) {
  return Boolean(filters && ((filters.children?.length || 0) > 0 || (filters.rules?.length || 0) > 0));
}

export async function listKols(params: {
  page: number;
  pageSize: number;
  search?: string;
  filters?: FilterPayload;
}): Promise<KolListResponse> {
  const { data } = await api.get<KolListResponse>("/kols", {
    params: {
      page: params.page,
      page_size: params.pageSize,
      search: params.search || undefined,
      filters: hasFilterRules(params.filters) ? JSON.stringify(params.filters) : undefined,
    },
  });
  return data;
}

export async function createKol(values: Record<string, unknown>): Promise<KolRecord> {
  const { data } = await api.post<KolRecord>("/kols", values);
  return data;
}

export async function updateKol(id: number, values: Record<string, unknown>): Promise<KolRecord> {
  const { data } = await api.patch<KolRecord>(`/kols/${id}`, { values });
  return data;
}

export async function deleteKol(id: number): Promise<{ deleted: boolean; id: number }> {
  const { data } = await api.delete<{ deleted: boolean; id: number }>(`/kols/${id}`);
  return data;
}

export async function getKolsByIds(ids: number[]): Promise<KolRecord[]> {
  const { data } = await api.post<KolRecord[]>("/kols/by-ids", { ids });
  return data;
}

export async function importExcel(file: File, scrape: boolean) {
  const form = new FormData();
  form.append("file", file);
  const { data } = await api.post(`/kols/import?scrape=${scrape}`, form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data;
}

export async function importLinks(text: string, scrape: boolean) {
  const { data } = await api.post("/kols/import-links", { text, scrape });
  return data;
}

export async function scrapeKols(ids?: number[]) {
  const { data } = await api.post("/kols/scrape", { ids });
  return data;
}

export async function getJob(id: number): Promise<ScrapeJob> {
  const { data } = await api.get<ScrapeJob>(`/jobs/${id}`);
  return data;
}

export async function getJobs(): Promise<ScrapeJob[]> {
  const { data } = await api.get<ScrapeJob[]>("/jobs");
  return data;
}

export async function getStats() {
  const { data } = await api.get("/kols/stats");
  return data;
}

export type FieldCatalog = {
  core: { key: string; label: string }[];
  extra: { key: string; label: string; count: number }[];
};

export async function getFields(): Promise<FieldCatalog> {
  const { data } = await api.get<FieldCatalog>("/kols/fields");
  return data;
}

export async function getBusinessFields(): Promise<BusinessFieldCatalog> {
  const { data } = await api.get<BusinessFieldCatalog>("/kols/business-fields");
  return data;
}

export async function getFieldInventory(): Promise<FieldInventory> {
  const { data } = await api.get<FieldInventory>("/kols/field-inventory");
  return data;
}

export async function getFieldAliasRules(): Promise<AliasRules> {
  const { data } = await api.get<AliasRules>("/kols/field-alias-rules");
  return data;
}

export async function getFilterOptions(): Promise<Record<string, string[]>> {
  const { data } = await api.get<Record<string, string[]>>("/filters/options");
  return data;
}

export async function exportKols(params: {
  ids?: number[];
  filters?: FilterPayload;
  updateMetrics?: boolean;
  sourceFile?: string;
}): Promise<Blob> {
  const response = await api.post(
    "/kols/export",
    {
      ids: params.ids?.length ? params.ids : undefined,
      filters: params.ids?.length ? undefined : params.filters,
      update_metrics: params.updateMetrics || false,
      source_file: params.sourceFile,
    },
    { responseType: "blob" },
  );
  const blob = response.data as Blob;
  const contentType = String(response.headers["content-type"] || "");
  if (contentType.includes("json") || (blob.type && blob.type.includes("json"))) {
    const text = await blob.text();
    try {
      const err = JSON.parse(text) as { detail?: string | Array<{ msg?: string }>; error?: string };
      if (typeof err.detail === "string") throw new Error(err.detail);
      if (Array.isArray(err.detail)) throw new Error(err.detail.map((item) => item.msg).filter(Boolean).join("; ") || text);
      if (err.error) throw new Error(err.error);
      throw new Error(text);
    } catch (parseError) {
      if (parseError instanceof Error && parseError.message !== text) throw parseError;
      throw new Error(text || "导出失败");
    }
  }
  return blob;
}
