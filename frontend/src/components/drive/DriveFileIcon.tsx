import React from "react";
import { File, FileSpreadsheet, FileText, Image as ImageIcon, Presentation } from "lucide-react";
import { fileKind } from "./fileUtils";

interface Props {
  contentType: string;
  className?: string;
}

export const DriveFileIcon: React.FC<Props> = ({ contentType, className = "w-5 h-5" }) => {
  const kind = fileKind(contentType);
  if (kind === "image") return <ImageIcon className={className} />;
  if (kind === "pdf") return <FileText className={className} />;
  if (kind === "word" || kind === "text") return <FileText className={className} />;
  if (kind === "sheet") return <FileSpreadsheet className={className} />;
  if (kind === "slides") return <Presentation className={className} />;
  return <File className={className} />;
};
