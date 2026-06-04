import { Button, Card, Input, Select, Space } from "antd";
import type { FilterGroup, FilterNode, FilterPayload, FilterRule } from "../api";

type FieldOption = { label: string; value: string; dataType?: string };

const ops = [
  { label: "包含 contains", value: "contains" },
  { label: "= 等于", value: "eq" },
  { label: "≠ 不等于", value: "neq" },
  { label: ">= 大于等于", value: "gte" },
  { label: "<= 小于等于", value: "lte" },
  { label: "为空 empty", value: "is_empty" },
  { label: "非空 not empty", value: "is_not_empty" },
];

type Props = {
  value: FilterPayload;
  onChange: (value: FilterPayload) => void;
  fields: FieldOption[];
  valueOptions: Record<string, string[]>;
};

export function FilterBuilder({ value, onChange, fields, valueOptions }: Props) {
  const root = toRootGroup(value, fields[0]?.value || "category");

  const commit = (next: FilterGroup) => {
    onChange({ logic: next.logic, children: next.children });
  };

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      <div style={{ color: "#667085", lineHeight: 1.6 }}>
        用条件组表达 AND / OR 混合筛选，例如“类目是游戏，并且国家是 US 或 CA”。主页面只显示入口，复杂规则在这里维护。
      </div>
      <FilterGroupEditor
        group={root}
        fields={fields}
        valueOptions={valueOptions}
        depth={0}
        onChange={commit}
        onRemove={undefined}
      />
      <Space>
        <Button onClick={() => commit({ logic: "and", children: [emptyRule(fields[0]?.value || "category")] })}>
          重置为一个条件
        </Button>
        <Button onClick={() => commit({ logic: "and", children: [] })}>清空 Clear</Button>
      </Space>
    </Space>
  );
}

type GroupEditorProps = {
  group: FilterGroup;
  fields: FieldOption[];
  valueOptions: Record<string, string[]>;
  depth: number;
  onChange: (group: FilterGroup) => void;
  onRemove?: () => void;
};

function FilterGroupEditor({ group, fields, valueOptions, depth, onChange, onRemove }: GroupEditorProps) {
  const fallbackField = fields[0]?.value || "category";
  const updateChild = (idx: number, child: FilterNode) => {
    onChange({ ...group, children: group.children.map((item, itemIdx) => (itemIdx === idx ? child : item)) });
  };
  const removeChild = (idx: number) => {
    onChange({ ...group, children: group.children.filter((_, itemIdx) => itemIdx !== idx) });
  };

  return (
    <Card size="small" style={{ marginLeft: depth * 16, borderStyle: depth ? "dashed" : "solid" }}>
      <Space direction="vertical" style={{ width: "100%" }}>
        <Space wrap>
          <span>{depth === 0 ? "根条件组" : "条件组"}</span>
          <Select
            value={group.logic}
            style={{ width: 120 }}
            onChange={(logic) => onChange({ ...group, logic })}
            options={[
              { label: "AND", value: "and" },
              { label: "OR", value: "or" },
            ]}
          />
          <Button onClick={() => onChange({ ...group, children: [...group.children, emptyRule(fallbackField)] })}>
            添加条件
          </Button>
          <Button
            onClick={() =>
              onChange({
                ...group,
                children: [...group.children, { logic: "or", children: [emptyRule(fallbackField)] }],
              })
            }
          >
            添加 OR/AND 组
          </Button>
          {onRemove && (
            <Button danger onClick={onRemove}>
              删除组
            </Button>
          )}
        </Space>
        {group.children.map((child, idx) =>
          isGroup(child) ? (
            <FilterGroupEditor
              key={idx}
              group={child}
              fields={fields}
              valueOptions={valueOptions}
              depth={depth + 1}
              onChange={(next) => updateChild(idx, next)}
              onRemove={() => removeChild(idx)}
            />
          ) : (
            <RuleEditor
              key={idx}
              rule={child}
              fields={fields}
              valueOptions={valueOptions}
              onChange={(rule) => updateChild(idx, rule)}
              onRemove={() => removeChild(idx)}
            />
          ),
        )}
        {!group.children.length && <div style={{ color: "#98a2b3" }}>当前条件组为空，应用后不会限制结果。</div>}
      </Space>
    </Card>
  );
}

type RuleEditorProps = {
  rule: FilterRule;
  fields: FieldOption[];
  valueOptions: Record<string, string[]>;
  onChange: (rule: FilterRule) => void;
  onRemove: () => void;
};

function RuleEditor({ rule, fields, valueOptions, onChange, onRemove }: RuleEditorProps) {
  return (
    <Space wrap>
      <Select
        showSearch
        value={rule.field}
        style={{ width: 260 }}
        options={fields}
        optionFilterProp="label"
        placeholder="选择字段"
        onChange={(field) => onChange({ ...rule, field, value: "" })}
      />
      <Select value={rule.op} style={{ width: 150 }} options={ops} onChange={(op) => onChange({ ...rule, op })} />
      {!['is_empty', 'is_not_empty'].includes(rule.op) &&
        (valueOptions[rule.field]?.length ? (
          <Select
            showSearch
            allowClear
            value={rule.value === undefined || rule.value === "" ? undefined : String(rule.value)}
            style={{ width: 260 }}
            options={valueOptions[rule.field].map((item) => ({ label: item, value: item }))}
            optionFilterProp="label"
            placeholder="选择筛选值"
            onChange={(next) => onChange({ ...rule, value: next || "" })}
          />
        ) : (
          <Input
            value={String(rule.value ?? "")}
            style={{ width: 220 }}
            onChange={(event) => onChange({ ...rule, value: event.target.value })}
            placeholder="输入筛选值"
          />
        ))}
      <Button danger onClick={onRemove}>
        删除
      </Button>
    </Space>
  );
}

function toRootGroup(value: FilterPayload, fallbackField: string): FilterGroup {
  if (value.children) {
    return { logic: value.logic || "and", children: value.children };
  }
  if (value.rules?.length) {
    return { logic: value.logic || "and", children: value.rules };
  }
  return { logic: "and", children: [emptyRule(fallbackField)] };
}

function emptyRule(field: string): FilterRule {
  return { field, op: "contains", value: "" };
}

function isGroup(node: FilterNode): node is FilterGroup {
  return "children" in node;
}
