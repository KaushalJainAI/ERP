import { useQuery } from "@tanstack/react-query";
import { listAllRecords } from "@/api/endpoints";
import { useCompany } from "@/context/CompanyContext";

export const QUICK_NOTES_ENDPOINT = "/assistant/quick-notes/";

/** A saved note. It writes nothing to the books; it is a job until marked done. */
export interface QuickNote {
  id: number;
  text: string;
  has_attachment: boolean;
  status: "open" | "done";
  created_at: string;
}

/** Every Open note of the active company, newest first. Shared by Home and the notes page. */
export function useOpenQuickNotes(enabled: boolean) {
  const { activeCompany } = useCompany();
  const companyId = activeCompany?.id ?? null;
  return useQuery({
    queryKey: ["quick-notes", "open", companyId],
    queryFn: async () => {
      const rows = await listAllRecords(QUICK_NOTES_ENDPOINT);
      return (rows as unknown as QuickNote[])
        .filter((note) => note.status === "open")
        .sort((a, b) => b.created_at.localeCompare(a.created_at));
    },
    enabled: enabled && !!activeCompany,
    staleTime: 30_000,
    retry: false,
  });
}
