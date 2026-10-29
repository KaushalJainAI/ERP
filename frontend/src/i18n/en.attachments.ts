/**
 * English strings for the Attachments (proof documents) panel.
 * Merged into the en bundle at the top level (see i18n.ts) under `att`.
 * `att.err.<code>` mirrors the stable `code` the API returns on a refusal.
 */
export const enAttachments = {
  att: {
    title: "Attachments",
    add_file: "Add file",
    take_photo: "Take photo",
    drop_here: "Drop files here, or use the buttons",
    hint: "PDF, JPEG, PNG, WebP, HEIC, XLSX or CSV, up to {{mb}} MB each.",
    save_first: "Save this record first, then you can attach proof documents to it.",
    empty: "No attachments yet.",
    uploading: "Uploading {{name}}...",
    uploaded: "{{name}} attached.",
    deleted: "{{name}} deleted.",
    uploaded_by: "{{name}} on {{date}}",
    view: "View",
    download: "Download",
    delete: "Delete",
    delete_title: "Delete attachment?",
    delete_message: "{{name}} will be permanently removed from this record.",
    locked_note: "Proof on a submitted or cancelled document is permanent and cannot be deleted.",
    load_failed: "Attachments could not be loaded.",
    err: {
      generic: "The attachment could not be saved.",
      attachment_forbidden: "You do not have permission to do that.",
      attachment_delete_forbidden: "Only the uploader or a manager can delete this attachment.",
      attachment_locked: "Proof attached to a submitted or cancelled document cannot be deleted.",
      attachment_not_found: "Attachment not found.",
      attachment_parent_invalid: "Files cannot be attached to this record.",
      attachment_type_not_allowed: "This file type is not allowed. Use PDF, JPEG, PNG, WebP, HEIC, XLSX or CSV.",
      attachment_content_mismatch: "The file content does not match its type, so it was rejected.",
      attachment_empty: "The file is empty.",
      attachment_missing: "No file was uploaded.",
      attachment_too_large: "The file is too large. The maximum size is {{mb}} MB.",
      attachment_duplicate: "This file is already attached to this record.",
      attachment_file_missing: "The stored file is missing.",
    },
  },
};
