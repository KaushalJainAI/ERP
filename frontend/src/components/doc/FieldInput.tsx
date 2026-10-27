import React from "react";
import { useTranslation } from "react-i18next";
import { isValidDecimalInput } from "@/lib/decimal";
import { formatIndianCurrency, formatIndianNumber } from "@/lib/numberFormat";
import { LinkField } from "./LinkField";
import { MultiSelectField } from "./MultiSelectField";
import type { FieldType, LinkDef, LinkRecord, SelectOption } from "@/types";

export interface InputDef {
  key: string;
  type: FieldType;
  required?: boolean;
  options?: SelectOption[];
  link?: LinkDef;
  decimals?: number;
  money?: boolean;
}

interface FieldInputProps {
  def: InputDef;
  cacheKey: string;
  value: any;
  values: Record<string, any>;
  parentValues: Record<string, any>;
  onChange: (value: any) => void;
  onLinkSelect?: (value: string | number | null, record: LinkRecord | null) => void;
  readOnly?: boolean;
  invalid?: boolean;
  compact?: boolean;
  id?: string;
  placeholder?: string;
  /** Text-like inputs only: fired when the input loses focus (server-side field checks). */
  onBlur?: () => void;
}

const defaultDecimals = (type: FieldType) => (type === "number" ? 3 : 2);

/** Single-value editor for every FieldType. Used in the form and in child-table cells. */
export const FieldInput: React.FC<FieldInputProps> = ({
  def,
  cacheKey,
  value,
  values,
  parentValues,
  onChange,
  onLinkSelect,
  readOnly,
  invalid,
  compact,
  id,
  placeholder,
  onBlur,
}) => {
  const { t } = useTranslation();
  const pad = compact ? "px-2 py-1.5 text-xs" : "px-3 py-2 text-sm";
  const base = `w-full bg-slate-950 border rounded-lg text-slate-200 focus:outline-none focus:border-indigo-500 disabled:opacity-60 disabled:cursor-not-allowed ${pad} ${
    invalid ? "border-rose-500" : "border-slate-800"
  }`;

  switch (def.type) {
    case "link":
      return (
        <LinkField
          id={id}
          link={def.link!}
          cacheKey={cacheKey}
          value={value}
          values={values}
          parentValues={parentValues}
          onSelect={(v, rec) => (onLinkSelect ? onLinkSelect(v, rec) : onChange(v))}
          disabled={readOnly}
          invalid={invalid}
          required={def.required}
          compact={compact}
          placeholder={placeholder}
        />
      );

    case "multiselect":
      return (
        <MultiSelectField
          id={id}
          link={def.link!}
          cacheKey={cacheKey}
          value={Array.isArray(value) ? value : []}
          onChange={onChange}
          disabled={readOnly}
          invalid={invalid}
        />
      );

    case "password":
      return (
        <input
          id={id}
          type="password"
          autoComplete="new-password"
          value={value ?? ""}
          disabled={readOnly}
          onChange={(e) => onChange(e.target.value)}
          className={base}
        />
      );

    case "boolean":
      return (
        <input
          id={id}
          type="checkbox"
          checked={!!value}
          disabled={readOnly}
          onChange={(e) => onChange(e.target.checked)}
          className="w-4 h-4 accent-indigo-500"
        />
      );

    case "select":
      return (
        <select
          id={id}
          value={value ?? ""}
          disabled={readOnly}
          onChange={(e) => onChange(e.target.value)}
          className={base}
        >
          {!def.required && <option value="" />}
          {(def.options || []).map((o) => (
            <option key={String(o.value)} value={o.value}>
              {t(o.labelKey)}
            </option>
          ))}
        </select>
      );

    case "textarea":
      return (
        <textarea
          id={id}
          rows={3}
          value={value ?? ""}
          disabled={readOnly}
          onChange={(e) => onChange(e.target.value)}
          className={base}
        />
      );

    case "date":
      return (
        <input
          id={id}
          type="date"
          value={value ?? ""}
          disabled={readOnly}
          onChange={(e) => onChange(e.target.value)}
          className={base}
        />
      );

    case "number":
    case "decimal": {
      const places = def.decimals ?? defaultDecimals(def.type);
      return (
        <input
          id={id}
          type="text"
          inputMode="decimal"
          value={value ?? ""}
          disabled={readOnly}
          placeholder={placeholder}
          onChange={(e) => {
            const v = e.target.value.trim();
            if (isValidDecimalInput(v, places)) onChange(v);
          }}
          className={`${base} text-right tabular-nums`}
        />
      );
    }

    case "email":
    case "text":
    default:
      return (
        <input
          id={id}
          type={def.type === "email" ? "email" : "text"}
          value={value ?? ""}
          disabled={readOnly}
          onChange={(e) => onChange(e.target.value)}
          onBlur={onBlur}
          className={base}
        />
      );
  }
};

/** Read-only text for a value (used for computed/preview cells and fully read-only docs). */
export function formatReadOnly(def: { type: FieldType; money?: boolean }, value: any): string {
  if (value === null || value === undefined || value === "") return "";
  if (Array.isArray(value)) return value.join(", ");
  if (def.type === "decimal" && def.money) return formatIndianCurrency(value);
  if (def.type === "number" || def.type === "decimal") return formatIndianNumber(value);
  return String(value);
}
