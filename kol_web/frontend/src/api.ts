import axios from "axios";

export type KolRecord = {
  id: number;
  name: string;
  category: string;
  normalized_category?: string | null;
  source_file?: string | null;
  country?: string | null;
  language?: string | null;
  platform_text?: string | null;
  notes?: string | null;
  tt_link?: string | null;
  case_links?: string | null;
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

export type FilterPayload = {
  logic: "and" | "or";
  rules: FilterRule[];
};

export const api = axios.create({ baseURL: "/kol-api" });

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
      filters: params.filters?.rules.length ? JSON.stringify(params.filters) : undefined,
    },
  });
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

export async function updateKol(id: number, values: Record<string, unknown>): Promise<KolRecord> {
  const { data } = await api.patch<KolRecord>(`/kols/${id}`, { values });
  return data;
}

export async function getKolsByIds(ids: number[]): Promise<KolRecord[]> {
  const { data } = await api.post<KolRecord[]>("/kols/by-ids", { ids });
  return data;
}

export async function scrapeKols(ids?: number[]) {
  const { data } = await api.post("/kols/scrape", { ids });
  return data;
}

export async function importLinks(text: string, scrape: boolean) {
  const { data } = await api.post("/kols/import-links", { text, scrape });
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

export async function getFilterOptions(): Promise<Record<string, string[]>> {
  const { data } = await api.get<Record<string, string[]>>("/filters/options");
  return data;
}

export async function exportKols(ids: number[], updateMetrics = false, sourceFile?: string): Promise<Blob> {
  const { data } = await api.post(
    "/kols/export",
    { ids, update_metrics: updateMetrics, source_file: sourceFile },
    { responseType: "blob" },
  );
  return data;
}
