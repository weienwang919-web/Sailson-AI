import { Button, Input, Select, Space } from "antd";
import type { FilterPayload, FilterRule } from "../api";

type FieldOption = { label: string; value: string };

const ops = [
  { label: "包含 contains", value: "contains" },
  { label: "= 等于", value: "eq" },
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
  const updateRule = (idx: number, patch: Partial<FilterRule>) => {
    const next = [...value.rules];
    next[idx] = { ...next[idx], ...patch };
    onChange({ ...value, rules: next });
  };

  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Space>
        <span>逻辑 Logic</span>
        <Select
          value={value.logic}
          style={{ width: 120 }}
          onChange={(logic) => onChange({ ...value, logic })}
          options={[
            { label: "AND", value: "and" },
            { label: "OR", value: "or" },
          ]}
        />
        <Button
          onClick={() =>
            onChange({
              ...value,
              rules: [...value.rules, { field: fields[0]?.value || "category", op: "contains", value: "" }],
            })
          }
        >
          添加条件 Add Rule
        </Button>
        <Button onClick={() => onChange({ logic: "and", rules: [] })}>清空 Clear</Button>
      </Space>
      {value.rules.map((rule, idx) => (
        <Space key={idx}>
          <Select
            showSearch
            value={rule.field}
            style={{ width: 260 }}
            options={fields}
            optionFilterProp="label"
            placeholder="选择字段"
            onChange={(field) => updateRule(idx, { field, value: "" })}
          />
          <Select
            value={rule.op}
            style={{ width: 150 }}
            options={ops}
            onChange={(op) => updateRule(idx, { op })}
          />
          {!["is_empty", "is_not_empty"].includes(rule.op) && (
            valueOptions[rule.field]?.length ? (
              <Select
                showSearch
                allowClear
                value={rule.value === undefined || rule.value === "" ? undefined : String(rule.value)}
                style={{ width: 260 }}
                options={valueOptions[rule.field].map((item) => ({ label: item, value: item }))}
                optionFilterProp="label"
                placeholder="选择筛选值"
                onChange={(next) => updateRule(idx, { value: next || "" })}
              />
            ) : (
              <Input
                value={String(rule.value ?? "")}
                style={{ width: 220 }}
                onChange={(e) => updateRule(idx, { value: e.target.value })}
                placeholder="输入筛选值"
              />
            )
          )}
          <Button danger onClick={() => onChange({ ...value, rules: value.rules.filter((_, i) => i !== idx) })}>
            删除
          </Button>
        </Space>
      ))}
    </Space>
  );
}
