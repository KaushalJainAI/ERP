/** Company profile and organisation-drive API shapes. */

export interface CompanyProfile {
  id: number;
  name: string;
  legal_name: string;
  abbr: string;
  default_currency: string;
  country: string;
  gstin: string | null;
  is_active: boolean;
  is_default: boolean;
  can_write: boolean;
  email: string;
  phone_no: string;
  website: string;
  address_line1: string;
  address_line2: string;
  city: string;
  pincode: string;
  pan: string;
  cin: string;
  date_of_incorporation: string | null;
  state: string | null;
  state_name: string | null;
  gst_category: string;
  hsn_min_digits: number;
  enable_perpetual_inventory: boolean;
  default_inventory_account: number | null;
  stock_adjustment_account: number | null;
  default_expense_account: number | null;
  default_receivable_account?: number | null;
  default_payable_account?: number | null;
  default_income_account?: number | null;
  default_selling_price_list: number | null;
  default_buying_price_list: number | null;
  unprocessed_warehouse?: number | null;
  in_process_warehouse?: number | null;
  ready_warehouse?: number | null;
  enable_rounded_total: boolean;
  round_off_account: number | null;
  round_off_gst_values: boolean;
}

export interface DrivePathItem {
  id: number;
  name: string;
}

export interface DriveFolder {
  id: number;
  name: string;
  parent: number | null;
  is_system: boolean;
  system_key: string;
  file_count: number;
  folder_count: number;
  can_edit: boolean;
  created_by: number | null;
  created_by_name: string;
  created_at: string;
  modified_at: string;
  path: DrivePathItem[];
}

export interface DriveShare {
  id: number;
  user: number;
  name: string;
  email: string;
  can_write: boolean;
  created_at: string;
}

export interface DriveFile {
  id: number;
  folder: number | null;
  folder_name: string;
  name: string;
  original_filename: string;
  content_type: string;
  size: number;
  sha256: string;
  description: string;
  uploaded_by: number;
  uploaded_by_name: string;
  created_at: string;
  modified_at: string;
  visibility: "company" | "private";
  is_owner: boolean;
  can_edit: boolean;
  can_delete: boolean;
  previewable: boolean;
  shared_with: DriveShare[];
  shared_with_me: boolean;
}

export interface DriveMember {
  id: number;
  name: string;
  email: string;
  is_self: boolean;
}

export interface DriveMembersResponse {
  results: DriveMember[];
  allowed_extensions: string[];
  max_bytes: number;
}

export interface CompanyDocumentsResponse {
  folder: DriveFolder;
  files: DriveFile[];
  allowed_extensions: string[];
  max_bytes: number;
}
