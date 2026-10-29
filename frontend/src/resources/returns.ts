import { createElement } from "react";
import { Link } from "react-router-dom";
import { ReturnBadge } from "@/components/ui/ReturnBadge";
import { DocStatus } from "@/types";
import type { ChildColumnDef, FieldDef, LinkDef, ListColumnDef } from "@/types";

/**
 * Sales Return (credit note) / Purchase Return (debit note): shared UI config for the two
 * invoice resources. A return is an invoice with `is_return` and NEGATIVE quantities;
 * `return_against` is the original invoice.
 */

type Kind = "credit" | "debit";

/** Invoice line table: quantity may be negative, plus the link to the original row (kept on save). */
export function withReturnColumns<T extends { columns: ChildColumnDef[] }>(table: T, linkKey: string): T {
  return {
    ...table,
    columns: [
      ...table.columns.map((c) => (c.key === "qty" ? { ...c, allowNegative: true } : c)),
      {
        key: linkKey,
        labelKey: "ret.original_row",
        type: "number",
        decimals: 0,
        widthClass: "w-24",
        hidden: (_row, form) => !form.is_return,
      } as ChildColumnDef,
    ],
  };
}

/** List columns: the credit / debit note marker and the original document (a link). */
export function returnColumns(kind: Kind, basePath: string): ListColumnDef[] {
  return [
    {
      key: "is_return",
      labelKey: "ret.type",
      render: (v) => (v ? createElement(ReturnBadge, { kind }) : ""),
    },
    {
      key: "return_against_name",
      labelKey: "ret.return_against",
      render: (v, row) =>
        v && row.return_against
          ? createElement(Link, { to: `${basePath}/${row.return_against}`, className: "text-indigo-400 hover:underline" }, v)
          : "",
    },
  ];
}

/** Form fields: the return flag, the original invoice, the outstanding mode and a badge + link in the totals block. */
export function returnHeaderFields(
  kind: Kind,
  basePath: string,
  endpoint: string,
  partyKey: string,
  partyNameKey: string
): FieldDef[] {
  const originalLink: LinkDef = {
    endpoint,
    labelField: "name",
    subLabel: (r) => r[partyNameKey],
    display: (v) => v.return_against_name,
    filters: (_row, form) => ({
      company: form.company,
      [partyKey]: form[partyKey],
      docstatus: DocStatus.SUBMITTED,
      is_return: false,
    }),
  };
  return [
    { key: "is_return", labelKey: "ret.is_return", type: "boolean", readOnlyAfterCreate: true, defaultValue: false },
    {
      key: "return_against",
      labelKey: "ret.return_against",
      type: "link",
      link: originalLink,
      readOnlyAfterCreate: true,
      hidden: (v) => !v.is_return,
    },
    {
      key: "update_outstanding_for_self",
      labelKey: "ret.update_outstanding_for_self",
      type: "boolean",
      defaultValue: true,
      helpKey: "ret.update_outstanding_help",
      hidden: (v) => !v.is_return,
    },
    {
      key: "return_marker",
      labelKey: "ret.return_against",
      type: "text",
      readOnly: true,
      preview: (v) => (v.is_return ? (v.return_against_name ?? "") || "-" : null),
      renderValue: (_value, v) =>
        createElement(
          "span",
          { className: "inline-flex items-center gap-2" },
          createElement(ReturnBadge, { kind }),
          v.return_against && v.return_against_name
            ? createElement(
                Link,
                { to: `${basePath}/${v.return_against}`, className: "text-indigo-400 hover:underline" },
                v.return_against_name
              )
            : null
        ),
      hidden: (v, isNew) => isNew || !v.is_return,
    },
  ];
}
