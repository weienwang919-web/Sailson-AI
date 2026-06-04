import { useEffect, useMemo, useState } from "react";
import type { Key } from "react";
import { Button, Card, Drawer, Input, message, Modal, Space, Table, Tabs, Tag, Upload } from "antd";
import type { ColumnsType } from "antd/es/table";
import { UploadOutlined } from "@ant-design/icons";
import type { BusinessField, BusinessFieldCatalog, FieldInventoryItem, FilterPayload, KolRecord, ScrapeJob } from "../api";
import {
  createKol,
  deleteKol,
  exportKols,
  getBusinessFields,
  getFieldAliasRules,
  getFieldInventory,
  getFilterOptions,
  getJob,
  getJobs,
  importExcel,
  importLinks,
  listKols,
  updateKol,
} from "../api";
import { FilterBuilder } from "../components/FilterBuilder";

const { TextArea } = Input;

type AppSection = "query" | "manage" | "governance";

type KolFormState = Record<string, string>;

const defaultFilters: FilterPayload = { logic: "and", children: [] };

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
  const [inventory, setInventory] = useState<FieldInventoryItem[]>([]);
  const [aliasRuleCount, setAliasRuleCount] = useState(0);

  const filterFields = useMemo(
    () => businessFields.filter.map((field) => ({ label: field.label, value: field.filter_key, dataType: field.data_type })),
    [businessFields],
  );

  const filterCount = countFilterRules(filters);

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

  const columns: ColumnsType<KolRecord> = useMemo(() => {
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
        render: (_: unknown, record: KolRecord) => renderBusinessValue(record, field),
      };
    };
    const compact = (cols: Array<ReturnType<typeof makeFieldColumn>>) => cols.filter(Boolean) as ColumnsType<KolRecord>;
    return [
      {
        title: "基础信息",
        key: "base_group",
        children: compact([
          makeFieldColumn("name"),
          makeFieldColumn("normalized_category"),
          makeFieldColumn("platform_text"),
          makeFieldColumn("country"),
          makeFieldColumn("case_links"),
          makeFieldColumn("notes"),
        ]),
      },
      {
        title: <span className="platform-title">TikTok</span>,
        key: "tiktok_group",
        className: "platform-group platform-tiktok",
        onHeaderCell: () => ({ className: "platform-group platform-tiktok" }),
        children: compact([
          makeFieldColumn("tt_link", "platform-tiktok-sub"),
          makeFieldColumn("tt_follower", "platform-tiktok-sub"),
          makeFieldColumn("tt_avv", "platform-tiktok-sub"),
          makeFieldColumn("tt_short_video_price", "platform-tiktok-sub"),
          makeFieldColumn("tt_main_price", "platform-tiktok-sub"),
          makeFieldColumn("tt_cpm", "platform-tiktok-sub"),
          makeFieldColumn("tt_collaboration", "platform-tiktok-sub"),
        ]),
      },
      {
        title: <span className="platform-title">Instagram</span>,
        key: "instagram_group",
        className: "platform-group platform-instagram",
        onHeaderCell: () => ({ className: "platform-group platform-instagram" }),
        children: compact([
          makeFieldColumn("ins_link", "platform-instagram-sub"),
          makeFieldColumn("ins_follower", "platform-instagram-sub"),
          makeFieldColumn("ins_post_price", "platform-instagram-sub"),
          makeFieldColumn("ins_main_price", "platform-instagram-sub"),
          makeFieldColumn("ins_cpm", "platform-instagram-sub"),
          makeFieldColumn("ins_collaboration", "platform-instagram-sub"),
        ]),
      },
      {
        title: <span className="platform-title">YouTube</span>,
        key: "youtube_group",
        className: "platform-group platform-youtube",
        onHeaderCell: () => ({ className: "platform-group platform-youtube" }),
        children: compact([
          makeFieldColumn("yt_link", "platform-youtube-sub"),
          makeFieldColumn("yt_follower", "platform-youtube-sub"),
          makeFieldColumn("yt_avv", "platform-youtube-sub"),
          makeFieldColumn("yt_full_video_price", "platform-youtube-sub"),
          makeFieldColumn("yt_main_price", "platform-youtube-sub"),
          makeFieldColumn("yt_cpm", "platform-youtube-sub"),
          makeFieldColumn("yt_collaboration", "platform-youtube-sub"),
        ]),
      },
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
    ];
  }, [businessFields.list]);

  const queryColumns = useMemo(() => columns.filter((column) => column.key !== "actions"), [columns]);

  const jobColumns: ColumnsType<ScrapeJob> = [
    { title: "Job", dataIndex: "id", width: 80 },
    { title: "状态", dataIndex: "status", width: 120, render: (status: string) => <Tag color={jobColor(status)}>{status}</Tag> },
    { title: "进度", width: 110, render: (_: unknown, job: ScrapeJob) => `${job.done}/${job.total}` },
    { title: "创建时间", dataIndex: "created_at", width: 190, render: (value: string) => new Date(value).toLocaleString() },
    { title: "更新时间", dataIndex: "updated_at", width: 190, render: (value: string) => new Date(value).toLocaleString() },
    { title: "错误", dataIndex: "error", ellipsis: true },
  ];

  const inventoryColumns: ColumnsType<FieldInventoryItem> = [
    { title: "原始字段", dataIndex: "raw_field", width: 220, fixed: "left" },
    { title: "平台", dataIndex: "platform", width: 110, render: (value?: string | null) => value || "未识别" },
    { title: "处理方式", width: 190, render: (_: unknown, item: FieldInventoryItem) => item.platform ? "按平台归到商务字段" : "导出时放到兜底商务报价" },
    { title: "建议合并", dataIndex: "suggested_standard_field", width: 150, render: (value?: string | null) => value || "商务报价兜底" },
    { title: "出现次数", dataIndex: "count", width: 100, sorter: (a, b) => a.count - b.count },
    { title: "来源", dataIndex: "sources", width: 240, render: (sources: string[]) => sources.join("\n") },
    { title: "样例值", dataIndex: "sample_values", render: (values: string[]) => values.join(" / ") },
  ];

  const applyFilters = (nextFilters: FilterPayload) => {
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
    const blob = await exportKols({
      ids: selectedRowKeys.length ? selectedRowKeys.map(Number) : undefined,
      filters,
    });
    downloadBlob(blob, `kol_export_${timestampForFile()}.xlsx`);
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

  const loadFieldInventory = async () => {
    const [inventoryData, rules] = await Promise.all([getFieldInventory(), getFieldAliasRules()]);
    setInventory(inventoryData.items);
    setAliasRuleCount(rules.platform_alias_rules.reduce((sum, rule) => sum + rule.aliases.length, 0));
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
                <Table
                  rowKey="id"
                  loading={loading}
                  columns={queryColumns}
                  dataSource={items}
                  size="small"
                  scroll={{ x: 2200, y: 640 }}
                  rowSelection={{ selectedRowKeys, onChange: setSelectedRowKeys }}
                  pagination={paginationProps(total, page, pageSize, setPage, setPageSize)}
                />
              </Card>
            ),
          },
          {
            key: "manage",
            label: "KOL 管理",
            children: (
              <Card>
                <div className="toolbar">
                  <Button type="primary" onClick={openCreate}>
                    新增 KOL
                  </Button>
                  <Button onClick={() => void loadList()}>刷新列表</Button>
                </div>
                <Table
                  rowKey="id"
                  loading={loading}
                  columns={columns}
                  dataSource={items}
                  size="small"
                  scroll={{ x: 2400, y: 640 }}
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
                    <div className="work-desc">导入报价、合作模式、受众等人工字段，复杂字段会进入字段盘点。</div>
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
                  <Card className="work-card">
                    <div className="work-title">字段盘点</div>
                    <div className="work-desc">只要平台识别正确，报价字段会优先归到对应平台；识别不了的字段导出时统一追加到兜底商务报价。</div>
                    <Button onClick={loadFieldInventory}>生成字段盘点</Button>
                  </Card>
                </div>
                <Card title="任务中心">
                  <Table rowKey="id" loading={jobLoading} columns={jobColumns} dataSource={jobs} pagination={false} size="small" />
                </Card>
                <Card title={`字段盘点与商务报价兜底（已沉淀别名 ${aliasRuleCount} 个）`}>
                  <div style={{ color: "#667085", marginBottom: 12 }}>
                    字段维护的核心是不要识别错平台。能识别平台的商务报价直接进入对应平台列；不能识别平台的报价、CPM、合作模式等字段不会丢，导出时会追加到“未识别商务报价”列。
                  </div>
                  <Table rowKey="raw_field" columns={inventoryColumns} dataSource={inventory} size="small" scroll={{ x: 1200, y: 420 }} />
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
  const value = field.source === "extra" ? record.extra_fields?.[field.filter_key.replace(/^extra:/, "")] : record[field.key as keyof KolRecord];
  if (field.data_type === "link" && value) {
    return <a href={String(value)} target="_blank" rel="noreferrer">链接</a>;
  }
  return format(value);
}

function recordToForm(record: KolRecord, fields: BusinessField[]) {
  const state: KolFormState = {};
  for (const field of fields) {
    const value = field.source === "extra" ? record.extra_fields?.[field.filter_key.replace(/^extra:/, "")] : record[field.key as keyof KolRecord];
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
