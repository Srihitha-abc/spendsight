import type { UploadInfo } from "./api";

/** The file being onboarded, handed from Data Input to Column Mapping. Lost on reload by design (re-upload). */
export const flow: { upload: UploadInfo | null } = { upload: null };
