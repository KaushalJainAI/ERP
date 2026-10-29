import React from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Printer } from "lucide-react";
import { PRINT_TARGETS, isChallan, printPath } from "./printTargets";

/** "Print" (and "Delivery Challan") buttons of a saved document; the print view is /print/<kind>/<id>. */
export const PrintButton: React.FC<{ module: string; resource: string; id: string | number | undefined }> = ({
  module,
  resource,
  id,
}) => {
  const { t } = useTranslation();
  const kinds = PRINT_TARGETS[`${module}/${resource}`];
  if (!kinds || id === undefined || id === null) return null;
  return (
    <>
      {kinds.map((kind) => (
        <Link
          key={kind}
          to={printPath(kind, id)}
          className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-sm font-medium transition-colors bg-slate-900 hover:bg-slate-800 border border-slate-800 shadow-sm text-slate-100"
        >
          <Printer className="w-4 h-4" /> {t(isChallan(kind) ? "prt.button.challan" : "prt.button.print")}
        </Link>
      ))}
    </>
  );
};
