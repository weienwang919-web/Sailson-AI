import { useEffect, useMemo, useState } from "react";
import type { Key } from "react";
import {
  Button,
  Card,
  Checkbox,
  Drawer,
  Input,
  message,
  Modal,
  Space,
  Switch,
  Table,
  Tag,
  Upload,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { UploadOutlined } from "@ant-design/icons";
import type { FieldCatalog, FilterPayload, KolRecord, OfficialAccount, OfficialProfileMetric, OfficialVideo, ScrapeJob } from "../api";
import { api, exportKols, exportOfficialVideos, getFields, getFilterOptions, getJob, getJobs, getKolsByIds, getStats, importExcel, importLinks, listKols, listOfficialAccounts, listOfficialProfileMetrics, listOfficialVideos, refreshOfficialAccounts, scrapeKols, updateKol } from "../api";
import { FilterBuilder } from "../components/FilterBuilder";

const { TextArea } = Input;

type ManualFieldKey = keyof KolRecord | `extra:${string}`;

const manualPricingFields: { key: ManualFieldKey; title: string; width: number }[] = [
  { key: "name", title: "KOL / 名称", width: 170 },
  { key: "category", title: "类目", width: 130 },
  { key: "notes", title: "备注", width: 180 },
  { key: "tt_short_video_price", title: "TT Short Video", width: 130 },
  { key: "tt_anchor_link_price", title: "TT Anchor Link", width: 130 },
  { key: "extra:TikTok - 合作模式", title: "TT 合作模式", width: 130 },
  { key: "extra:TikTok - 商务报价", title: "TT 商务报价", width: 130 },
  { key: "extra:TikTok - 商务CPM", title: "TT 商务CPM", width: 130 },
  { key: "ins_post_price", title: "IG Post", width: 120 },
  { key: "ins_reels_price", title: "IG Reels", width: 120 },
  { key: "extra:Instagram - 合作模式", title: "IG 合作模式", width: 130 },
  { key: "extra:Instagram - 商务报价", title: "IG 商务报价", width: 130 },
  { key: "extra:Instagram - 商务CPM", title: "IG 商务CPM", width: 130 },
  { key: "yt_full_video_price", title: "YT Full Video", width: 130 },
  { key: "yt_live_2hr_price", title: "YT Live 2hr", width: 130 },
  { key: "yt_pre_roll_price", title: "YT Pre-roll", width: 120 },
  { key: "yt_short_video_price", title: "YT Short Video", width: 130 },
  { key: "extra:YouTube - 合作模式", title: "YT 合作模式", width: 130 },
  { key: "extra:YouTube - 商务报价", title: "YT 商务报价", width: 130 },
  { key: "extra:YouTube - 商务CPM", title: "YT 商务CPM", width: 130 },
];

const numberFields = new Set([
  "tt_follower",
  "tt_avv",
  "tt_short_video_price",
  "tt_anchor_link_price",
  "ins_follower",
  "ins_post_price",
  "ins_reels_price",
  "yt_follower",
  "yt_avv",
  "yt_full_video_price",
  "yt_live_2hr_price",
  "yt_pre_roll_price",
  "yt_short_video_price",
]);

const platformHeaderStyle = {
  tiktok: { backgroundColor: "#111827", color: "#fff", textAlign: "center" as const, fontWeight: 700 },
  instagram: { backgroundColor: "#c13584", color: "#fff", textAlign: "center" as const, fontWeight: 700 },
  youtube: { backgroundColor: "#dc2626", color: "#fff", textAlign: "center" as const, fontWeight: 700 },
};

const platformSubHeaderStyle = {
  tiktok: { backgroundColor: "#eef2ff", borderTop: "2px solid #111827" },
  instagram: { backgroundColor: "#fdf2f8", borderTop: "2px solid #c13584" },
  youtube: { backgroundColor: "#fef2f2", borderTop: "2px solid #dc2626" },
};

const platformBusinessFields = {
  tiktok: ["TikTok - 合作模式", "TikTok - 主报价", "TikTok - CPM", "TikTok - 直播报价", "TikTok - 授权报价"],
  instagram: [
    "Instagram - 合作模式",
    "Instagram - 主报价",
    "Instagram - CPM",
    "Instagram - 直播报价",
    "Instagram - 授权报价",
  ],
  youtube: ["YouTube - 合作模式", "YouTube - 主报价", "YouTube - CPM", "YouTube - 直播报价", "YouTube - 授权报价"],
};

const businessExtraKeys = new Set(Object.values(platformBusinessFields).flat());

export default function Dashboard() {
  const [items, setItems] = useState<KolRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [search, setSearch] = useState("");
  const [filters, setFilters] = useState<FilterPayload>({ logic: "and", rules: [] });
  const [filterOpen, setFilterOpen] = useState(false);
  const [selectedRowKeys, setSelectedRowKeys] = useState<Key[]>([]);
  const [loading, setLoading] = useState(false);
  const [stats, setStats] = useState<Record<string, number>>({});
  const [coreFields, setCoreFields] = useState<FieldCatalog["core"]>([]);
  const [extraFields, setExtraFields] = useState<{ key: string; label: string; count: number }[]>([]);
  const [filterValueOptions, setFilterValueOptions] = useState<Record<string, string[]>>({});
  const [visibleExtraFields, setVisibleExtraFields] = useState<string[]>([]);
  const [columnSearch, setColumnSearch] = useState("");
  const [showLowFrequencyColumns, setShowLowFrequencyColumns] = useState(false);
  const [autoHideEmptyExtraColumns, setAutoHideEmptyExtraColumns] = useState(true);
  const [showRawColumnPicker, setShowRawColumnPicker] = useState(false);
  const [columnsOpen, setColumnsOpen] = useState(false);
  const [taskCenterOpen, setTaskCenterOpen] = useState(false);
  const [jobs, setJobs] = useState<ScrapeJob[]>([]);
  const [excelScrape, setExcelScrape] = useState(true);
  const [excelImporting, setExcelImporting] = useState(false);
  const [pureRefreshRunning, setPureRefreshRunning] = useState(false);
  const [pureRefreshStatus, setPureRefreshStatus] = useState("");
  const [linkImportOpen, setLinkImportOpen] = useState(false);
  const [linkText, setLinkText] = useState("");
  const [linkScrape, setLinkScrape] = useState(true);
  const [manualPricingOpen, setManualPricingOpen] = useState(false);
  const [manualPricingRows, setManualPricingRows] = useState<KolRecord[]>([]);
  const [manualPricingSaving, setManualPricingSaving] = useState(false);
  const [exportUpdating, setExportUpdating] = useState(false);
  const [officialAccounts, setOfficialAccounts] = useState<OfficialAccount[]>([]);
  const [officialVideos, setOfficialVideos] = useState<OfficialVideo[]>([]);
  const [officialTotal, setOfficialTotal] = useState(0);
  const [officialPage, setOfficialPage] = useState(1);
  const [officialPageSize, setOfficialPageSize] = useState(20);
  const [officialMetrics, setOfficialMetrics] = useState<OfficialProfileMetric[]>([]);
  const [officialLoading, setOfficialLoading] = useState(false);
  const [officialRefreshing, setOfficialRefreshing] = useState(false);
  const [officialDetail, setOfficialDetail] = useState<OfficialVideo | null>(null);

  const load = async (overrides?: { page?: number; search?: string }) => {
    setLoading(true);
    try {
      const data = await listKols({
        page: overrides?.page ?? page,
        pageSize,
        search: overrides?.search ?? search,
        filters,
      });
      setItems(data.items);
      setTotal(data.total);
      setStats(await getStats());
      const fields = await getFields();
      setCoreFields(fields.core);
      setExtraFields(fields.extra);
      setFilterValueOptions(await getFilterOptions());
    } finally {
      setLoading(false);
    }
  };

  const loadJobs = async () => {
    setJobs(await getJobs());
  };
  const loadOfficial = async (pageOverride?: number) => {
    setOfficialLoading(true);
    try {
      const accounts = await listOfficialAccounts();
      setOfficialAccounts(accounts);
      const videos = await listOfficialVideos({ page: pageOverride ?? officialPage, pageSize: officialPageSize });
      setOfficialVideos(videos.items);
      setOfficialTotal(videos.total);
      setOfficialMetrics(await listOfficialProfileMetrics(accounts[0]?.id));
    } finally {
      setOfficialLoading(false);
    }
  };


  useEffect(() => {
    load();
  }, [page, pageSize]);

  useEffect(() => {
    loadOfficial();
  }, [officialPage, officialPageSize]);

  const saveCell = async (record: KolRecord, field: keyof KolRecord | `extra:${string}`, raw: string) => {
    const value = numberFields.has(field as string) ? (raw === "" ? null : Number(raw)) : raw;
    await updateKol(record.id, { [field]: value });
    message.success("已保存 Saved");
    await load();
  };

  const editable = (field: keyof KolRecord) => ({
    render: (_: unknown, record: KolRecord) => (
      <div
        className="editable"
        contentEditable
        suppressContentEditableWarning
        onBlur={(e) => saveCell(record, field, e.currentTarget.innerText.trim())}
      >
        {format(record[field])}
      </div>
    ),
  });

  const fieldHasValueOnCurrentPage = (field: string) =>
    items.some((record) => hasValue(record.extra_fields?.[field]));

  const selectableExtraFields = useMemo(
    () => extraFields.filter((field) => !businessExtraKeys.has(field.key)),
    [extraFields],
  );

  const recommendedExtraFields = useMemo(() => {
    const threshold = Math.max(5, Math.ceil(total * 0.03));
    return selectableExtraFields
      .filter((field) => field.count >= threshold)
      .slice(0, 18)
      .map((field) => field.key);
  }, [selectableExtraFields, total]);

  const currentPageExtraFields = useMemo(
    () => selectableExtraFields.filter((field) => fieldHasValueOnCurrentPage(field.key)).map((field) => field.key),
    [selectableExtraFields, items],
  );

  const audienceExtraFields = useMemo(
    () => selectableExtraFields.filter((field) => isAudienceField(field.key)).map((field) => field.key),
    [selectableExtraFields],
  );

  const progressExtraFields = useMemo(
    () => selectableExtraFields.filter((field) => isProgressField(field.key)).map((field) => field.key),
    [selectableExtraFields],
  );

  const displayedExtraFields = useMemo(
    () =>
      autoHideEmptyExtraColumns
        ? visibleExtraFields.filter((field) => fieldHasValueOnCurrentPage(field))
        : visibleExtraFields,
    [autoHideEmptyExtraColumns, visibleExtraFields, items],
  );

  const filteredExtraFields = useMemo(() => {
    const keyword = columnSearch.trim().toLowerCase();
    const lowFrequencyThreshold = Math.max(5, Math.ceil(total * 0.01));
    return selectableExtraFields.filter((field) => {
      if (!showLowFrequencyColumns && field.count < lowFrequencyThreshold) return false;
      if (!keyword) return true;
      return `${field.label} ${field.key}`.toLowerCase().includes(keyword);
    });
  }, [columnSearch, selectableExtraFields, showLowFrequencyColumns, total]);

  const groupedExtraFields = useMemo(() => groupExtraFields(filteredExtraFields), [filteredExtraFields]);

  const columns: ColumnsType<KolRecord> = useMemo(
    () => {
      const makeExtraColumn = (field: string, platform: "tiktok" | "instagram" | "youtube") => ({
        title: field.replace(/^(TikTok|Instagram|YouTube|INS) - /, ""),
        width: 180,
        onHeaderCell: () => ({ style: platformSubHeaderStyle[platform] }),
        render: (_: unknown, record: KolRecord) => (
          <div
            className="editable"
            contentEditable
            suppressContentEditableWarning
            onBlur={(e) => saveCell(record, `extra:${field}`, e.currentTarget.innerText.trim())}
          >
            {format(record.extra_fields?.[field])}
          </div>
        ),
      });
      const makeBusinessColumns = (platform: "tiktok" | "instagram" | "youtube") =>
        platformBusinessFields[platform].map((field) => ({
          ...makeExtraColumn(field, platform),
          width: field.endsWith("合作模式") ? 130 : 110,
        }));
      const ttExtra = displayedExtraFields
        .filter((field) => field.startsWith("TikTok - "))
        .map((field) => makeExtraColumn(field, "tiktok"));
      const insExtra = displayedExtraFields
        .filter((field) => field.startsWith("Instagram - ") || field.startsWith("INS - "))
        .map((field) => makeExtraColumn(field, "instagram"));
      const ytExtra = displayedExtraFields
        .filter((field) => field.startsWith("YouTube - "))
        .map((field) => makeExtraColumn(field, "youtube"));
      const otherExtra = displayedExtraFields
        .filter(
          (field) =>
            !field.startsWith("TikTok - ") &&
            !field.startsWith("Instagram - ") &&
            !field.startsWith("INS - ") &&
            !field.startsWith("YouTube - "),
        )
        .map((field) => makeExtraColumn(field, "youtube"));
      return [
      { title: "KOL / Name", dataIndex: "name", width: 170, fixed: "left", ...editable("name") },
      { title: "标准类目", dataIndex: "normalized_category", width: 140 },
      { title: "原始类目", dataIndex: "category", width: 170, ...editable("category") },
      { title: "Source / 来源", dataIndex: "source_file", width: 180 },
      { title: "Platform / 平台", dataIndex: "platform_text", width: 120, ...editable("platform_text") },
      { title: "Country", dataIndex: "country", width: 110, ...editable("country") },
      {
        title: <span className="platform-title">TikTok</span>,
        className: "platform-group platform-tiktok",
        onHeaderCell: () => ({ style: platformHeaderStyle.tiktok }),
        children: [
          { title: "Link", dataIndex: "tt_link", width: 230, onHeaderCell: () => ({ style: platformSubHeaderStyle.tiktok }), className: "platform-tiktok-sub", ...editable("tt_link") },
          { title: "Follower", dataIndex: "tt_follower", width: 110, onHeaderCell: () => ({ style: platformSubHeaderStyle.tiktok }), className: "platform-tiktok-sub", ...editable("tt_follower") },
          { title: "AVV", dataIndex: "tt_avv", width: 110, onHeaderCell: () => ({ style: platformSubHeaderStyle.tiktok }), className: "platform-tiktok-sub", ...editable("tt_avv") },
          ...makeBusinessColumns("tiktok"),
          ...ttExtra,
        ],
      },
      {
        title: <span className="platform-title">Instagram</span>,
        className: "platform-group platform-instagram",
        onHeaderCell: () => ({ style: platformHeaderStyle.instagram }),
        children: [
          { title: "Link", dataIndex: "ins_link", width: 230, onHeaderCell: () => ({ style: platformSubHeaderStyle.instagram }), className: "platform-instagram-sub", ...editable("ins_link") },
          { title: "Follower", dataIndex: "ins_follower", width: 110, onHeaderCell: () => ({ style: platformSubHeaderStyle.instagram }), className: "platform-instagram-sub", ...editable("ins_follower") },
          ...makeBusinessColumns("instagram"),
          ...insExtra,
        ],
      },
      {
        title: <span className="platform-title">YouTube</span>,
        className: "platform-group platform-youtube",
        onHeaderCell: () => ({ style: platformHeaderStyle.youtube }),
        children: [
          { title: "Link", dataIndex: "yt_link", width: 230, onHeaderCell: () => ({ style: platformSubHeaderStyle.youtube }), className: "platform-youtube-sub", ...editable("yt_link") },
          { title: "Follower", dataIndex: "yt_follower", width: 110, onHeaderCell: () => ({ style: platformSubHeaderStyle.youtube }), className: "platform-youtube-sub", ...editable("yt_follower") },
          { title: "AVV", dataIndex: "yt_avv", width: 110, onHeaderCell: () => ({ style: platformSubHeaderStyle.youtube }), className: "platform-youtube-sub", ...editable("yt_avv") },
          ...makeBusinessColumns("youtube"),
          ...ytExtra,
        ],
      },
      { title: "案例链接", dataIndex: "case_links", width: 260, ...editable("case_links") },
      { title: "Notes / 备注", dataIndex: "notes", width: 220, ...editable("notes") },
      ...otherExtra,
    ];
    },
    [displayedExtraFields],
  );

  const filterFields = useMemo(
    () => [
      ...coreFields.map((field) => ({ label: field.label, value: field.key })),
      ...extraFields.map((field) => ({ label: `扩展 / ${field.label}`, value: `extra:${field.key}` })),
    ],
    [coreFields, extraFields],
  );

  const importProps = {
    name: "file",
    action: `/kol-api/kols/import?scrape=${excelScrape}`,
    accept: ".xlsx",
    showUploadList: false,
    onChange(info: any) {
      if (info.file.status === "uploading") {
        if (!excelImporting) {
          message.loading({ content: "正在上传并导入 Excel，请稍候...", key: "excel-import", duration: 0 });
        }
        setExcelImporting(true);
        return;
      }
      if (info.file.status === "done") {
        setExcelImporting(false);
        const jobText = info.file.response.job ? `，已创建粉丝/AVV拉取任务 Job #${info.file.response.job.id}` : "";
        message.success({
          content: `导入完成 added=${info.file.response.added}, updated=${info.file.response.updated}, skipped=${info.file.response.skipped}${jobText}`,
          key: "excel-import",
        });
        load();
        loadJobs();
      } else if (info.file.status === "error") {
        setExcelImporting(false);
        const detail = info.file.response?.detail || info.file.response?.error || "Import failed";
        message.error({ content: `导入失败 ${detail}`, key: "excel-import" });
      }
    },
  };

  const handleScrape = async () => {
    const ids = selectedRowKeys.map(Number);
    const job = await scrapeKols(ids.length ? ids : undefined);
    message.info(`已创建更新任务 Job #${job.id}`);
    await loadJobs();
  };

  const handlePureRefresh = async (file: File) => {
    setPureRefreshRunning(true);
    setPureRefreshStatus("正在导入表格并识别账号...");
    try {
      const result = await importExcel(file, true);
      const ids: number[] = result.ids || [];
      if (!ids.length) {
        message.warning("没有识别到可刷新的账号链接");
        return;
      }
      if (result.job) {
        setPureRefreshStatus(`已创建 Job #${result.job.id}，正在拉取粉丝数和 AVV...`);
        await waitForJobCompletion(result.job.id, (job) =>
          setPureRefreshStatus(`Job #${job.id} ${job.status}，进度 ${job.done}/${job.total}`),
        );
      }
      setPureRefreshStatus("拉取完成，正在生成导出文件...");
      const blob = await exportKols(ids, false, result.filename);
      downloadBlob(blob, `kol_refresh_${timestampForFile()}.xlsx`);
      message.success("纯刷数据完成，已下载更新后的 Excel");
      setPureRefreshStatus("已完成并下载");
      await load();
      await loadJobs();
    } catch (error) {
      const detail = error instanceof Error ? error.message : "未知错误";
      message.error(`纯刷数据失败：${detail}`);
      setPureRefreshStatus("处理失败，请打开任务中心查看详情");
    } finally {
      setPureRefreshRunning(false);
    }
  };

  const openManualPricing = async (ids: number[]) => {
    const records = await getKolsByIds(ids);
    setManualPricingRows(records);
    setManualPricingOpen(true);
  };

  const waitForLinkJob = async (jobId: number, ids: number[]) => {
    message.info(`正在批量拉取数据，完成后会弹出补充报价窗口 Job #${jobId}`);
    try {
      await waitForJobCompletion(jobId);
      message.success(`拉取完成 Job #${jobId}`);
      await openManualPricing(ids);
      await load();
    } catch (error) {
      message.error(error instanceof Error ? error.message : `拉取失败：Job #${jobId}`);
      await openManualPricing(ids);
    }
  };

  const handleLinkImport = async () => {
    const result = await importLinks(linkText, linkScrape);
    message.success(
      `链接导入完成 added=${result.added}, updated=${result.updated}, skipped=${result.skipped}` +
        (result.job ? `，已创建拉取任务 Job #${result.job.id}` : ""),
    );
    setLinkImportOpen(false);
    setLinkText("");
    await load();
    if (result.job) {
      await loadJobs();
      void waitForLinkJob(result.job.id, result.ids);
    } else if (result.ids?.length) {
      await openManualPricing(result.ids);
    }
  };

  const setManualValue = (rowId: number, field: ManualFieldKey, value: string) => {
    setManualPricingRows((rows) =>
      rows.map((row) => {
        if (row.id !== rowId) return row;
        if (field.startsWith("extra:")) {
          const key = field.slice(6);
          return { ...row, extra_fields: { ...(row.extra_fields || {}), [key]: value } };
        }
        return { ...row, [field]: value };
      }),
    );
  };

  const saveManualPricing = async () => {
    setManualPricingSaving(true);
    try {
      for (const row of manualPricingRows) {
        const values: Record<string, unknown> = {};
        for (const field of manualPricingFields) {
          const raw = getManualValue(row, field.key);
          values[field.key] = numberFields.has(field.key) ? (raw === "" ? null : Number(raw)) : raw;
        }
        await updateKol(row.id, values);
      }
      message.success("补充信息已保存");
      setManualPricingOpen(false);
      await load();
    } finally {
      setManualPricingSaving(false);
    }
  };

  const handleExport = async () => {
    Modal.confirm({
      title: "导出 Export",
      content: (
        <Space>
          <span>导出前更新 Follower & AVV</span>
          <Switch checked={exportUpdating} onChange={setExportUpdating} />
        </Space>
      ),
      onOk: async () => {
        const { data } = await api.post(
          "/kols/export",
          {
            ids: selectedRowKeys.length ? selectedRowKeys.map(Number) : undefined,
            filters: selectedRowKeys.length ? undefined : filters,
            update_metrics: exportUpdating,
          },
          { responseType: "blob" },
        );
        const url = window.URL.createObjectURL(data);
        const a = document.createElement("a");
        a.href = url;
        a.download = "kol_export_restored.xlsx";
        a.click();
        window.URL.revokeObjectURL(url);
      },
    });
  };
  const handleOfficialRefresh = async () => {
    setOfficialRefreshing(true);
    try {
      const job = await refreshOfficialAccounts(undefined, 30);
      if (job.status === "failed") {
        message.error(`官号刷新失败：${job.error || "未知错误"}`);
      } else {
        message.success(`官号刷新完成：${job.done}/${job.total}`);
      }
      await loadOfficial(1);
      setOfficialPage(1);
    } finally {
      setOfficialRefreshing(false);
    }
  };

  const handleOfficialExport = async () => {
    const blob = await exportOfficialVideos();
    downloadBlob(blob, `official_tiktok_monitor_${timestampForFile()}.xlsx`);
  };


  const officialVideoColumns: ColumnsType<OfficialVideo> = [
    { title: "发布时间", dataIndex: "create_time", width: 150, render: (value: string | null) => (value ? new Date(value).toLocaleString() : "") },
    { title: "caption", dataIndex: "caption", width: 260, ellipsis: true },
    { title: "播放", dataIndex: "video_views", width: 100, render: format },
    { title: "平均观看(s)", dataIndex: "average_time_watched", width: 120, render: format },
    { title: "完播率", dataIndex: "full_video_watched_rate", width: 100, render: percentFormat },
    { title: "赞", dataIndex: "likes", width: 90, render: format },
    { title: "评", dataIndex: "comments", width: 90, render: format },
    { title: "转", dataIndex: "shares", width: 90, render: format },
    { title: "收藏", dataIndex: "favorites", width: 90, render: format },
    { title: "新增粉", dataIndex: "new_followers", width: 100, render: format },
    {
      title: "操作",
      width: 120,
      render: (_: unknown, record: OfficialVideo) => <Button size="small" onClick={() => setOfficialDetail(record)}>详情</Button>,
    },
  ];

  const manualPricingColumns: ColumnsType<KolRecord> = [
    {
      title: "平台",
      width: 110,
      fixed: "left",
      render: (_: unknown, record: KolRecord) => record.platform_text || activePlatform(record),
    },
    {
      title: "链接",
      width: 260,
      fixed: "left",
      render: (_: unknown, record: KolRecord) => record.tt_link || record.ins_link || record.yt_link || "",
    },
    ...manualPricingFields.map((field) => ({
      title: field.title,
      width: field.width,
      render: (_: unknown, record: KolRecord) => (
        <Input
          size="small"
          value={getManualValue(record, field.key)}
          onChange={(e) => setManualValue(record.id, field.key, e.target.value)}
        />
      ),
    })),
  ];

  const jobColumns: ColumnsType<ScrapeJob> = [
    { title: "Job", dataIndex: "id", width: 80 },
    {
      title: "状态",
      dataIndex: "status",
      width: 120,
      render: (status: string) => <Tag color={jobColor(status)}>{status}</Tag>,
    },
    { title: "进度", width: 110, render: (_: unknown, job: ScrapeJob) => `${job.done}/${job.total}` },
    { title: "创建时间", dataIndex: "created_at", width: 190, render: (value: string) => new Date(value).toLocaleString() },
    { title: "更新时间", dataIndex: "updated_at", width: 190, render: (value: string) => new Date(value).toLocaleString() },
    { title: "错误", dataIndex: "error", ellipsis: true },
  ];

  return (
    <div className="page">
      <div className="header">
        <div>
          <h1 className="title">KOL List Manager</h1>
          <div className="subtitle">中英混排看板 · Excel 导入 · Apify 更新 · 原表版式导出</div>
        </div>
      </div>
      <div className="stats">
        <Stat label="Total" value={stats.total} />
        <Stat label="TikTok" value={stats.tiktok} />
        <Stat label="Instagram" value={stats.instagram} />
        <Stat label="YouTube" value={stats.youtube} />
      </div>
      <Card className="official-card" title="TikTok 官号监控 / Official Account Monitor">
        <Space direction="vertical" style={{ width: "100%" }}>
          <div className="official-summary">
            {officialAccounts.map((account) => (
              <div className="official-account" key={account.id}>
                <div className="official-account-name">{account.display_name || account.username || account.business_id}</div>
                <div className="official-account-meta">
                  粉丝 {format(account.followers_count)} · 视频 {format(account.videos_count)} · {account.is_business_account ? "企业号" : "账号"}
                </div>
                <div className="official-account-meta">最近刷新 {account.last_refreshed_at ? new Date(account.last_refreshed_at).toLocaleString() : "暂无"}</div>
              </div>
            ))}
            {!officialAccounts.length && <div className="official-account-meta">暂无账号配置，将使用后端 mock 账号预览。</div>}
          </div>
          <Space wrap>
            <Button type="primary" loading={officialRefreshing} onClick={handleOfficialRefresh}>刷新官方数据</Button>
            <Button onClick={handleOfficialExport}>导出官号 Excel</Button>
            <span className="official-hint">无 token 时后端会返回 mock 数据；真实联调需配置 TIKTOK_BUSINESS_ACCESS_TOKEN 和账号 open_id。</span>
          </Space>
          <div className="official-metrics">
            <Stat label="官号视频" value={officialTotal} />
            <Stat label="近日报表" value={officialMetrics.length} />
            <Stat label="总播放" value={sumOfficial(officialVideos, "video_views")} />
            <Stat label="总互动" value={sumOfficial(officialVideos, "likes") + sumOfficial(officialVideos, "comments") + sumOfficial(officialVideos, "shares")} />
          </div>
          <Table
            rowKey="id"
            loading={officialLoading}
            columns={officialVideoColumns}
            dataSource={officialVideos}
            size="small"
            scroll={{ x: 1400 }}
            pagination={{
              total: officialTotal,
              current: officialPage,
              pageSize: officialPageSize,
              showSizeChanger: true,
              onChange: (p, ps) => {
                setOfficialPage(p);
                setOfficialPageSize(ps);
              },
            }}
          />
        </Space>
      </Card>
      <div className="workbench">
        <Card className="work-card">
          <div className="work-title">1. 导入带报价 Excel</div>
          <div className="work-desc">先识别报价、合作模式、受众等人工字段；勾选后只拉取粉丝数和 AVV，不覆盖报价。</div>
          <Space>
            <Upload {...importProps}>
              <Button type="primary" icon={<UploadOutlined />} loading={excelImporting} disabled={excelImporting}>
                {excelImporting ? "导入中..." : "上传 Excel"}
              </Button>
            </Upload>
            <Switch checked={excelScrape} onChange={setExcelScrape} disabled={excelImporting} />
            <span>导入后拉粉丝/AVV</span>
          </Space>
        </Card>
        <Card className="work-card">
          <div className="work-title">2. 只上传链接</div>
          <div className="work-desc">适合临时补账号：自动识别平台、去重、拉取数据，完成后进入报价补充表。</div>
          <Button onClick={() => setLinkImportOpen(true)}>打开链接导入</Button>
        </Card>
        <Card className="work-card">
          <div className="work-title">3. 任务中心</div>
          <div className="work-desc">查看 Apify 拉取进度、成功数量和失败原因，不再只靠弹窗消息。</div>
          <Button
            onClick={async () => {
              await loadJobs();
              setTaskCenterOpen(true);
            }}
          >
            查看任务
          </Button>
        </Card>
        <Card className="work-card">
          <div className="work-title">4. 纯刷数据</div>
          <div className="work-desc">上传一份 Excel，只更新粉丝数和 AVV；任务完成后自动导出更新后的表格。</div>
          <Space direction="vertical" style={{ width: "100%" }}>
            <Upload
              accept=".xlsx"
              showUploadList={false}
              disabled={pureRefreshRunning}
              beforeUpload={(file) => {
                void handlePureRefresh(file);
                return false;
              }}
            >
              <Button loading={pureRefreshRunning} icon={<UploadOutlined />}>
                上传并刷数据
              </Button>
            </Upload>
            {pureRefreshStatus && <div className="work-status">{pureRefreshStatus}</div>}
          </Space>
        </Card>
      </div>
      <Card>
        <div className="toolbar">
          <Input.Search
            allowClear
            placeholder="搜索 KOL / Category"
            style={{ width: 260 }}
            onSearch={(value) => {
              setSearch(value);
              setPage(1);
              load({ page: 1, search: value });
            }}
          />
          <Button onClick={() => setFilterOpen(true)}>高级筛选 Advanced Filter</Button>
          <Button onClick={() => setColumnsOpen(true)}>选择列 Columns</Button>
          <Button onClick={() => setLinkImportOpen(true)}>只上传链接 Link Import</Button>
          <Button onClick={handleScrape}>更新所选数据 Update Selected</Button>
          <Button type="primary" onClick={handleExport}>导出 Export</Button>
        </div>
        <Table
          rowKey="id"
          loading={loading}
          columns={columns}
          dataSource={items}
          size="small"
          scroll={{ x: 2600, y: 640 }}
          rowSelection={{ selectedRowKeys, onChange: setSelectedRowKeys }}
          pagination={{
            total,
            current: page,
            pageSize,
            showSizeChanger: true,
            onChange: (p, ps) => {
              setPage(p);
              setPageSize(ps);
            },
          }}
        />
      </Card>
      <Drawer title="Advanced Filter / 高级筛选" open={filterOpen} onClose={() => setFilterOpen(false)} width={720}>
        <FilterBuilder value={filters} onChange={setFilters} fields={filterFields} valueOptions={filterValueOptions} />
        <Space style={{ marginTop: 16 }}>
          <Button
            type="primary"
            onClick={() => {
              setPage(1);
              setFilterOpen(false);
              load();
            }}
          >
            应用 Apply
          </Button>
        </Space>
      </Drawer>
      <Drawer title="选择视图 / Choose View" open={columnsOpen} onClose={() => setColumnsOpen(false)} width={620}>
        <Space direction="vertical" style={{ width: "100%" }}>
          <div style={{ color: "#667085", lineHeight: 1.6 }}>
            首页默认已经展示基础信息、三平台粉丝/AVV、报价大类、案例链接和备注。一般不需要逐列勾选；只有做受众分析、推进状态或排查原始表头时再打开扩展列。
          </div>
          <Space wrap>
            <Button type="primary" onClick={() => setVisibleExtraFields(recommendedExtraFields)}>
              推荐扩展
            </Button>
            <Button onClick={() => setVisibleExtraFields([])}>基础+报价视图</Button>
            <Button onClick={() => setVisibleExtraFields(audienceExtraFields)}>受众分析视图</Button>
            <Button onClick={() => setVisibleExtraFields(progressExtraFields)}>推进状态视图</Button>
            <Button onClick={() => setVisibleExtraFields(currentPageExtraFields)}>当前页有值</Button>
            <Button onClick={() => setVisibleExtraFields([])}>清空 Clear</Button>
          </Space>
          <Space wrap>
            <span>自动隐藏当前页空列</span>
            <Switch checked={autoHideEmptyExtraColumns} onChange={setAutoHideEmptyExtraColumns} />
            <span>高级：原始字段选择</span>
            <Switch checked={showRawColumnPicker} onChange={setShowRawColumnPicker} />
          </Space>
          <div style={{ color: "#98a2b3" }}>
            已选 {visibleExtraFields.length} 列，当前页实际展示 {displayedExtraFields.length} 列
          </div>
          {showRawColumnPicker && (
            <>
              <Input.Search
                allowClear
                placeholder="搜索原始字段，例如 Audience / 客户反馈 / 状态"
                value={columnSearch}
                onChange={(event) => setColumnSearch(event.target.value)}
              />
              <Space wrap>
                <span>显示低频原始字段</span>
                <Switch checked={showLowFrequencyColumns} onChange={setShowLowFrequencyColumns} />
                <Button onClick={() => setVisibleExtraFields(filteredExtraFields.map((x) => x.key))}>
                  全选当前搜索结果
                </Button>
              </Space>
              <Checkbox.Group
                style={{ width: "100%" }}
                value={visibleExtraFields}
                onChange={(values) => setVisibleExtraFields(values.map(String))}
              >
                <Space direction="vertical" style={{ width: "100%" }}>
                  {groupedExtraFields.map((group) => (
                    <div key={group.title}>
                      <div style={{ fontWeight: 700, margin: "10px 0 6px" }}>{group.title}</div>
                      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                        {group.fields.map((field) => (
                          <Checkbox key={field.key} value={field.key}>
                            {shortExtraLabel(field.label)} <span style={{ color: "#98a2b3" }}>({field.count})</span>
                          </Checkbox>
                        ))}
                      </div>
                    </div>
                  ))}
                  {!groupedExtraFields.length && <div style={{ color: "#98a2b3" }}>没有匹配的原始字段</div>}
                </Space>
              </Checkbox.Group>
            </>
          )}
          {!showRawColumnPicker && visibleExtraFields.length > 0 && (
            <div>
              <div style={{ fontWeight: 700, marginBottom: 8 }}>当前额外展示</div>
              <Space wrap>
                {visibleExtraFields.map((field) => (
                  <Tag key={field} closable onClose={() => setVisibleExtraFields((fields) => fields.filter((x) => x !== field))}>
                    {shortExtraLabel(field)}
                  </Tag>
                ))}
              </Space>
            </div>
          )}
        </Space>
      </Drawer>
      <Drawer title="任务中心 / Apify Jobs" open={taskCenterOpen} onClose={() => setTaskCenterOpen(false)} width={860}>
        <Space direction="vertical" style={{ width: "100%" }}>
          <Button onClick={loadJobs}>刷新任务状态</Button>
          <Table rowKey="id" columns={jobColumns} dataSource={jobs} pagination={false} size="small" />
        </Space>
      </Drawer>
      <Drawer title="官号视频详情" open={!!officialDetail} onClose={() => setOfficialDetail(null)} width={720}>
        {officialDetail && (
          <Space direction="vertical" style={{ width: "100%" }}>
            <div style={{ fontWeight: 700 }}>{officialDetail.caption || officialDetail.item_id}</div>
            <div>链接：{officialDetail.share_url || "暂无"}</div>
            <div>Request ID：{officialDetail.request_id || "暂无"}</div>
            <div>Log ID：{officialDetail.log_id || "暂无"}</div>
            <Distribution title="互动点赞分布" rows={officialDetail.engagement_likes} />
            <Distribution title="视频留存分布" rows={officialDetail.video_view_retention} />
            <Distribution title="流量来源" rows={officialDetail.impression_sources} />
            <Distribution title="受众国家" rows={officialDetail.audience_countries} />
          </Space>
        )}
      </Drawer>
      <Modal
        title="只上传链接 / Link-only Import"
        open={linkImportOpen}
        onCancel={() => setLinkImportOpen(false)}
        onOk={handleLinkImport}
        okText="导入链接"
        cancelText="取消"
        okButtonProps={{ disabled: !linkText.trim() }}
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          <div style={{ color: "#667085" }}>
            支持 TikTok / Instagram / YouTube 链接。这里只建账号和拉取粉丝/AVV，报价会在拉取完成后单独补充。
          </div>
          <TextArea
            rows={8}
            value={linkText}
            onChange={(e) => setLinkText(e.target.value)}
            placeholder={"https://www.tiktok.com/@...\nhttps://www.instagram.com/.../\nhttps://www.youtube.com/@..."}
          />
          <Space>
            <Upload
              accept=".txt,.csv"
              showUploadList={false}
              beforeUpload={(file) => {
                const reader = new FileReader();
                reader.onload = () => setLinkText(String(reader.result || ""));
                reader.readAsText(file);
                return false;
              }}
            >
              <Button icon={<UploadOutlined />}>上传链接文件</Button>
            </Upload>
            <Switch checked={linkScrape} onChange={setLinkScrape} />
            <span>导入后立即批量拉数据</span>
          </Space>
        </Space>
      </Modal>
      <Modal
        title="补充报价 / 合作信息"
        open={manualPricingOpen}
        onCancel={() => setManualPricingOpen(false)}
        onOk={saveManualPricing}
        okText="保存"
        cancelText="稍后再填"
        confirmLoading={manualPricingSaving}
        width={1300}
      >
        <div style={{ color: "#667085", marginBottom: 12 }}>
          这里展示本次链接导入的账号。拉取到的粉丝数/AVV 会自动回填，报价、合作模式、CPM 等可以人工补充。
        </div>
        <Table
          rowKey="id"
          columns={manualPricingColumns}
          dataSource={manualPricingRows}
          pagination={false}
          size="small"
          scroll={{ x: 2800, y: 480 }}
        />
      </Modal>
    </div>
  );
}

function Stat({ label, value }: { label: string; value?: number }) {
  return (
    <div className="stat-card">
      <div className="stat-number">{value ?? 0}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

function format(value: unknown) {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") return value.toLocaleString();
  return String(value);
}

function percentFormat(value: unknown) {
  if (value === null || value === undefined || value === "") return "";
  const n = Number(value);
  if (Number.isNaN(n)) return String(value);
  return `${(n * 100).toFixed(1)}%`;
}

function sumOfficial(rows: OfficialVideo[], field: keyof OfficialVideo) {
  return rows.reduce((sum, row) => {
    const value = Number(row[field] ?? 0);
    return sum + (Number.isNaN(value) ? 0 : value);
  }, 0);
}

function Distribution({ title, rows }: { title: string; rows: Record<string, unknown>[] }) {
  return (
    <div>
      <div style={{ fontWeight: 700, margin: "12px 0 6px" }}>{title}</div>
      {rows?.length ? (
        <Table
          size="small"
          pagination={false}
          rowKey={(_, idx) => String(idx)}
          dataSource={rows}
          columns={Object.keys(rows[0] || {}).map((key) => ({ title: key, dataIndex: key }))}
        />
      ) : (
        <div style={{ color: "#98a2b3" }}>暂无数据/权限不足/数据延迟</div>
      )}
    </div>
  );
}

function hasValue(value: unknown) {
  return value !== null && value !== undefined && String(value).trim() !== "";
}

function shortExtraLabel(label: string) {
  return label.replace(/^(TikTok|Instagram|YouTube|INS) - /, "");
}

function extraFieldGroupTitle(key: string) {
  if (key.startsWith("TikTok - ")) return "TikTok 字段";
  if (key.startsWith("Instagram - ") || key.startsWith("INS - ")) return "Instagram 字段";
  if (key.startsWith("YouTube - ")) return "YouTube 字段";
  return "其他字段";
}

function isAudienceField(key: string) {
  return /受众|性别|年龄|国家占比|地区占比|audience|gender|age|nationality|geography|distribution|活跃率|互动率|activeness/i.test(key);
}

function isProgressField(key: string) {
  return /客户反馈|是否|状态|进展|推进|档期|脚本|意见|feedback|status|schedule/i.test(key);
}

function groupExtraFields(fields: { key: string; label: string; count: number }[]) {
  const order = ["TikTok 字段", "Instagram 字段", "YouTube 字段", "其他字段"];
  const groups = new Map<string, { title: string; fields: { key: string; label: string; count: number }[] }>();
  for (const field of fields) {
    const title = extraFieldGroupTitle(field.key);
    const group = groups.get(title) || { title, fields: [] };
    group.fields.push(field);
    groups.set(title, group);
  }
  return [...groups.values()].sort((a, b) => order.indexOf(a.title) - order.indexOf(b.title));
}

function getManualValue(record: KolRecord, field: ManualFieldKey) {
  const plain = (value: unknown) => (value === null || value === undefined ? "" : String(value));
  if (field.startsWith("extra:")) {
    return plain(record.extra_fields?.[field.slice(6)]);
  }
  return plain(record[field as keyof KolRecord]);
}

function activePlatform(record: KolRecord) {
  if (record.tt_link) return "TikTok";
  if (record.ins_link) return "Instagram";
  if (record.yt_link) return "YouTube";
  return "";
}

function delay(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function waitForJobCompletion(jobId: number, onTick?: (job: ScrapeJob) => void) {
  for (;;) {
    await delay(3000);
    const job = await getJob(jobId);
    onTick?.(job);
    if (job.status === "completed") {
      return job;
    }
    if (job.status === "failed") {
      throw new Error(`拉取失败：${job.error || `Job #${jobId}`}`);
    }
  }
}

function downloadBlob(blob: Blob, filename: string) {
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  window.URL.revokeObjectURL(url);
}

function timestampForFile() {
  return new Date().toISOString().replace(/[-:]/g, "").replace(/\..+/, "").replace("T", "_");
}

function jobColor(status: string) {
  if (status === "completed") return "green";
  if (status === "failed") return "red";
  if (status === "running") return "blue";
  return "default";
}
