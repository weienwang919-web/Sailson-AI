import { useEffect, useMemo, useState } from "react";
import type { Key } from "react";
import { Button, Card, Drawer, Input, message, Modal, Segmented, Space, Table, Tabs, Tag, Upload } from "antd";
import type { ColumnsType } from "antd/es/table";
import { UploadOutlined } from "@ant-design/icons";
import type { BusinessField, BusinessFieldCatalog, FilterNode, FilterPayload, FilterRule, KolRecord, ScrapeJob } from "../api";
import {
  createKol,
  deleteKol,
  exportKols,
  getBusinessFields,
  getFilterOptions,
  getJob,
  getJobs,
  hasFilterRules,
  importExcel,
  importLinks,
  listKols,
  updateKol,
} from "../api";
import { FilterBuilder } from "../components/FilterBuilder";

const { TextArea } = Input;

type AppSection = "query" | "manage" | "governance";

type KolFormState = Record<string, string>;

type KolPlatform = "tiktok" | "instagram" | "youtube";
type PlatformView = "auto" | KolPlatform;

const PLATFORM_META: Record<
  KolPlatform,
  { label: string; short: string; groupClass: string; subClass: string; fieldKeys: string[] }
> = {
  tiktok: {
    label: "TikTok",
    short: "TT",
    groupClass: "platform-tiktok",
    subClass: "platform-tiktok-sub",
    fieldKeys: ["tt_link", "tt_follower", "tt_avv", "tt_short_video_price", "tt_main_price", "tt_cpm", "tt_collaboration"],
  },
  instagram: {
    label: "Instagram",
    short: "INS",
    groupClass: "platform-instagram",
    subClass: "platform-instagram-sub",
    fieldKeys: ["ins_link", "ins_follower", "ins_post_price", "ins_main_price", "ins_cpm", "ins_collaboration"],
  },
  youtube: {
    label: "YouTube",
    short: "YT",
    groupClass: "platform-youtube",
    subClass: "platform-youtube-sub",
    fieldKeys: ["yt_link", "yt_follower", "yt_avv", "yt_full_video_price", "yt_main_price", "yt_cpm", "yt_collaboration"],
  },
};

const UNIFIED_PLATFORM_COLUMNS: Array<{
  role: string;
  title: string;
  width: number;
  keys: Record<KolPlatform, string | null>;
}> = [
  { role: "link", title: "链接/Link", width: 110, keys: { tiktok: "tt_link", instagram: "ins_link", youtube: "yt_link" } },
  { role: "follower", title: "粉丝/Followers", width: 120, keys: { tiktok: "tt_follower", instagram: "ins_follower", youtube: "yt_follower" } },
  { role: "avv", title: "AVV/均观看量", width: 120, keys: { tiktok: "tt_avv", instagram: null, youtube: "yt_avv" } },
  { role: "cpm", title: "CPM", width: 100, keys: { tiktok: "tt_cpm", instagram: "ins_cpm", youtube: "yt_cpm" } },
  {
    role: "collaboration",
    title: "合作模式/Collaboration",
    width: 140,
    keys: { tiktok: "tt_collaboration", instagram: "ins_collaboration", youtube: "yt_collaboration" },
  },
];

const PLATFORM_VIEW_OPTIONS: Array<{ value: PlatformView; label: string }> = [
  { value: "auto", label: "智能展示/Auto" },
  { value: "tiktok", label: "TikTok" },
  { value: "instagram", label: "Instagram" },
  { value: "youtube", label: "YouTube" },
];

const defaultFilters: FilterPayload = { logic: "and", children: [] };

const QUICK_FILTERS = {
  categories: [
    { label: "游戏/Gaming", value: "游戏/Gaming" },
    { label: "动漫娱乐/Anime & Entertainment", value: "动漫娱乐/Anime & Entertainment" },
    { label: "coser/Cosplayer", value: "coser/Cosplayer" },
    { label: "非游/Non-Gaming", value: "非游/Non-Gaming" },
  ],
  followerRanges: [
    { label: "10万以下", min: 0, max: 100000 },
    { label: "10-50万", min: 100000, max: 500000 },
    { label: "50-100万", min: 500000, max: 1000000 },
    { label: "100万+", min: 1000000, max: null },
  ],
  countries: [
    { label: "美国/US", value: "美国" },
    { label: "日本/JP", value: "日本" },
    { label: "韩国/KR", value: "韩国" },
    { label: "泰国/TH", value: "泰国" },
    { label: "印尼/ID", value: "印尼" },
    { label: "英国/UK", value: "英国" },
  ],
  priceStatus: [
    { label: "有报价", hasPrice: true },
    { label: "无报价", hasPrice: false },
  ],
};

