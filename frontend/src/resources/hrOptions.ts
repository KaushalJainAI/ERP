import i18n from "i18next";

/** i18n key for a backend choice value ("Half Day" -> hrm.opt.half_day). */
export const optKey = (value: string) => `hrm.opt.${value.toLowerCase().replace(/\s+/g, "_")}`;

/** Translated label of a backend choice value, for list cells and badges. */
export const optCell = (value: any): string =>
  value ? i18n.t(optKey(String(value)), { defaultValue: String(value) }) : "";
