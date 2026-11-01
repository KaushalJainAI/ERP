import React, { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Search, Loader2, Compass, Package, Users, ShoppingCart, X, FileText } from "lucide-react";
import { apiClient } from "@/api/client";
import { useModuleNav } from "@/resources/nav";
import { RESOURCES } from "@/resources/registry";

interface SearchResultItem {
  key: string;
  category: "pages" | "items" | "parties" | "orders";
  title: string;
  subtitle?: string;
  path: string;
  icon: React.ComponentType<{ className?: string }>;
}

const RESULT_GROUPS: {
  category: SearchResultItem["category"];
  labelKey: string;
  label: string;
  iconClass: string;
}[] = [
  { category: "pages", labelKey: "search.group_pages", label: "Navigation & Reports", iconClass: "text-slate-400" },
  { category: "items", labelKey: "search.group_items", label: "Items & Products", iconClass: "text-emerald-400" },
  { category: "parties", labelKey: "search.group_parties", label: "Customers & Suppliers", iconClass: "text-blue-400" },
  { category: "orders", labelKey: "search.group_orders", label: "Orders & Invoices", iconClass: "text-amber-400" },
];

/**
 * The search row under the top bar.
 *
 * On a list page the box filters that list: the list reads `?search=` from the URL.
 * Matches from across the whole ERP are still listed beneath it. On every other page
 * the box searches the ERP directly.
 */
export const SearchBar: React.FC = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const nav = useModuleNav();

  const pageResource = useMemo(
    () => RESOURCES.find((r) => `/${r.module}/${r.key}` === pathname),
    [pathname]
  );
  const pageMode = !!pageResource;

  const [localQuery, setLocalQuery] = useState("");
  const query = pageMode ? searchParams.get("search") ?? "" : localQuery;
  const setQuery = (value: string) => {
    if (!pageMode) {
      setLocalQuery(value);
      return;
    }
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value) next.set("search", value);
        else next.delete("search");
        return next;
      },
      { replace: true }
    );
  };

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

  // 1. Instant client-side search over module pages, reports and record lists
  const localPageResults = useMemo<SearchResultItem[]>(() => {
    const q = query.trim().toLowerCase();
    if (!q) return [];

    const hits: SearchResultItem[] = [];

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

  // 2. Debounced remote search over items, customers, suppliers and sales orders
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

        const rowsOf = (res: PromiseSettledResult<any>) => {
          if (res.status !== "fulfilled" || !res.value.data) return [];
          return Array.isArray(res.value.data) ? res.value.data : res.value.data.results || [];
        };

        rowsOf(itemsRes).forEach((item: any) => {
          hits.push({
            key: `item-${item.id}`,
            category: "items",
            title: item.name || item.code || `Item #${item.id}`,
            subtitle: [item.code, item.stock_uom_name || item.stock_uom].filter(Boolean).join(" · "),
            path: `/stock/items/${item.id}`,
            icon: Package,
          });
        });

        rowsOf(customersRes).forEach((cust: any) => {
          hits.push({
            key: `cust-${cust.id}`,
            category: "parties",
            title: cust.name || cust.customer_name || `Customer #${cust.id}`,
            subtitle: [cust.customer_type, cust.gstin].filter(Boolean).join(" · ") || "Customer",
            path: `/selling/customers/${cust.id}`,
            icon: Users,
          });
        });

        rowsOf(suppliersRes).forEach((sup: any) => {
          hits.push({
            key: `sup-${sup.id}`,
            category: "parties",
            title: sup.name || sup.supplier_name || `Supplier #${sup.id}`,
            subtitle: [sup.supplier_type, sup.gstin].filter(Boolean).join(" · ") || "Supplier",
            path: `/buying/suppliers/${sup.id}`,
            icon: Users,
          });
        });

        rowsOf(ordersRes).forEach((so: any) => {
          hits.push({
            key: `so-${so.id}`,
            category: "orders",
            title: so.name || `Order #${so.id}`,
            subtitle: so.customer_name ? `Customer: ${so.customer_name}` : "Sales Order",
            path: `/selling/sales-orders/${so.id}`,
            icon: ShoppingCart,
          });
        });

        setRemoteResults(hits);
        setActiveIndex(0);
        // Only pop the list open while the person is typing in the box.
        if (document.activeElement === inputRef.current) setOpen(true);
      } catch {
        setRemoteResults([]);
      } finally {
        setSearching(false);
      }
    }, 250);

    return () => clearTimeout(timer);
  }, [query]);

  const allResults = useMemo(() => [...localPageResults, ...remoteResults], [localPageResults, remoteResults]);
  const indexOf = useMemo(() => new Map(allResults.map((r, i) => [r.key, i])), [allResults]);

  const go = (path: string) => {
    setOpen(false);
    // On a list page the query is the list filter in the URL; leaving the page drops it.
    if (!pageMode) setLocalQuery("");
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
      if (allResults[activeIndex]) go(allResults[activeIndex].path);
    }
  };

  const renderHit = (hit: SearchResultItem, iconClass: string) => {
    const Icon = hit.icon;
    const idx = indexOf.get(hit.key) ?? -1;
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
        <Icon className={`h-3.5 w-3.5 shrink-0 ${isActive ? "text-white" : iconClass}`} />
        <span className="min-w-0 flex-1 truncate text-xs font-medium">{hit.title}</span>
        {hit.subtitle && (
          <span className={`text-[10px] shrink-0 ${isActive ? "text-indigo-200" : "text-slate-500"}`}>
            {hit.subtitle}
          </span>
        )}
      </button>
    );
  };

  const hasAnyResults = allResults.length > 0;
  const isMac = typeof navigator !== "undefined" && /Mac|iPod|iPhone|iPad/.test(navigator.platform);
  const placeholder = pageMode
    ? t("search.placeholder_page", { page: t(pageResource!.titleKey), defaultValue: "Search {{page}}..." })
    : t("search.placeholder", "Search ERP...");

  return (
    <div className="border-b border-slate-800 bg-slate-900/90 backdrop-blur-md">
      <div className="mx-auto flex h-12 max-w-[1440px] items-center px-3 sm:px-6">
        <div ref={containerRef} className="relative w-full max-w-2xl">
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
              placeholder={placeholder}
              aria-label={placeholder}
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
            <div className="absolute inset-x-0 top-full z-50 mt-2 max-h-[70vh] overflow-y-auto rounded-xl border border-slate-800 bg-slate-900 p-1.5 shadow-2xl animate-pop-in">
              {!hasAnyResults && !searching && (
                <p className="px-3 py-6 text-center text-xs text-slate-400">
                  {t("search.no_results", { query: query.trim(), defaultValue: 'Nothing in the ERP matches "{{query}}".' })}
                </p>
              )}

              {pageMode && hasAnyResults && (
                <p className="px-2.5 pb-1 pt-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                  {t("search.across_erp", "Across the ERP")}
                </p>
              )}

              {RESULT_GROUPS.map((group) => {
                const hits = allResults.filter((r) => r.category === group.category);
                if (hits.length === 0) return null;
                return (
                  <div key={group.category} className="border-t border-slate-800/60 py-1 first:border-t-0">
                    <p className="px-2.5 pb-1 pt-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                      {t(group.labelKey, group.label)}
                    </p>
                    {hits.map((hit) => renderHit(hit, group.iconClass))}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