export default function Dashboard() {
  const [activeSection, setActiveSection] = useState<AppSection>("query");
  const [items, setItems] = useState<KolRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [search, setSearch] = useState("");
  const [filters, setFilters] = useState<FilterPayload>(defaultFilters);
  const [filterOpen, setFilterOpen] = useState(false);
  const [selectedRowKeys, setSelectedRowKeys] = useState<Key[]>([]);
  const [loading, setLoading] = useState(false);
  const [businessFields, setBusinessFields] = useState<BusinessFieldCatalog>({
    list: [],
    filter: [],
    detail: [],
    export: [],
    create: [],
    update: [],
  });
  const [filterValueOptions, setFilterValueOptions] = useState<Record<string, string[]>>({});
  const [jobs, setJobs] = useState<ScrapeJob[]>([]);
  const [jobLoading, setJobLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [editRecord, setEditRecord] = useState<KolRecord | null>(null);
  const [formState, setFormState] = useState<KolFormState>({});
  const [saving, setSaving] = useState(false);
  const [linkImportOpen, setLinkImportOpen] = useState(false);
  const [linkText, setLinkText] = useState("");
  const [importing, setImporting] = useState(false);
  const [pureRefreshStatus, setPureRefreshStatus] = useState("");
  const [activeQuickFilters, setActiveQuickFilters] = useState<{
    category?: string;
    followerRange?: { min: number; max: number | null };
    country?: string;
    hasPrice?: boolean;
  }>({});
  const [platformView, setPlatformView] = useState<PlatformView>("auto");

  const filterFields = useMemo(
    () => businessFields.filter.map((field) => ({ label: field.label, value: field.filter_key, dataType: field.data_type })),
    [businessFields],
  );

  const filterCount = countFilterRules(filters);

  const stats = useMemo(() => {
    if (!items.length) return null;
    let totalFollowers = 0;
    let followerCount = 0;
    let totalPrice = 0;
    let priceCount = 0;
    let totalCpm = 0;
    let cpmCount = 0;
    for (const r of items) {
      const metrics = getRecordPlatformMetrics(r, platformView);
      if (!metrics) continue;
      if (metrics.follower) {
        totalFollowers += metrics.follower;
        followerCount++;
      }
      if (metrics.price) {
        totalPrice += metrics.price;
        priceCount++;
      }
      if (metrics.cpm) {
        totalCpm += metrics.cpm;
        cpmCount++;
      }
    }
    return {
      count: items.length,
      total,
      avgFollower: followerCount ? Math.round(totalFollowers / followerCount) : 0,
      avgPrice: priceCount ? Math.round(totalPrice / priceCount) : 0,
      avgCpm: cpmCount ? +(totalCpm / cpmCount).toFixed(1) : 0,
    };
  }, [items, total, platformView]);

  const buildQuickFilterPayload = (quick: typeof activeQuickFilters): FilterPayload => {
    const children: FilterNode[] = [];
    if (quick.category) {
      children.push({ field: "major_category", op: "eq", value: quick.category });
    }
    if (quick.followerRange) {
      const { min, max } = quick.followerRange;
      children.push({
        field: "tt_follower",
        op: max !== null ? "between" : "gte",
        value: max !== null ? [min, max] : min,
      });
    }
    if (quick.country) {
      children.push({ field: "country", op: "eq", value: quick.country });
    }
    if (quick.hasPrice !== undefined) {
      const modelPriceFields = [
        "tt_short_video_price",
        "tt_anchor_link_price",
        "ins_post_price",
        "ins_reels_price",
        "yt_full_video_price",
        "yt_live_2hr_price",
        "yt_pre_roll_price",
        "yt_short_video_price",
      ];
      // 报价汇总列还会读取 extra_fields 里的主/直播/授权报价，筛选需保持一致
      const platforms = ["TikTok", "Instagram", "YouTube", "Other"];
      const priceTypes = ["主报价", "直播报价", "授权报价"];
      const extraPriceFields = platforms.flatMap((p) =>
        priceTypes.map((t) => `extra:${p} - ${t}`)
      );
      const priceFields = [...modelPriceFields, ...extraPriceFields];
      if (quick.hasPrice) {
        // 任一字段有报价 → OR
        children.push({
          logic: "or",
          children: priceFields.map((f) => ({ field: f, op: "is_not_empty", value: undefined })),
        });
      } else {
        // 所有字段都没报价 → AND
        children.push({
          logic: "and",
          children: priceFields.map((f) => ({ field: f, op: "is_empty", value: undefined })),
        });
      }
    }
    return children.length ? { logic: "and", children } : defaultFilters;
  };

  const applyQuickFilters = (nextQuick: typeof activeQuickFilters) => {
    setActiveQuickFilters(nextQuick);
    const payload = buildQuickFilterPayload(nextQuick);
    setFilters(payload);
    setPage(1);
    void loadList({ page: 1, filters: payload });
  };

  const clearQuickFilters = () => {
    setActiveQuickFilters({});
    setFilters(defaultFilters);
    setPage(1);
    void loadList({ page: 1, filters: defaultFilters });
  };

  const loadList = async (overrides?: { page?: number; pageSize?: number; search?: string; filters?: FilterPayload }) => {
    setLoading(true);
    try {
      const nextPage = overrides?.page ?? page;
      const nextPageSize = overrides?.pageSize ?? pageSize;
      const nextSearch = overrides?.search ?? search;
      const nextFilters = overrides?.filters ?? filters;
      const data = await listKols({ page: nextPage, pageSize: nextPageSize, search: nextSearch, filters: nextFilters });
      setItems(data.items);
      setTotal(data.total);
    } finally {
      setLoading(false);
    }
  };

  const loadMetadata = async () => {
    const [fieldCatalog, options] = await Promise.all([getBusinessFields(), getFilterOptions()]);
    setBusinessFields(fieldCatalog);
    setFilterValueOptions(options);
  };

  useEffect(() => {
    void loadMetadata();
  }, []);

  useEffect(() => {
    void loadList();
  }, [page, pageSize]);

  const tableScrollX = platformView === "auto" ? 1500 : 1300;

  const platformViewBar = (
    <div className="platform-view-bar">
      <span className="quick-filter-label">平台视图/Platform View</span>
      <Segmented
        value={platformView}
        onChange={(value) => setPlatformView(value as PlatformView)}
        options={PLATFORM_VIEW_OPTIONS.map((option) => ({ label: option.label, value: option.value }))}
      />
      {platformView === "auto" ? (
        <span className="platform-view-hint">每行只展示该 KOL 的主平台数据，多平台会标注其他平台</span>
      ) : (
        <span className="platform-view-hint">仅展示 {PLATFORM_META[platformView].label} 列，无数据的单元格为空</span>
      )}
    </div>
  );

  const jobColumns: ColumnsType<ScrapeJob> = [
    { title: "Job", dataIndex: "id", width: 80 },
    { title: "状态", dataIndex: "status", width: 120, render: (status: string) => <Tag color={jobColor(status)}>{status}</Tag> },
    { title: "进度", width: 110, render: (_: unknown, job: ScrapeJob) => `${job.done}/${job.total}` },
    { title: "创建时间", dataIndex: "created_at", width: 190, render: (value: string) => new Date(value).toLocaleString() },
    { title: "更新时间", dataIndex: "updated_at", width: 190, render: (value: string) => new Date(value).toLocaleString() },
    { title: "错误", dataIndex: "error", ellipsis: true },
  ];

  const applyFilters = (nextFilters: FilterPayload) => {
    setActiveQuickFilters({});
    setFilters(nextFilters);
    setPage(1);
    setFilterOpen(false);
    void loadList({ page: 1, filters: nextFilters });
  };

  const handleSearch = (value: string) => {
    setSearch(value);
    setPage(1);
    void loadList({ page: 1, search: value });
  };

  const handleExport = async () => {
    try {
      const blob = await exportKols({
        ids: selectedRowKeys.length ? selectedRowKeys.map(Number) : undefined,
        filters: hasFilterRules(filters) ? filters : undefined,
      });
      downloadBlob(blob, `kol_export_${timestampForFile()}.xlsx`);
      message.success("导出成功");
    } catch (error) {
      const detail = error instanceof Error ? error.message : "导出失败，请稍后重试";
      message.error(detail);
    }
  };

  const openCreate = () => {
    setFormState({});
    setCreateOpen(true);
  };

  const openEdit = (record: KolRecord) => {
    setEditRecord(record);
    setFormState(recordToForm(record, businessFields.update));
  };

  const saveCreate = async () => {
    setSaving(true);
    try {
      await createKol(formToPayload(formState, businessFields.create));
      message.success("KOL 已新增");
      setCreateOpen(false);
      await loadList();
    } finally {
      setSaving(false);
    }
  };

  const saveEdit = async () => {
    if (!editRecord) return;
    setSaving(true);
    try {
      await updateKol(editRecord.id, formToPayload(formState, businessFields.update));
      message.success("KOL 已更新");
      setEditRecord(null);
      await loadList();
    } finally {
      setSaving(false);
    }
  };

  const confirmDelete = (record: KolRecord) => {
    Modal.confirm({
      title: "确认删除 KOL",
      content: `删除后将从当前资源池移除：${record.name}`,
      okText: "删除",
      okButtonProps: { danger: true },
      cancelText: "取消",
      onOk: async () => {
        await deleteKol(record.id);
        message.success("KOL 已删除");
        await loadList();
      },
    });
  };

  const queryColumns = useMemo(
    () => buildListColumns(businessFields, platformView),
    [businessFields.list, platformView],
  );

  const columns: ColumnsType<KolRecord> = useMemo(
    () => [
      ...queryColumns,
      {
        title: "操作",
        key: "actions",
        fixed: "right" as const,
        width: 150,
        render: (_: unknown, record: KolRecord) => (
          <Space>
            <Button size="small" onClick={() => openEdit(record)}>
              编辑
            </Button>
            <Button size="small" danger onClick={() => confirmDelete(record)}>
              删除
            </Button>
          </Space>
        ),
      },
    ],
    [queryColumns],
  );

  const handleExcelImport = async (file: File, scrape = false) => {
    setImporting(true);
    try {
      const result = await importExcel(file, scrape);
      message.success(`导入完成 added=${result.added}, updated=${result.updated}, skipped=${result.skipped}`);
      await loadList();
    } finally {
      setImporting(false);
    }
  };

  const handlePureRefresh = async (file: File) => {
    setImporting(true);
    setPureRefreshStatus("正在导入并拉取粉丝/AVV...");
    try {
      const result = await importExcel(file, true);
      if (result.job) {
        await waitForJobCompletion(result.job.id, (job) => setPureRefreshStatus(`Job #${job.id} ${job.status}，进度 ${job.done}/${job.total}`));
      }
      const blob = await exportKols({ ids: result.ids || [], sourceFile: result.filename });
      downloadBlob(blob, `kol_refresh_${timestampForFile()}.xlsx`);
      setPureRefreshStatus("已完成并下载");
      await loadList();
    } finally {
      setImporting(false);
    }
  };

  const handleLinkImport = async () => {
    const result = await importLinks(linkText, true);
    message.success(`链接导入完成 added=${result.added}, updated=${result.updated}, skipped=${result.skipped}`);
    setLinkText("");
    setLinkImportOpen(false);
    await loadList();
  };

  const loadJobs = async () => {
    setJobLoading(true);
    try {
      setJobs(await getJobs());
    } finally {
      setJobLoading(false);
    }
  };

  return (
    <div className="page">
      <div className="header">
        <div>
          <h1 className="title">KOL 资源池</h1>
          <div className="subtitle">业务查询、KOL 维护、数据治理分层管理</div>
        </div>
      </div>
      <Tabs
        activeKey={activeSection}
        onChange={(key) => setActiveSection(key as AppSection)}
        items={[
          {
            key: "query",
            label: "业务查询",
            children: (
              <Space direction="vertical" style={{ width: "100%" }} size="middle">
                {stats && (
                  <div className="stats-bar">
                    <div className="stats-item">
                      <span className="stats-label">当前页/总数 Page/Total</span>
                      <span className="stats-value">{stats.count}/{stats.total.toLocaleString()}</span>
                    </div>
                    <div className="stats-item">
                      <span className="stats-label">平均粉丝 Avg Followers</span>
                      <span className="stats-value">{stats.avgFollower.toLocaleString()}</span>
                    </div>
                    <div className="stats-item">
                      <span className="stats-label">平均报价 Avg Price</span>
                      <span className="stats-value">${stats.avgPrice.toLocaleString()}</span>
                    </div>
                    <div className="stats-item">
                      <span className="stats-label">平均CPM Avg CPM</span>
                      <span className="stats-value">${stats.avgCpm}</span>
                    </div>
                  </div>
                )}
                <Card>
                  <div className="toolbar toolbar-minimal">
                    <Input.Search
                      allowClear
                      value={search}
                      placeholder="搜索 KOL / 类目 / 国家 / 平台"
                      style={{ width: 300 }}
                      onChange={(event) => setSearch(event.target.value)}
                      onSearch={handleSearch}
                    />
                    <Button onClick={() => setFilterOpen(true)}>高级筛选{filterCount ? `（${filterCount}）` : ""}</Button>
                    <Button type="primary" onClick={handleExport}>
                      导出
                    </Button>
                  </div>
                  {platformViewBar}
                  <div className="quick-filters">
                    <div className="quick-filter-row">
                      <span className="quick-filter-label">大类/Category</span>
                      <Space wrap size={4}>
                        {QUICK_FILTERS.categories.map((c) => (
                          <Tag.CheckableTag
                            key={c.value}
                            checked={activeQuickFilters.category === c.value}
                            onChange={() =>
                              applyQuickFilters({
                                ...activeQuickFilters,
                                category: activeQuickFilters.category === c.value ? undefined : c.value,
                              })
                            }
                          >
                            {c.label}
                          </Tag.CheckableTag>
                        ))}
                      </Space>
                    </div>
                    <div className="quick-filter-row">
                      <span className="quick-filter-label">粉丝量级/Followers</span>
                      <Space wrap size={4}>
                        {QUICK_FILTERS.followerRanges.map((f) => (
                          <Tag.CheckableTag
                            key={f.label}
                            checked={activeQuickFilters.followerRange?.min === f.min && activeQuickFilters.followerRange?.max === f.max}
                            onChange={() =>
                              applyQuickFilters({
                                ...activeQuickFilters,
                                followerRange:
                                  activeQuickFilters.followerRange?.min === f.min && activeQuickFilters.followerRange?.max === f.max
                                    ? undefined
                                    : { min: f.min, max: f.max },
                              })
                            }
                          >
                            {f.label}
                          </Tag.CheckableTag>
                        ))}
                      </Space>
                    </div>
                    <div className="quick-filter-row">
                      <span className="quick-filter-label">国家/Country</span>
                      <Space wrap size={4}>
                        {QUICK_FILTERS.countries.map((c) => (
                          <Tag.CheckableTag
                            key={c.value}
                            checked={activeQuickFilters.country === c.value}
                            onChange={() =>
                              applyQuickFilters({
                                ...activeQuickFilters,
                                country: activeQuickFilters.country === c.value ? undefined : c.value,
                              })
                            }
                          >
                            {c.label}
                          </Tag.CheckableTag>
                        ))}
                      </Space>
                    </div>
                    <div className="quick-filter-row">
                      <span className="quick-filter-label">报价/Price</span>
                      <Space wrap size={4}>
                        {QUICK_FILTERS.priceStatus.map((p) => (
                          <Tag.CheckableTag
                            key={String(p.hasPrice)}
                            checked={activeQuickFilters.hasPrice === p.hasPrice}
                            onChange={() =>
                              applyQuickFilters({
                                ...activeQuickFilters,
                                hasPrice: activeQuickFilters.hasPrice === p.hasPrice ? undefined : p.hasPrice,
                              })
                            }
                          >
                            {p.label}
                          </Tag.CheckableTag>
                        ))}
                        <Button type="link" size="small" onClick={clearQuickFilters} style={{ padding: 0, color: "#ff4d4f" }}>
                          重置/Reset
                        </Button>
                      </Space>
                    </div>
                  </div>
                  <Table
                    rowKey="id"
                    loading={loading}
                    columns={queryColumns}
                    dataSource={items}
                    size="small"
                    scroll={{ x: tableScrollX, y: 640 }}
                    rowSelection={{ selectedRowKeys, onChange: setSelectedRowKeys }}
                    pagination={paginationProps(total, page, pageSize, setPage, setPageSize)}
                  />
                </Card>
              </Space>
            ),
          },
          {
            key: "manage",
            label: "KOL 管理",
            children: (
              <Card>
                <div className="toolbar">
                  <Input.Search
                    allowClear
                    value={search}
                    placeholder="搜索 KOL / 类目 / 国家 / 平台"
                    style={{ width: 300 }}
                    onChange={(event) => setSearch(event.target.value)}
                    onSearch={handleSearch}
                  />
                  <Button onClick={() => setFilterOpen(true)}>筛选{filterCount ? `（${filterCount}）` : ""}</Button>
                  <Button type="primary" onClick={openCreate}>
                    新增 KOL
                  </Button>
                  <Button onClick={() => void loadList()}>刷新列表</Button>
                </div>
                {platformViewBar}
                <Table
                  rowKey="id"
                  loading={loading}
                  columns={columns}
                  dataSource={items}
                  size="small"
                  scroll={{ x: tableScrollX, y: 640 }}
                  pagination={paginationProps(total, page, pageSize, setPage, setPageSize)}
                />
              </Card>
            ),
          },
          {
            key: "governance",
            label: "数据治理",
            children: (
              <Space direction="vertical" style={{ width: "100%" }} size="middle">
                <div className="workbench governance-grid">
                  <Card className="work-card">
                    <div className="work-title">Excel 导入</div>
                    <div className="work-desc">导入报价、合作模式、受众等人工字段，系统会按平台优先归到对应商务字段。</div>
                    <Upload accept=".xlsx" showUploadList={false} beforeUpload={(file) => { void handleExcelImport(file, false); return false; }}>
                      <Button type="primary" icon={<UploadOutlined />} loading={importing}>上传 Excel</Button>
                    </Upload>
                  </Card>
                  <Card className="work-card">
                    <div className="work-title">链接导入</div>
                    <div className="work-desc">批量粘贴 TikTok / Instagram / YouTube 链接，自动识别平台并入库。</div>
                    <Button onClick={() => setLinkImportOpen(true)}>打开链接导入</Button>
                  </Card>
                  <Card className="work-card">
                    <div className="work-title">纯刷数据</div>
                    <div className="work-desc">上传 Excel 后只更新粉丝数和 AVV，并下载回填后的文件。</div>
                    <Upload accept=".xlsx" showUploadList={false} disabled={importing} beforeUpload={(file) => { void handlePureRefresh(file); return false; }}>
                      <Button icon={<UploadOutlined />} loading={importing}>上传并刷数据</Button>
                    </Upload>
                    {pureRefreshStatus && <div className="work-status">{pureRefreshStatus}</div>}
                  </Card>
                  <Card className="work-card">
                    <div className="work-title">任务中心</div>
                    <div className="work-desc">查看抓取任务状态、进度和错误信息。</div>
                    <Button onClick={loadJobs}>刷新任务</Button>
                  </Card>
                </div>
                <Card title="任务中心">
                  <Table rowKey="id" loading={jobLoading} columns={jobColumns} dataSource={jobs} pagination={false} size="small" />
                </Card>
              </Space>
            ),
          },
        ]}
      />
      <Drawer title="高级筛选" open={filterOpen} onClose={() => setFilterOpen(false)} width={860}>
        <FilterBuilder value={filters} onChange={setFilters} fields={filterFields} valueOptions={filterValueOptions} />
        <Space style={{ marginTop: 16 }}>
          <Button type="primary" onClick={() => applyFilters(filters)}>
            应用筛选
          </Button>
          <Button onClick={() => applyFilters(defaultFilters)}>清空筛选</Button>
        </Space>
      </Drawer>
      <KolFormModal
        title="新增 KOL"
        open={createOpen}
        fields={businessFields.create}
        state={formState}
        saving={saving}
        onChange={setFormState}
        onCancel={() => setCreateOpen(false)}
        onOk={saveCreate}
      />
      <KolFormModal
        title={editRecord ? `编辑 ${editRecord.name}` : "编辑 KOL"}
        open={Boolean(editRecord)}
        fields={businessFields.update}
        state={formState}
        saving={saving}
        onChange={setFormState}
        onCancel={() => setEditRecord(null)}
        onOk={saveEdit}
      />
      <Modal
        title="链接导入"
        open={linkImportOpen}
        onCancel={() => setLinkImportOpen(false)}
        onOk={handleLinkImport}
        okText="导入链接"
        cancelText="取消"
        okButtonProps={{ disabled: !linkText.trim() }}
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          <div style={{ color: "#667085" }}>支持 TikTok / Instagram / YouTube 链接。导入后会立即批量拉取基础数据。</div>
          <TextArea
            rows={8}
            value={linkText}
            onChange={(event) => setLinkText(event.target.value)}
            placeholder={"https://www.tiktok.com/@...\nhttps://www.instagram.com/.../\nhttps://www.youtube.com/@..."}
          />
        </Space>
      </Modal>
    </div>
  );
}

function buildListColumns(businessFields: BusinessFieldCatalog, platformView: PlatformView): ColumnsType<KolRecord> {
  const fieldMap = new Map(businessFields.list.map((field) => [field.key, field]));
  const makeFieldColumn = (key: string, className?: string) => {
    const field = fieldMap.get(key);
    if (!field) return null;
    return {
      title: field.label.replace(/^TikTok |^Instagram |^YouTube /, ""),
      key: field.key,
      width: columnWidth(field),
      className,
      onHeaderCell: () => ({ className }),
      render: (_: unknown, record: KolRecord) => renderCellValue(record, field),
    };
  };
  const compact = (cols: Array<ReturnType<typeof makeFieldColumn>>) => cols.filter(Boolean) as ColumnsType<KolRecord>;

  const baseColumns = compact([
    makeFieldColumn("name"),
    makeFieldColumn("major_category"),
    makeFieldColumn("country"),
    makeFieldColumn("language"),
    makeFieldColumn("case_links"),
  ]);

  const platformBadgeColumn = {
    title: "平台/Platform",
    key: "active_platform",
    width: platformView === "auto" ? 130 : 100,
    render: (_: unknown, record: KolRecord) => <PlatformCell record={record} view={platformView} />,
  };

  // 统一报价列
  const priceColumn = {
    title: "报价汇总/All Prices",
    key: "all_prices",
    width: 200,
    render: (_: unknown, record: KolRecord) => <AllPricesCell record={record} />,
  };

  // 粉丝画像列（进度条可视化）
  const audienceColumns = [
    {
      title: "受众地区/Audience Region",
      key: "audience_region",
      width: 200,
      render: (_: unknown, record: KolRecord) => {
        const raw = record.audience_region || record.extra_fields?.["受众地区"];
        return <AudienceBar value={raw} type="region" />;
      },
    },
    {
      title: "性别/Audience Gender",
      key: "audience_gender",
      width: 160,
      render: (_: unknown, record: KolRecord) => {
        const raw = record.audience_gender || record.extra_fields?.["受众性别"];
        return <AudienceBar value={raw} type="gender" />;
      },
    },
    {
      title: "年龄/Audience Age",
      key: "audience_age",
      width: 180,
      render: (_: unknown, record: KolRecord) => {
        const raw = record.audience_age || record.extra_fields?.["受众年龄"];
        return <AudienceBar value={raw} type="age" />;
      },
    },
  ];

  if (platformView === "auto") {
    const unifiedColumns = UNIFIED_PLATFORM_COLUMNS.map((column) => ({
      title: column.title,
      key: `unified_${column.role}`,
      width: column.width,
      render: (_: unknown, record: KolRecord) => {
        const platform = resolveActivePlatform(record, "auto");
        if (!platform) return <span className="empty-cell">-</span>;
        const fieldKey = column.keys[platform];
        if (!fieldKey) return <span className="empty-cell">-</span>;
        const field = fieldMap.get(fieldKey);
        if (!field) return "";
        return renderCellValue(record, field);
      },
    }));

    return [
      {
        title: "基础信息/Basic Info",
        key: "base_group",
        children: [platformBadgeColumn, ...baseColumns],
      },
      {
        title: "平台数据/Platform Data",
        key: "platform_data_group",
        children: [
          ...UNIFIED_PLATFORM_COLUMNS.map((column) => ({
            title: column.title,
            key: `unified_${column.role}`,
            width: column.width,
            render: (_: unknown, record: KolRecord) => {
              const platform = resolveActivePlatform(record, "auto");
              if (!platform) return <span className="empty-cell">-</span>;
              const fieldKey = column.keys[platform];
              if (!fieldKey) return <span className="empty-cell">-</span>;
              const field = fieldMap.get(fieldKey);
              if (!field) return "";
              return renderCellValue(record, field);
            },
          })),
          priceColumn,
        ],
      },
      {
        title: "粉丝画像/Audience",
        key: "audience_group",
        children: audienceColumns,
      },
    ];
  }

  const meta = PLATFORM_META[platformView];
  return [
    {
      title: "基础信息/Basic Info",
      key: "base_group",
      children: [platformBadgeColumn, ...baseColumns],
    },
    {
      title: <span className="platform-title">{meta.label}</span>,
      key: `${platformView}_group`,
      className: `platform-group ${meta.groupClass}`,
      onHeaderCell: () => ({ className: `platform-group ${meta.groupClass}` }),
      children: compact(meta.fieldKeys.map((key) => makeFieldColumn(key, meta.subClass))),
    },
  ];
}

function PlatformCell({ record, view }: { record: KolRecord; view: PlatformView }) {
  const active = resolveActivePlatform(record, view);
  const all = listPlatformsWithData(record);
  if (!active) return <span className="empty-cell">-</span>;
  return (
    <Space size={4} wrap>
      <Tag className={`platform-tag platform-tag-${active}`}>{PLATFORM_META[active].label}</Tag>
      {view === "auto" &&
        all
          .filter((platform) => platform !== active)
          .map((platform) => (
            <Tag key={platform} className="platform-tag platform-tag-muted">
              {PLATFORM_META[platform].short}
            </Tag>
          ))}
    </Space>
  );
}

function parsePlatformsFromText(text: string): KolPlatform[] {
  const lower = text.toLowerCase();
  const found: KolPlatform[] = [];
  if (/\b(tt|tiktok)\b/.test(lower) || lower.includes("tiktok")) found.push("tiktok");
  if (/\b(ins|ig|instagram)\b/.test(lower) || lower.includes("instagram")) found.push("instagram");
  if (/\b(yt|ytb|youtube|twitch)\b/.test(lower) || lower.includes("youtube") || lower.includes("twitch")) {
    found.push("youtube");
  }
  return [...new Set(found)];
}

function platformDataScore(record: KolRecord, platform: KolPlatform): number {
  const metrics = getRecordPlatformMetrics(record, platform);
  if (!metrics) return 0;
  let score = 0;
  if (metrics.link) score += 10;
  if (metrics.follower) score += 5;
  if (metrics.avv) score += 2;
  if (metrics.price) score += 3;
  if (metrics.mainPrice) score += 2;
  if (metrics.cpm) score += 1;
  return score;
}

function listPlatformsWithData(record: KolRecord): KolPlatform[] {
  return (Object.keys(PLATFORM_META) as KolPlatform[]).filter((platform) => platformDataScore(record, platform) > 0);
}

function resolveActivePlatform(record: KolRecord, view: PlatformView): KolPlatform | null {
  if (view !== "auto") return view;
  const fromText = parsePlatformsFromText(record.platform_text || "");
  const scored = (Object.keys(PLATFORM_META) as KolPlatform[])
    .map((platform) => ({ platform, score: platformDataScore(record, platform) }))
    .filter((item) => item.score > 0);
  if (!scored.length) return fromText[0] ?? null;
  if (fromText.length === 1) return fromText[0];
  if (fromText.length > 1) {
    return fromText
      .map((platform) => ({ platform, score: platformDataScore(record, platform) }))
      .sort((a, b) => b.score - a.score)[0].platform;
  }
  return scored.sort((a, b) => b.score - a.score)[0].platform;
}

function getRecordPlatformMetrics(record: KolRecord, view: PlatformView) {
  const platform = resolveActivePlatform(record, view);
  if (!platform) return null;

  const link = platform === "tiktok" ? record.tt_link : platform === "instagram" ? record.ins_link : record.yt_link;
  const mainPriceExtraKey =
    platform === "tiktok" ? "TikTok - 主报价" : platform === "instagram" ? "Instagram - 主报价" : "YouTube - 主报价";
  const cpmExtraKey =
    platform === "tiktok" ? "TikTok - CPM" : platform === "instagram" ? "Instagram - CPM" : "YouTube - CPM";
  const follower =
    platform === "tiktok" ? record.tt_follower : platform === "instagram" ? record.ins_follower : record.yt_follower;
  const avv = platform === "tiktok" ? record.tt_avv : platform === "youtube" ? record.yt_avv : null;
  const modelPrice = platform === "tiktok" ? record.tt_short_video_price : platform === "instagram" ? record.ins_post_price : record.yt_full_video_price;
  const extraMain = record.extra_fields?.[mainPriceExtraKey];
  const price = toNumber(modelPrice) || toNumber(extraMain);
  const cpm = toNumber(record.extra_fields?.[cpmExtraKey]);

  return { platform, link, follower, avv, price, mainPrice: toNumber(extraMain), cpm };
}

function toNumber(value: unknown): number {
  if (typeof value === "number") return value;
  if (value === null || value === undefined || value === "") return 0;
  const parsed = Number(String(value).replace(/[$,￥]/g, ""));
  return Number.isFinite(parsed) ? parsed : 0;
}

function renderCellValue(record: KolRecord, field: BusinessField) {
  const value = renderBusinessValue(record, field);
  if (value === "" || value === null || value === undefined) {
    return <span className="empty-cell">-</span>;
  }
  return value;
}

// 国家代码 -> 中文名 映射
const COUNTRY_NAME_MAP: Record<string, string> = {
  US: "美国", GB: "英国", UK: "英国", TH: "泰国", JP: "日本", KR: "韩国",
  ID: "印尼", PH: "菲律宾", VN: "越南", MY: "马来西亚", SG: "新加坡",
  IN: "印度", PK: "巴基斯坦", BD: "孟加拉", MM: "缅甸", KH: "柬埔寨",
  LA: "老挝", BN: "文莱", TL: "东帝汶", NP: "尼泊尔", LK: "斯里兰卡",
  AU: "澳大利亚", NZ: "新西兰", CA: "加拿大", MX: "墨西哥", BR: "巴西",
  AR: "阿根廷", CL: "智利", CO: "哥伦比亚", PE: "秘鲁", VE: "委内瑞拉",
  DE: "德国", FR: "法国", IT: "意大利", ES: "西班牙", PT: "葡萄牙",
  NL: "荷兰", BE: "比利时", CH: "瑞士", AT: "奥地利", PL: "波兰",
  SE: "瑞典", NO: "挪威", DK: "丹麦", FI: "芬兰", IE: "爱尔兰",
  RU: "俄罗斯", UA: "乌克兰", TR: "土耳其", SA: "沙特", AE: "阿联酋",
  EG: "埃及", NG: "尼日利亚", KE: "肯尼亚", ZA: "南非", MA: "摩洛哥",
  CN: "中国", TW: "台湾", HK: "香港", MO: "澳门",
};

// 按类型严格解析受众数据，使用全局匹配支持紧贴格式（如 "18-24:55% 25-34:32%"）
function parseAudienceByType(text: string, type: string): Array<{ label: string; pct: number }> {
  if (!text) return [];
  const results: Array<{ label: string; pct: number }> = [];

  // 全局匹配：标签可能由中英文字母/数字/连字符组成，后跟可选冒号、数字、百分号
  // 用 [^\d%]+ 跳过百分号后到下个标签前的分隔符
  const pattern = /([A-Za-z0-9\u4e00-\u9fa5][A-Za-z0-9\u4e00-\u9fa5\-+]*?)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*%/g;
  let match;
  while ((match = pattern.exec(text)) !== null) {
    const rawLabel = match[1].trim();
    const pct = parseFloat(match[2]);
    if (isNaN(pct) || pct <= 0 || pct > 100) continue;

    let label = rawLabel;

    if (type === "region") {
      const upper = rawLabel.toUpperCase();
      if (/^[A-Z]{2}$/.test(upper)) {
        label = COUNTRY_NAME_MAP[upper] || upper;
      } else if (COUNTRY_NAME_MAP[upper]) {
        label = COUNTRY_NAME_MAP[upper];
      } else if (/[\u4e00-\u9fa5]/.test(rawLabel)) {
        label = rawLabel;
      } else {
        continue;
      }
    } else if (type === "gender") {
      const lower = rawLabel.toLowerCase();
      if (/男/.test(rawLabel) || /^male$|^man$|^m$/.test(lower)) label = "男";
      else if (/女/.test(rawLabel) || /^female$|^woman$|^f$/.test(lower)) label = "女";
      else continue;
    } else if (type === "age") {
      // 年龄：18-24、25-34、35+、35-、52+ 等
      if (!/^\d+(-\d+|\+|-)?$/.test(rawLabel)) continue;
      label = rawLabel;
    }

    if (label) results.push({ label, pct });
  }

  // 统一排序，避免同列顺序不一致
  if (type === "gender") {
    // 男在上，女在下
    results.sort((a, b) => (a.label === "男" ? -1 : 1) - (b.label === "男" ? -1 : 1));
  } else if (type === "age") {
    // 按年龄段起始数字升序
    results.sort((a, b) => parseInt(a.label, 10) - parseInt(b.label, 10));
  } else if (type === "region") {
    // 按占比降序
    results.sort((a, b) => b.pct - a.pct);
  }

  return results.slice(0, 6);
}

// 兼容旧调用（不指定类型时宽松解析）
function parseAudience(text: string): Array<{ label: string; pct: number }> {
  if (!text) return [];
  const parts = text.split(/[,;/&|]\s*/);
  const results: Array<{ label: string; pct: number }> = [];
  for (const part of parts) {
    const m = part.match(/([A-Za-z0-9\u4e00-\u9fa5%+\-—–\s]+?)\s*([\d.]+)\s*%/);
    if (m) {
      const label = m[1].trim();
      const pct = parseFloat(m[2]);
      if (label && !isNaN(pct)) results.push({ label, pct });
    }
  }
  return results.slice(0, 5);
}

function AudienceBar({ value, type }: { value: unknown; type?: string }) {
  const text = value ? String(value) : "";
  if (!text) return <span className="empty-cell">-</span>;
  const items = type ? parseAudienceByType(text, type) : parseAudience(text);
  if (!items.length) return <span className="audience-text" title={text}>{text}</span>;
  const typeClass = type ? ` type-${type}` : "";
  return (
    <div className={`audience-bar${typeClass}`}>
      {items.map((item) => (
        <div key={item.label} className="audience-item">
          <div className="audience-label">{item.label}</div>
          <div className="audience-track">
            <div
              className="audience-fill"
              style={{ width: `${Math.min(100, item.pct)}%` }}
            />
          </div>
          <div className="audience-pct">{item.pct}%</div>
        </div>
      ))}
    </div>
  );
}

// 判断是否为"干净的"单一报价：纯数字/金额，而非含多个金额的合作描述文字
function isCleanPrice(value: unknown): boolean {
  if (typeof value === "number") return true;
  const text = String(value).trim();
  if (!text) return false;
  // 描述性关键词（合作模式文字混入），直接排除
  if (/定制|贴片|直播|发布|套餐|打包|授权|视频|个月|一月/.test(text)) {
    // 含多个数字（多个金额拼在一起）则视为描述文字
    const numCount = (text.match(/\d[\d,.]*/g) || []).length;
    if (numCount >= 2) return false;
  }
  return true;
}

function AllPricesCell({ record }: { record: KolRecord }) {
  const ef = record.extra_fields || {};
  const rows: Array<{ label: string; value: unknown }> = [];
  const seen = new Set<string>();
  const addRow = (label: string, value: unknown) => {
    if (value === null || value === undefined || value === "") return;
    // 过滤掉描述性长文本（含多个金额或合作描述），这类是合作模式不是单一报价
    if (!isCleanPrice(value)) return;
    const key = `${label}:${value}`;
    if (seen.has(key)) return;
    seen.add(key);
    rows.push({ label, value });
  };

  // Model fields（直接字段）
  addRow("TT 短视频报价", record.tt_short_video_price);
  addRow("TT Anchor Link 报价", record.tt_anchor_link_price);
  addRow("INS Post 报价", record.ins_post_price);
  addRow("INS Reels 报价", record.ins_reels_price);
  addRow("YT 长视频报价", record.yt_full_video_price);
  addRow("YT 直播报价", record.yt_live_2hr_price);
  addRow("YT 贴片报价", record.yt_pre_roll_price);
  addRow("YT 短视频报价", record.yt_short_video_price);

  // Extra fields: 明确是报价的才显示，平台前缀统一成简短标签
  const platformPrefix: Record<string, string> = {
    TikTok: "TT ",
    Instagram: "INS ",
    YouTube: "YT ",
    Other: "", // 未识别平台不加前缀
  };
  for (const [k, v] of Object.entries(ef)) {
    if (v === null || v === undefined || v === "") continue;
    const trimmed = k.trim();
    // 排除 CPM、合作模式（不是价格）
    if (/cpm|合作模式|collaboration/i.test(trimmed)) continue;
    // 只显示报价/授权类
    if (!/报价|授权/.test(trimmed)) continue;
    // 解析 "平台 - 字段名" 格式，统一前缀
    const m = trimmed.match(/^(TikTok|Instagram|YouTube|Other)\s*-\s*(.+)$/);
    if (m) {
      addRow(`${platformPrefix[m[1]] ?? ""}${m[2]}`, v);
    } else {
      addRow(trimmed, v);
    }
  }

  if (!rows.length) return <span className="empty-cell">-</span>;
  return (
    <div className="all-prices-cell">
      {rows.map((row) => (
        <div key={row.label} className="price-row">
          <span className="price-label">{row.label}</span>
          <span className="price-value">{typeof row.value === "number" ? row.value.toLocaleString() : String(row.value)}</span>
        </div>
      ))}
    </div>
  );
}

function KolFormModal({
  title,
  open,
  fields,
  state,
  saving,
  onChange,
  onCancel,
  onOk,
}: {
  title: string;
  open: boolean;
  fields: BusinessField[];
  state: KolFormState;
  saving: boolean;
  onChange: (state: KolFormState) => void;
  onCancel: () => void;
  onOk: () => void;
}) {
  return (
    <Modal title={title} open={open} onCancel={onCancel} onOk={onOk} confirmLoading={saving} width={760} okText="保存" cancelText="取消">
      <div className="kol-form-grid">
        {fields.map((field) => (
          <label key={field.key} className="kol-form-field">
            <span>{field.label}</span>
            <Input
              value={state[field.key] || ""}
              onChange={(event) => onChange({ ...state, [field.key]: event.target.value })}
              placeholder={field.data_type === "number" ? "请输入数字" : "请输入"}
            />
          </label>
        ))}
      </div>
    </Modal>
  );
}

function renderBusinessValue(record: KolRecord, field: BusinessField) {
  const value = field.source === "extra" ? record.extra_fields?.[field.filter_key.replace(/^extra:/, "")] : (record as Record<string, unknown>)[field.key];
  if (field.data_type === "link" && value) {
    return <a href={String(value)} target="_blank" rel="noreferrer">链接</a>;
  }
  return format(value);
}

function recordToForm(record: KolRecord, fields: BusinessField[]) {
  const state: KolFormState = {};
  for (const field of fields) {
    const value = field.source === "extra" ? record.extra_fields?.[field.filter_key.replace(/^extra:/, "")] : (record as Record<string, unknown>)[field.key];
    state[field.key] = value === null || value === undefined ? "" : String(value);
  }
  return state;
}

function formToPayload(state: KolFormState, fields: BusinessField[]) {
  const payload: Record<string, unknown> = {};
  for (const field of fields) {
    const raw = state[field.key];
    if (raw === undefined) continue;
    const apiKey = field.source === "extra" ? field.filter_key : field.key;
    payload[apiKey] = field.data_type === "number" ? (raw.trim() === "" ? null : Number(raw)) : raw.trim();
  }
  return payload;
}

function columnWidth(field: BusinessField) {
  if (field.data_type === "link") return 110;
  if (field.group === "business") return 130;
  if (field.key === "name") return 180;
  if (["tt_main_price", "ins_main_price", "yt_main_price", "tt_collaboration", "ins_collaboration", "yt_collaboration"].includes(field.key)) return 140;
  return 140;
}

function paginationProps(
  total: number,
  page: number,
  pageSize: number,
  setPage: (page: number) => void,
  setPageSize: (pageSize: number) => void,
) {
  return {
    total,
    current: page,
    pageSize,
    showSizeChanger: true,
    onChange: (nextPage: number, nextPageSize: number) => {
      setPage(nextPage);
      setPageSize(nextPageSize);
    },
  };
}

function countFilterRules(filters: FilterPayload): number {
  return countFilterNodes(filters.children || filters.rules || []);
}

function countFilterNodes(children: NonNullable<FilterPayload["children"] | FilterPayload["rules"]>): number {
  return children.reduce((count, child) => count + ("children" in child ? countFilterNodes(child.children) : 1), 0);
}

function format(value: unknown) {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") return value.toLocaleString();
  return String(value);
}

function delay(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function waitForJobCompletion(jobId: number, onTick?: (job: ScrapeJob) => void) {
  for (;;) {
    await delay(3000);
    const job = await getJob(jobId);
    onTick?.(job);
    if (job.status === "completed") return job;
    if (job.status === "failed") throw new Error(`拉取失败：${job.error || `Job #${jobId}`}`);
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
