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


export type OfficialAccount = {
  id: number;
  business_id: string;
  username?: string | null;
  display_name?: string | null;
  profile_image?: string | null;
  profile_deep_link?: string | null;
  bio_description?: string | null;
  is_business_account?: boolean | null;
  is_verified?: boolean | null;
  following_count?: number | null;
  followers_count?: number | null;
  total_likes?: number | null;
  videos_count?: number | null;
  enabled: boolean;
  notes?: string | null;
  last_refreshed_at?: string | null;
  updated_at: string;
};

export type OfficialVideo = {
  id: number;
  account_id: number;
  business_id: string;
  item_id: string;
  media_type?: string | null;
  is_ad?: boolean | null;
  thumbnail_url?: string | null;
  share_url?: string | null;
  caption?: string | null;
  create_time?: string | null;
  video_duration?: number | null;
  reach?: number | null;
  video_views?: number | null;
  likes?: number | null;
  comments?: number | null;
  shares?: number | null;
  favorites?: number | null;
  total_time_watched?: number | null;
  average_time_watched?: number | null;
  full_video_watched_rate?: number | null;
  new_followers?: number | null;
  profile_views?: number | null;
  engagement_likes: Record<string, unknown>[];
  video_view_retention: Record<string, unknown>[];
  impression_sources: Record<string, unknown>[];
  audience_countries: Record<string, unknown>[];
  request_id?: string | null;
  log_id?: string | null;
  fetched_at: string;
};

export type OfficialVideoListResponse = {
  total: number;
  items: OfficialVideo[];
};

export type OfficialProfileMetric = {
  id: number;
  account_id: number;
  business_id: string;
  metric_date: string;
  followers_count?: number | null;
  video_views?: number | null;
  unique_video_views?: number | null;
  profile_views?: number | null;
  likes?: number | null;
  comments?: number | null;
  shares?: number | null;
  daily_total_followers?: number | null;
  daily_new_followers?: number | null;
  daily_lost_followers?: number | null;
  engaged_audience?: number | null;
};

export type OfficialJob = {
  id: number;
  status: string;
  total: number;
  done: number;
  error?: string | null;
  request_id?: string | null;
  log_id?: string | null;
  created_at: string;
  updated_at: string;
};

export async function listOfficialAccounts(): Promise<OfficialAccount[]> {
  const { data } = await api.get<OfficialAccount[]>("/official/accounts");
  return data;
}

export async function refreshOfficialAccounts(accountIds?: number[], days = 30): Promise<OfficialJob> {
  const { data } = await api.post<OfficialJob>("/official/refresh", { account_ids: accountIds, days });
  return data;
}

export async function listOfficialVideos(params: {
  page: number;
  pageSize: number;
  accountId?: number;
}): Promise<OfficialVideoListResponse> {
  const { data } = await api.get<OfficialVideoListResponse>("/official/videos", {
    params: { page: params.page, page_size: params.pageSize, account_id: params.accountId },
  });
  return data;
}

export async function listOfficialProfileMetrics(accountId?: number): Promise<OfficialProfileMetric[]> {
  const { data } = await api.get<OfficialProfileMetric[]>("/official/profile-metrics", {
    params: { account_id: accountId },
  });
  return data;
}

export async function exportOfficialVideos(): Promise<Blob> {
  const { data } = await api.post("/official/export", {}, { responseType: "blob" });
  return data;
}
