/**
 * TypeScript definitions for Marketing & Sales KPI Tracking.
 */

export interface KPISummary {
  net_revenue: string;
  revenue_growth_pct: number | null;
  volume: string;
  volume_growth_pct: number | null;
  invoices_count: number;
  average_order_value: string;
  conversion_rate_pct: number;
  average_conversion_days: number;
  active_customers: number;
  new_customers: number;
  repeat_rate_pct: number;
  total_receivables: string;
  dso_days: number;
}

export interface FunnelStage {
  stage: string;
  count: number;
  amount: string;
}

export interface ConversionAndVelocity {
  total_orders_count: number;
  total_order_value: string;
  total_order_net: string;
  converted_orders_count: number;
  unconverted_orders_count: number;
  conversion_rate_pct: number;
  fully_converted_count: number;
  partially_converted_count: number;
  average_conversion_days: number;
  median_conversion_days: number;
  velocity_distribution: {
    same_day: number;
    "1_to_3_days": number;
    "4_to_7_days": number;
    "8_to_14_days": number;
    "15_plus_days": number;
  };
  pipeline_funnel: FunnelStage[];
}

export interface MonthlyTrend {
  period: string;
  label: string;
  net_revenue: string;
  gross_revenue: string;
  tax_amount: string;
  volume: string;
  invoices_count: number;
  growth_pct: number | null;
}

export interface RevenueAndVolume {
  current_period: {
    from_date: string;
    to_date: string;
    net_revenue: string;
    gross_revenue: string;
    total_tax: string;
    volume: string;
    invoices_count: number;
    average_order_value: string;
  };
  previous_period: {
    from_date: string;
    to_date: string;
    net_revenue: string;
    gross_revenue: string;
    volume: string;
    invoices_count: number;
    average_order_value: string;
  };
  growth: {
    revenue_growth_pct: number | null;
    gross_growth_pct: number | null;
    volume_growth_pct: number | null;
    invoices_growth_pct: number | null;
    aov_growth_pct: number | null;
  };
  monthly_trends: MonthlyTrend[];
}

export interface ProductKPI {
  item_id: number;
  item_code: string;
  item_name: string;
  item_group: string;
  uom: string;
  quantity_sold: string;
  revenue: string;
  average_rate: string;
  invoices_count: number;
  contribution_pct: number;
  is_spice_blend: boolean;
}

export interface TopProductsData {
  total_revenue: string;
  total_quantity: string;
  top_products: ProductKPI[];
  top_spice_blends: ProductKPI[];
  spice_blends_count: number;
  total_products_count: number;
}

export interface TopCustomerKPI {
  customer_id: number;
  customer_name: string;
  customer_group: string;
  revenue: string;
  invoices_count: number;
  revenue_share_pct: number;
}

export interface CustomerAnalyticsData {
  active_customers_count: number;
  new_customers_count: number;
  repeat_customers_count: number;
  churned_customers_count: number;
  repeat_rate_pct: number;
  concentration: {
    top_5_share_pct: number;
    top_10_share_pct: number;
    pareto_20_pct_customers_share: number;
    hhi_index: number;
    concentration_risk: "Low" | "Moderate" | "High" | string;
  };
  top_customers: TopCustomerKPI[];
}

export interface AgeingBucket {
  amount: string;
  count: number;
  share_pct: number;
}

export interface ReceivablesAndDSO {
  as_of_date: string;
  total_outstanding_receivables: string;
  period_credit_sales: string;
  days_sales_outstanding: number;
  ageing_summary: {
    current_0_30: AgeingBucket;
    overdue_31_60: AgeingBucket;
    overdue_61_90: AgeingBucket;
    overdue_91_plus: AgeingBucket;
  };
}

export interface SalesAnalyticsResponse {
  company: {
    id: number;
    name: string;
    abbr: string;
  };
  period: {
    code: string;
    from_date: string;
    to_date: string;
  };
  summary: KPISummary;
  conversion_and_velocity: ConversionAndVelocity;
  revenue_and_volume: RevenueAndVolume;
  top_products: TopProductsData;
  customer_analytics: CustomerAnalyticsData;
  receivables_and_dso: ReceivablesAndDSO;
}

export type PeriodFilterOption =
  | "this_month"
  | "last_month"
  | "this_quarter"
  | "last_quarter"
  | "this_fy"
  | "last_fy"
  | "all_time"
  | "custom";
