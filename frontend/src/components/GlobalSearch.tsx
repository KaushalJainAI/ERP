import React, { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  Search,
  Loader2,
  Compass,
  Package,
  Users,
  ShoppingCart,
  Receipt,
  X,
  FileText,
} from "lucide-react";
import { apiClient } from "@/api/client";
import { useModuleNav, type ModuleNav } from "@/resources/nav";

interface SearchResultItem {
  key: string;
  category: "pages" | "items" | "parties" | "orders";
  title: string;
  subtitle?: string;
  path: string;
  icon: React.ComponentType<{ className?: string }>;
}

export const GlobalSearch: React.FC = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const nav = useModuleNav();

  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [searching, setSearching] = useState(false);
  const [remoteResults, setRemoteResults] = useState<SearchResultItem[]>([]);
  const [activeIndex, setActiveIndex] = useState(0);

  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Global Ctrl+K / Cmd+K shortcut to focus search
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        inputRef.current?.focus();
        inputRef.current?.select();
        setOpen(true);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  // Close when clicking outside
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  // 1. Instant client-side search over all modules, documents, and reports
  const localPageResults = useMemo<SearchResultItem[]>(() => {
    const q = query.trim().toLowerCase();
    if (!q) return [];

    const hits: SearchResultItem[] = [];

    // Search module navigation pages
    nav.forEach((n) => {
      const moduleTitle = t(n.module.titleKey).toLowerCase();
      if (moduleTitle.includes(q)) {
        hits.push({
          key: `module-${n.module.key}`,
          category: "pages",
          title: t(n.module.titleKey),
          subtitle: "Module",
          path: n.path,
          icon: n.module.icon || Compass,
        });
      }

      [...n.records, ...n.reports].forEach((link) => {
        const label = t(link.labelKey).toLowerCase();
        if (label.includes(q) || link.path.toLowerCase().includes(q)) {
          hits.push({
            key: `link-${link.path}`,
            category: "pages",
            title: t(link.labelKey),
            subtitle: t(n.module.titleKey),
            path: link.path,
            icon: link.icon || FileText,
          });
        }
      });
    });

    return hits.slice(0, 5);
  }, [query, nav, t]);

  // 2. Debounced remote API search (DRF search over items, customers, suppliers, sales orders)
  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) {
      setRemoteResults([]);
      setSearching(false);
      return;
    }

    setSearching(true);
    const timer = setTimeout(async () => {
      const hits: SearchResultItem[] = [];

      try {
        const [itemsRes, customersRes, suppliersRes, ordersRes] = await Promise.allSettled([
          apiClient.get("/stock/items/", { params: { search: q, page_size: 4 } }),
          apiClient.get("/selling/customers/", { params: { search: q, page_size: 3 } }),
          apiClient.get("/buying/suppliers/", { params: { search: q, page_size: 3 } }),
          apiClient.get("/selling/sales-orders/", { params: { search: q, page_size: 3 } }),
        ]);

        // Items / Products
        if (itemsRes.status === "fulfilled" && itemsRes.value.data) {
          const list = Array.isArray(itemsRes.value.data)
            ? itemsRes.value.data
            : itemsRes.value.data.results || [];
          list.forEach((item: any) => {
            hits.push({
              key: `item-${item.id}`,
              category: "items",
              title: item.name || item.code || `Item #${item.id}`,
              subtitle: [item.code, item.stock_uom_name || item.stock_uom].filter(Boolean).join(" · "),
              path: `/stock/items/${item.id}`,
              icon: Package,
            });
          });
        }

        // Customers
        if (customersRes.status === "fulfilled" && customersRes.value.data) {
          const list = Array.isArray(customersRes.value.data)
            ? customersRes.value.data
            : customersRes.value.data.results || [];
          list.forEach((cust: any) => {
            hits.push({
              key: `cust-${cust.id}`,
              category: "parties",
              title: cust.name || cust.customer_name || `Customer #${cust.id}`,
              subtitle: [cust.customer_type, cust.gstin].filter(Boolean).join(" · ") || "Customer",
              path: `/selling/customers/${cust.id}`,
              icon: Users,
            });
          });
        }

        // Suppliers
        if (suppliersRes.status === "fulfilled" && suppliersRes.value.data) {
          const list = Array.isArray(suppliersRes.value.data)
            ? suppliersRes.value.data
            : suppliersRes.value.data.results || [];
          list.forEach((sup: any) => {
            hits.push({
              key: `sup-${sup.id}`,
              category: "parties",
              title: sup.name || sup.supplier_name || `Supplier #${sup.id}`,
              subtitle: [sup.supplier_type, sup.gstin].filter(Boolean).join(" · ") || "Supplier",
              path: `/buying/suppliers/${sup.id}`,
              icon: Users,
            });
          });
        }

        // Sales Orders
        if (ordersRes.status === "fulfilled" && ordersRes.value.data) {
          const list = Array.isArray(ordersRes.value.data)
            ? ordersRes.value.data
            : ordersRes.value.data.results || [];
          list.forEach((so: any) => {
            hits.push({
              key: `so-${so.id}`,
              category: "orders",
              title: so.name || `Order #${so.id}`,
              subtitle: so.customer_name ? `Customer: ${so.customer_name}` : "Sales Order",
              path: `/selling/sales-orders/${so.id}`,
              icon: ShoppingCart,
            });
          });
        }

        setRemoteResults(hits);
        setActiveIndex(0);
        setOpen(true);
      } catch {
        setRemoteResults([]);
      } finally {
        setSearching(false);
      }
    }, 250);

    return () => clearTimeout(timer);
  }, [query]);

  // Combine results in display order
  const allResults = useMemo(() => {
    return [...localPageResults, ...remoteResults];
  }, [localPageResults, remoteResults]);

  const go = (path: string) => {
    setOpen(false);
    setQuery("");
    navigate(path);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Escape") {
      setOpen(false);
      return;
    }
    if (!open || allResults.length === 0) return;

    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => (i + 1) % allResults.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => (i - 1 + allResults.length) % allResults.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (allResults[activeIndex]) {
        go(allResults[activeIndex].path);
      }
    }
  };

  // Group by category for structured rendering (matching NGU Admin Panel)
  const pageHits = allResults.filter((r) => r.category === "pages");
  const itemHits = allResults.filter((r) => r.category === "items");
  const partyHits = allResults.filter((r) => r.category === "parties");
  const orderHits = allResults.filter((r) => r.category === "orders");

  const hasAnyResults = allResults.length > 0;
  const isMac = typeof navigator !== "undefined" && /Mac|iPod|iPhone|iPad/.test(navigator.platform);

  return (
    <div ref={containerRef} className="relative w-full">
      <div className="relative flex items-center">
        {searching ? (
          <Loader2 className="pointer-events-none absolute start-3 h-4 w-4 animate-spin text-slate-400" />
        ) : (
          <Search className="pointer-events-none absolute start-3 h-4 w-4 text-slate-500" />
        )}
        <input
          ref={inputRef}
          type="text"
          role="searchbox"
          autoComplete="off"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            if (!open) setOpen(true);
          }}
          onFocus={() => {
            if (query.trim().length > 0) setOpen(true);
          }}
          onKeyDown={onKeyDown}
          placeholder={t("search.placeholder", "Search ERP...")}
          aria-label={t("search.placeholder", "Search ERP...")}
          className="h-9 w-full rounded-full border border-slate-800 bg-slate-800/40 ps-10 pe-16 text-[13px] text-slate-200 placeholder:text-slate-500 transition-colors hover:border-slate-700 focus:border-indigo-500 focus:bg-slate-950 focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
        />
        {query ? (
          <button
            type="button"
            onClick={() => {
              setQuery("");
              setOpen(false);
              inputRef.current?.focus();
            }}
            aria-label={t("common.clear", "Clear")}
            className="absolute end-2.5 rounded-full p-1 text-slate-500 hover:bg-slate-700/60 hover:text-slate-200"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        ) : (
          <kbd className="pointer-events-none absolute end-3 hidden lg:inline-block rounded-md border border-slate-700 bg-slate-900 px-1.5 py-0.5 font-sans text-[10px] font-medium leading-none text-slate-400">
            {isMac ? "⌘K" : "Ctrl+K"}
          </kbd>
        )}
      </div>

      {open && query.trim().length > 0 && (
        <div className="absolute start-0 top-full mt-2 w-full min-w-[20rem] lg:min-w-[24rem] max-h-[70vh] overflow-y-auto rounded-xl border border-slate-800 bg-slate-900/95 backdrop-blur-md shadow-2xl p-1.5 z-50 animate-pop-in">
          {!hasAnyResults && !searching && (
            <p className="px-3 py-6 text-center text-xs text-slate-400">
              No matching pages, stock items, or parties found for <span className="font-semibold text-slate-200">"{query.trim()}"</span>
            </p>
          )}

          {/* 1. Pages & Reports */}
          {pageHits.length > 0 && (
            <div className="py-1">
              <p className="px-2.5 pb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                Navigation & Reports
              </p>
              {pageHits.map((hit) => {
                const Icon = hit.icon;
                const idx = allResults.findIndex((r) => r.key === hit.key);
                const isActive = activeIndex === idx;
                return (
                  <button
                    key={hit.key}
                    type="button"
                    onClick={() => go(hit.path)}
                    onMouseEnter={() => setActiveIndex(idx)}
                    className={`w-full flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-start transition-colors ${
                      isActive ? "bg-indigo-600 text-white" : "text-slate-300 hover:bg-slate-800/60"
                    }`}
                  >
                    <Icon className={`h-3.5 w-3.5 shrink-0 ${isActive ? "text-white" : "text-slate-400"}`} />
                    <span className="min-w-0 flex-1 truncate text-xs font-medium">{hit.title}</span>
                    {hit.subtitle && (
                      <span className={`text-[10px] shrink-0 ${isActive ? "text-indigo-200" : "text-slate-500"}`}>
                        {hit.subtitle}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          )}

          {/* 2. Stock Items */}
          {itemHits.length > 0 && (
            <div className="py-1 border-t border-slate-800/60 mt-1">
              <p className="px-2.5 pb-1 pt-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                Items & Products
              </p>
              {itemHits.map((hit) => {
                const Icon = hit.icon;
                const idx = allResults.findIndex((r) => r.key === hit.key);
                const isActive = activeIndex === idx;
                return (
                  <button
                    key={hit.key}
                    type="button"
                    onClick={() => go(hit.path)}
                    onMouseEnter={() => setActiveIndex(idx)}
                    className={`w-full flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-start transition-colors ${
                      isActive ? "bg-indigo-600 text-white" : "text-slate-300 hover:bg-slate-800/60"
                    }`}
                  >
                    <Icon className={`h-3.5 w-3.5 shrink-0 ${isActive ? "text-white" : "text-emerald-400"}`} />
                    <span className="min-w-0 flex-1 truncate text-xs font-medium">{hit.title}</span>
                    {hit.subtitle && (
                      <span className={`text-[10px] shrink-0 ${isActive ? "text-indigo-200" : "text-slate-500"}`}>
                        {hit.subtitle}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          )}

          {/* 3. Customers & Suppliers */}
          {partyHits.length > 0 && (
            <div className="py-1 border-t border-slate-800/60 mt-1">
              <p className="px-2.5 pb-1 pt-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                Customers & Suppliers
              </p>
              {partyHits.map((hit) => {
                const Icon = hit.icon;
                const idx = allResults.findIndex((r) => r.key === hit.key);
                const isActive = activeIndex === idx;
                return (
                  <button
                    key={hit.key}
                    type="button"
                    onClick={() => go(hit.path)}
                    onMouseEnter={() => setActiveIndex(idx)}
                    className={`w-full flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-start transition-colors ${
                      isActive ? "bg-indigo-600 text-white" : "text-slate-300 hover:bg-slate-800/60"
                    }`}
                  >
                    <Icon className={`h-3.5 w-3.5 shrink-0 ${isActive ? "text-white" : "text-blue-400"}`} />
                    <span className="min-w-0 flex-1 truncate text-xs font-medium">{hit.title}</span>
                    {hit.subtitle && (
                      <span className={`text-[10px] shrink-0 ${isActive ? "text-indigo-200" : "text-slate-500"}`}>
                        {hit.subtitle}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          )}

          {/* 4. Orders & Invoices */}
          {orderHits.length > 0 && (
            <div className="py-1 border-t border-slate-800/60 mt-1">
              <p className="px-2.5 pb-1 pt-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                Orders & Invoices
              </p>
              {orderHits.map((hit) => {
                const Icon = hit.icon;
                const idx = allResults.findIndex((r) => r.key === hit.key);
                const isActive = activeIndex === idx;
                return (
                  <button
                    key={hit.key}
                    type="button"
                    onClick={() => go(hit.path)}
                    onMouseEnter={() => setActiveIndex(idx)}
                    className={`w-full flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-start transition-colors ${
                      isActive ? "bg-indigo-600 text-white" : "text-slate-300 hover:bg-slate-800/60"
                    }`}
                  >
                    <Icon className={`h-3.5 w-3.5 shrink-0 ${isActive ? "text-white" : "text-amber-400"}`} />
                    <span className="min-w-0 flex-1 truncate text-xs font-medium">{hit.title}</span>
                    {hit.subtitle && (
                      <span className={`text-[10px] shrink-0 ${isActive ? "text-indigo-200" : "text-slate-500"}`}>
                        {hit.subtitle}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
};
