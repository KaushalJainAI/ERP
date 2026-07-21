import React, { useState } from "react";
import { Layout } from "@/components/Layout";
import { DataTable } from "@/components/DataTable";
import { formatIndianCurrency } from "@/lib/numberFormat";
import { Boxes, Warehouse as WarehouseIcon, ArrowLeftRight, Layers } from "lucide-react";

export const StockPage: React.FC = () => {
  const [tab, setTab] = useState<"items" | "warehouses" | "entries" | "bins">("items");

  const itemColumns = [
    { key: "code", label: "Item Code" },
    { key: "name", label: "Item Name" },
    { key: "item_group_name", label: "Group" },
    { key: "stock_uom_name", label: "Stock UOM" },
    { key: "valuation_method", label: "Valuation" },
    { key: "standard_rate", label: "Standard Rate", render: (v: number) => formatIndianCurrency(v) },
  ];

  const warehouseColumns = [
    { key: "name", label: "Warehouse Name" },
    { key: "company_name", label: "Company" },
    { key: "is_group", label: "Is Group", render: (v: boolean) => (v ? "Yes" : "No") },
  ];

  const entryColumns = [
    { key: "name", label: "Voucher No" },
    { key: "stock_entry_type", label: "Entry Type" },
    { key: "posting_date", label: "Posting Date" },
    { key: "docstatus", label: "Status" },
  ];

  const binColumns = [
    { key: "item_code", label: "Item Code" },
    { key: "item_name", label: "Item Name" },
    { key: "warehouse_name", label: "Warehouse" },
    { key: "actual_qty", label: "Actual Qty" },
    { key: "valuation_rate", label: "Valuation Rate", render: (v: number) => formatIndianCurrency(v) },
    { key: "stock_value", label: "Stock Value", render: (v: number) => formatIndianCurrency(v) },
  ];

  return (
    <Layout>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-slate-100 tracking-tight">Stock & Inventory Management</h1>
            <p className="text-slate-400 text-xs mt-1">Item masters, warehouses, stock entries, and bin valuation</p>
          </div>

          <div className="flex space-x-2 bg-slate-900 p-1 border border-slate-800 rounded-xl">
            <button
              onClick={() => setTab("items")}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center space-x-2 ${
                tab === "items" ? "bg-indigo-600 text-white" : "text-slate-400 hover:text-slate-200"
              }`}
            >
              <Boxes className="w-3.5 h-3.5" />
              <span>Items</span>
            </button>
            <button
              onClick={() => setTab("warehouses")}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center space-x-2 ${
                tab === "warehouses" ? "bg-indigo-600 text-white" : "text-slate-400 hover:text-slate-200"
              }`}
            >
              <WarehouseIcon className="w-3.5 h-3.5" />
              <span>Warehouses</span>
            </button>
            <button
              onClick={() => setTab("entries")}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center space-x-2 ${
                tab === "entries" ? "bg-indigo-600 text-white" : "text-slate-400 hover:text-slate-200"
              }`}
            >
              <ArrowLeftRight className="w-3.5 h-3.5" />
              <span>Stock Entries</span>
            </button>
            <button
              onClick={() => setTab("bins")}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center space-x-2 ${
                tab === "bins" ? "bg-indigo-600 text-white" : "text-slate-400 hover:text-slate-200"
              }`}
            >
              <Layers className="w-3.5 h-3.5" />
              <span>Bin Stock</span>
            </button>
          </div>
        </div>

        {tab === "items" && (
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl">
            <DataTable title="Items Master" endpoint="/stock/items/" columns={itemColumns} searchPlaceholder="Search item code or name..." />
          </div>
        )}

        {tab === "warehouses" && (
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl">
            <DataTable title="Warehouses" endpoint="/stock/warehouses/" columns={warehouseColumns} searchPlaceholder="Search warehouse name..." />
          </div>
        )}

        {tab === "entries" && (
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl">
            <DataTable title="Stock Entries" endpoint="/stock/stock-entries/" columns={entryColumns} searchPlaceholder="Search voucher no..." />
          </div>
        )}

        {tab === "bins" && (
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl">
            <DataTable title="Derived Bin Balances" endpoint="/stock/bins/" columns={binColumns} searchPlaceholder="Search item code..." />
          </div>
        )}
      </div>
    </Layout>
  );
};
