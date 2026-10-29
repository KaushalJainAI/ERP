import { apiClient } from "@/api/client";
import type { DriveFile } from "@/types/organisation";

export const DRIVE_FILES = "/drive/files/";

export const formatBytes = (n: number): string => {
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  if (n >= 1024) return `${Math.round(n / 1024)} KB`;
  return `${n} B`;
};

export type DriveKind = "image" | "pdf" | "word" | "sheet" | "slides" | "text" | "file";

export const fileKind = (contentType: string): DriveKind => {
  if (contentType.startsWith("image/")) return "image";
  if (contentType === "application/pdf") return "pdf";
  if (contentType.includes("wordprocessingml")) return "word";
  if (contentType.includes("spreadsheetml") || contentType === "text/csv") return "sheet";
  if (contentType.includes("presentationml")) return "slides";
  if (contentType === "text/plain") return "text";
  return "file";
};

/** Fetch the bytes with the auth header, then open a tab or save the blob. */
export async function openDriveFile(file: DriveFile, inline = false): Promise<void> {
  const tab = inline ? window.open("", "_blank") : null;
  try {
    const res = await apiClient.get(`${DRIVE_FILES}${file.id}/download/`, {
      params: inline ? { inline: 1 } : undefined,
      responseType: "blob",
    });
    const url = window.URL.createObjectURL(res.data as Blob);
    if (inline && tab) {
      tab.location.href = url;
    } else {
      const a = document.createElement("a");
      a.href = url;
      a.download = file.original_filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
    }
    window.setTimeout(() => window.URL.revokeObjectURL(url), 60_000);
  } catch (err) {
    tab?.close();
    throw err;
  }
}
